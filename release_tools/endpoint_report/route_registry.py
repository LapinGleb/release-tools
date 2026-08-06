import ast
from pathlib import PurePosixPath

from release_tools.common.protocols import Source
from release_tools.schemas import (
    ReviewFinding,
    RouteDefinition,
    RouteKey,
)
from release_tools.schemas.endpoint_report import (
    HTTP_METHODS,
    ParsedModule,
    ParsedRoute,
    RouteRegistryResult,
    RouterInclude,
    RouteSymbol,
    normalize_path,
)


class RouteRegistryError(RuntimeError):
    """FastAPI route graph cannot be resolved statically."""


class RouteRegistry:
    @classmethod
    def build(cls, source: Source, revision: str) -> RouteRegistryResult:
        paths = tuple(
            path
            for path in source.list_python_files(revision)
            if path.startswith("src/entrypoints/api/") or path == "src/entrypoints/api/__init__.py"
        )
        modules = {path: cls._parse_module(path, source.read_file(revision, path)) for path in paths}
        root = RouteSymbol(module="src/entrypoints/api/__init__.py", name="api_router")
        definitions: dict[RouteKey, RouteDefinition] = {}
        review_findings = [finding for module in modules.values() for finding in module.review_findings]
        cls._walk(modules, root, "", definitions, set(), review_findings, recoverable=False)
        unique_findings = {
            (
                finding.key.method if finding.key else "",
                finding.key.path if finding.key else "",
                finding.reason,
            ): finding
            for finding in review_findings
        }
        return RouteRegistryResult(
            definitions=definitions,
            review_findings=tuple(unique_findings[key] for key in sorted(unique_findings)),
        )

    @classmethod
    def _parse_module(cls, path: str, source: str) -> ParsedModule:
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as error:
            message = f"Cannot parse {path}: {error}"
            raise RouteRegistryError(message) from error
        module = ParsedModule(path=path, tree=tree, source=source)
        cls._index_imports(module)
        cls._index_routers(module)
        return module

    @staticmethod
    def _index_imports(module: ParsedModule) -> None:
        for node in module.tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            imported_module = _resolve_import(module.path, node.module, node.level)
            for alias in node.names:
                module.imports[alias.asname or alias.name] = RouteSymbol(
                    module=imported_module,
                    name=alias.name,
                )

    @classmethod
    def _index_routers(cls, module: ParsedModule) -> None:
        for node in module.tree.body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and _call_name(node.value) == "APIRouter"
            ):
                prefix = cls._literal_keyword(node.value, "prefix", default="")
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        module.routers[target.id] = prefix
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                cls._index_include(module, node.value)
            elif isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                cls._index_route(module, node)

    @classmethod
    def _index_include(cls, module: ParsedModule, call: ast.Call) -> None:
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "include_router":
            return
        if not isinstance(call.func.value, ast.Name) or not call.args or not isinstance(call.args[0], ast.Name):
            return
        owner = call.func.value.id
        included_name = call.args[0].id
        prefix_node = _keyword_value(call, "prefix")
        if prefix_node is None:
            prefix = ""
        elif isinstance(prefix_node, ast.Constant) and isinstance(prefix_node.value, str):
            prefix = prefix_node.value
        else:
            module.review_findings.append(
                ReviewFinding(key=None, reason=f"Router prefix must be a literal in {module.path}:{call.lineno}"),
            )
            return
        included = module.imports.get(
            included_name,
            RouteSymbol(module=module.path, name=included_name),
        )
        module.includes.setdefault(owner, []).append(RouterInclude(symbol=included, prefix=prefix))

    @classmethod
    def _index_route(cls, module: ParsedModule, function: ast.AsyncFunctionDef | ast.FunctionDef) -> None:
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.upper()
            if method not in HTTP_METHODS or not isinstance(decorator.func.value, ast.Name):
                continue
            path_node = decorator.args[0] if decorator.args else _keyword_value(decorator, "path")
            if path_node is None:
                path = ""
            elif isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
                path = path_node.value
            else:
                module.review_findings.append(
                    ReviewFinding(
                        key=None,
                        reason=f"Route path must be a literal in {module.path}:{decorator.lineno}",
                    ),
                )
                continue
            response_node = _keyword_value(decorator, "response_model")
            module.routes.append(
                ParsedRoute(
                    router=decorator.func.value.id,
                    method=method,
                    path=path,
                    function=function,
                    response_model=_annotation_name(response_node),
                ),
            )

    @staticmethod
    def _literal_keyword(call: ast.Call, name: str, default: str) -> str:
        value = _keyword_value(call, name)
        if value is None:
            return default
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        message = f"Router {name} must be a literal"
        raise RouteRegistryError(message)

    @classmethod
    def _walk(
        cls,
        modules: dict[str, ParsedModule],
        symbol: RouteSymbol,
        parent_prefix: str,
        definitions: dict[RouteKey, RouteDefinition],
        stack: set[RouteSymbol],
        review_findings: list[ReviewFinding],
        recoverable: bool,
    ) -> None:
        if symbol.module not in modules and symbol.module.endswith(".py"):
            package_module = f"{symbol.module[:-3]}/__init__.py"
            if package_module in modules:
                symbol = RouteSymbol(module=package_module, name=symbol.name)
        if symbol in stack:
            message = f"Router include cycle at {symbol.module}:{symbol.name}"
            raise RouteRegistryError(message)
        module = modules.get(symbol.module)
        if module is None or symbol.name not in module.routers:
            message = f"Cannot resolve router {symbol.module}:{symbol.name}"
            if recoverable:
                review_findings.append(ReviewFinding(key=None, reason=message))
                return
            raise RouteRegistryError(message)
        prefix = _join_paths(parent_prefix, module.routers[symbol.name])
        next_stack = {*stack, symbol}
        for route in module.routes:
            if route.router != symbol.name:
                continue
            key = RouteKey(method=route.method, path=_join_paths(prefix, route.path))
            if key in definitions:
                message = f"Duplicate route {key.method} {key.path}"
                raise RouteRegistryError(message)
            request_models, dependencies = _function_parameters(route.function)
            definitions[key] = RouteDefinition(
                key=key,
                module=module.path,
                function=route.function.name,
                request_models=request_models,
                response_model=route.response_model,
                direct_calls=_direct_calls(route.function, dependencies),
                source=ast.get_source_segment(module.source, route.function) or "",
            )
        for included in module.includes.get(symbol.name, []):
            include_prefix = _join_paths(prefix, included.prefix)
            cls._walk(
                modules,
                included.symbol,
                include_prefix,
                definitions,
                next_stack,
                review_findings,
                recoverable=True,
            )


def _resolve_import(current_path: str, module_name: str | None, level: int) -> str:
    if not level:
        return f"{module_name.replace('.', '/')}.py" if module_name else current_path
    current = PurePosixPath(current_path)
    package = current.parent
    parts = list(package.parts)
    parts = parts[: len(parts) - level + 1]
    if module_name:
        parts.extend(module_name.split("."))
    candidate = PurePosixPath(*parts)
    module_file = f"{candidate}.py"
    package_file = str(candidate / "__init__.py")
    return package_file if current_path.endswith("__init__.py") and not module_name else module_file


def _join_paths(left: str, right: str) -> str:
    return normalize_path(f"{left}/{right}")


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _annotation_name(annotation: ast.AST | None) -> str | None:  # noqa: PLR0911
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        if isinstance(annotation.slice, ast.Tuple) and annotation.slice.elts:
            return _annotation_name(annotation.slice.elts[0])
        return _annotation_name(annotation.slice)
    if isinstance(annotation, ast.BinOp):
        return _annotation_name(annotation.left)
    return None


def _function_parameters(
    function: ast.AsyncFunctionDef | ast.FunctionDef,
) -> tuple[tuple[str, ...], dict[str, str]]:
    arguments = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
    positional_defaults: list[ast.expr | None] = [None] * (
        len(function.args.posonlyargs) + len(function.args.args) - len(function.args.defaults)
    )
    positional_defaults.extend(function.args.defaults)
    defaults = [*positional_defaults, *function.args.kw_defaults]
    request_models: set[str] = set()
    dependencies: dict[str, str] = {}
    for argument, default in zip(arguments, defaults, strict=True):
        annotation = _annotation_name(argument.annotation)
        if not annotation:
            continue
        if isinstance(default, ast.Call) and _call_name(default) == "Depends":
            dependencies[argument.arg] = annotation
        elif annotation not in {"str", "int", "float", "bool", "bytes", "UploadFile", "Response", "Request"}:
            request_models.add(annotation)
    return tuple(sorted(request_models)), dependencies


def _direct_calls(function: ast.AsyncFunctionDef | ast.FunctionDef, dependencies: dict[str, str]) -> tuple[str, ...]:
    calls: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if isinstance(node.func.value, ast.Name) and node.func.value.id in dependencies:
            calls.add(f"{dependencies[node.func.value.id]}.{node.func.attr}")
    return tuple(sorted(calls))
