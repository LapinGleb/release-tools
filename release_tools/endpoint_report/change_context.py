import ast
import difflib
from collections import Counter

from release_tools import schemas
from release_tools.common.protocols import Source


class ChangeContextError(RuntimeError):
    """A bounded endpoint context cannot be produced."""


class ChangeContextBuilder:
    def __init__(self, source: Source, max_context_chars: int = 60_000) -> None:
        self.source = source
        self.max_context_chars = max_context_chars

    def build(
        self,
        base: str,
        head: str,
        base_routes: dict[schemas.RouteKey, schemas.RouteDefinition],
        head_routes: dict[schemas.RouteKey, schemas.RouteDefinition],
    ) -> tuple[schemas.ChangeCandidate, ...]:
        base_symbols = self._index_revision(base)
        head_symbols = self._index_revision(head)
        common_keys = sorted(base_routes.keys() & head_routes.keys())
        candidates: list[schemas.ChangeCandidate] = []
        for key in common_keys:
            reachable = self._reachable_symbols(
                base_routes[key],
                head_routes[key],
                base_symbols,
                head_symbols,
            )
            sections: list[str] = []
            changed_names: list[str] = []
            if base_routes[key] != head_routes[key]:
                sections.append(
                    _unified(
                        base_routes[key].source,
                        head_routes[key].source,
                        f"{base_routes[key].module}:{base_routes[key].function}",
                    ),
                )
                changed_names.append(f"{head_routes[key].module}:{head_routes[key].function}")
            for name in sorted(reachable):
                old = base_symbols.get(name)
                new = head_symbols.get(name)
                if old and new and old.source == new.source:
                    continue
                if old is None and new is None:
                    continue
                sections.append(_unified(old.source if old else "", new.source if new else "", name))
                changed_names.append(name)
            context = "\n".join(section for section in sections if section.strip())
            if not context:
                continue
            if len(context) > self.max_context_chars:
                message = f"Context for {key.method} {key.path} exceeds {self.max_context_chars} characters"
                raise ChangeContextError(message)
            candidates.append(schemas.ChangeCandidate(key=key, diff=context, symbols=tuple(changed_names)))
        return tuple(candidates)

    def _index_revision(self, revision: str) -> dict[str, schemas.endpoint_report.SymbolDefinition]:
        symbols: dict[str, schemas.endpoint_report.SymbolDefinition] = {}
        paths = tuple(path for path in self.source.list_python_files(revision) if _is_relevant_path(path))
        texts = self.source.read_files(revision, paths)
        trees = {path: ast.parse(text, filename=path) for path, text in texts.items()}
        class_name_counts = Counter(
            node.name for tree in trees.values() for node in tree.body if isinstance(node, ast.ClassDef)
        )
        known_classes = {class_name for class_name, count in class_name_counts.items() if count == 1}
        provider_returns = _provider_return_types(trees)
        for path, tree in trees.items():
            text = texts[path]
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                bindings = _constructor_bindings(node, provider_returns, known_classes)
                class_name = f"{path}:{node.name}"
                symbols[class_name] = _symbol(path, node.name, node, text, bindings)
                for child in node.body:
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                        qualified_name = f"{path}:{node.name}.{child.name}"
                        symbols[qualified_name] = _symbol(
                            path,
                            f"{node.name}.{child.name}",
                            child,
                            text,
                            bindings,
                        )
        return symbols

    def _reachable_symbols(
        self,
        base_route: schemas.RouteDefinition,
        head_route: schemas.RouteDefinition,
        base_symbols: dict[str, schemas.endpoint_report.SymbolDefinition],
        head_symbols: dict[str, schemas.endpoint_report.SymbolDefinition],
    ) -> set[str]:
        return self._reachable_in_revision(base_route, base_symbols) | self._reachable_in_revision(
            head_route,
            head_symbols,
        )

    def _reachable_in_revision(
        self,
        route: schemas.RouteDefinition,
        symbols: dict[str, schemas.endpoint_report.SymbolDefinition],
    ) -> set[str]:
        wanted_classes = {
            *route.request_models,
            *([route.response_model] if route.response_model else []),
        }
        reachable = {
            name
            for name, symbol in symbols.items()
            if "." not in symbol.qualified_name and symbol.qualified_name in wanted_classes
        }
        frontier = set(route.direct_calls)
        visited_calls: set[str] = set()
        while frontier:
            call = frontier.pop()
            if call in visited_calls:
                continue
            visited_calls.add(call)
            class_name, _, method_name = call.rpartition(".")
            matches = [
                (name, symbol)
                for name, symbol in symbols.items()
                if symbol.qualified_name == f"{class_name}.{method_name}"
            ]
            for name, symbol in matches:
                if name in reachable:
                    continue
                reachable.add(name)
                for attribute, called_method in symbol.calls:
                    target = _resolve_target(symbol, attribute, called_method, symbols)
                    if target:
                        frontier.add(target.qualified_name)
        return reachable


def _annotation_name(annotation: ast.expr | None) -> str | None:
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value
    return None


def _provider_return_types(trees: dict[str, ast.Module]) -> dict[str, str]:
    candidates: dict[str, list[str | None]] = {}
    for tree in trees.values():
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for child in node.body:
                if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                return_type = _annotation_name(child.returns)
                candidates.setdefault(f"{node.name}.{child.name}", []).append(return_type)
    return {
        method: return_types[0]
        for method, return_types in candidates.items()
        if len(return_types) == 1 and return_types[0] is not None
    }


def _self_attribute(target: ast.expr) -> str | None:
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
        return target.attr
    return None


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        return f"{call.func.value.id}.{call.func.attr}"
    return None


def _initializer_assignments(initializer: ast.FunctionDef) -> tuple[ast.Assign, ...]:
    assignments: list[ast.Assign] = []

    class AssignmentVisitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            assignments.append(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
            return

    visitor = AssignmentVisitor()
    for statement in initializer.body:
        visitor.visit(statement)
    return tuple(assignments)


def _constructor_bindings(
    node: ast.ClassDef,
    provider_returns: dict[str, str],
    known_classes: set[str],
) -> tuple[tuple[str, str], ...]:
    initializer = next(
        (child for child in node.body if isinstance(child, ast.FunctionDef) and child.name == "__init__"),
        None,
    )
    if initializer is None:
        return ()

    parameters: dict[str, str] = {}
    for argument in (*initializer.args.args, *initializer.args.kwonlyargs):
        parameter_type = _annotation_name(argument.annotation)
        if parameter_type:
            parameters[argument.arg] = parameter_type
    candidates: dict[str, list[str | None]] = {}
    for assignment in _initializer_assignments(initializer):
        if len(assignment.targets) != 1:
            continue
        attribute = _self_attribute(assignment.targets[0])
        if attribute is None or attribute.rstrip("_").endswith("client"):
            continue

        dependency: str | None = None
        if isinstance(assignment.value, ast.Name):
            dependency = parameters.get(assignment.value.id)
        elif isinstance(assignment.value, ast.Call):
            call_name = _call_name(assignment.value)
            if call_name and call_name.split(".")[-1].startswith("get_") and call_name.endswith("_client"):
                candidates.setdefault(attribute, []).append(None)
                continue
            if call_name and call_name in provider_returns:
                dependency = provider_returns[call_name]
            elif call_name in known_classes:
                dependency = call_name
        candidates.setdefault(attribute, []).append(dependency if dependency in known_classes else None)

    bindings = {
        attribute: dependencies[0]
        for attribute, dependencies in candidates.items()
        if len(set(dependencies)) == 1 and dependencies[0] is not None
    }
    return tuple(sorted(bindings.items()))


def _symbol(
    path: str,
    qualified_name: str,
    node: ast.AST,
    text: str,
    bindings: tuple[tuple[str, str], ...],
) -> schemas.endpoint_report.SymbolDefinition:
    calls: set[tuple[str, str]] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        owner = child.func.value
        if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name) and owner.value.id == "self":
            calls.add((owner.attr, child.func.attr))
    return schemas.endpoint_report.SymbolDefinition(
        module=path,
        qualified_name=qualified_name,
        source=ast.get_source_segment(text, node) or "",
        calls=tuple(sorted(calls)),
        bindings=bindings,
    )


def _resolve_target(
    current: schemas.endpoint_report.SymbolDefinition,
    attribute: str,
    method: str,
    symbols: dict[str, schemas.endpoint_report.SymbolDefinition],
) -> schemas.endpoint_report.SymbolDefinition | None:
    target_class = dict(current.bindings).get(attribute)
    if target_class is None:
        return None
    matches = [
        symbol
        for symbol in symbols.values()
        if symbol.qualified_name == f"{target_class}.{method}" and _is_dependency_module(symbol.module)
    ]
    return matches[0] if len(matches) == 1 else None


def _is_relevant_path(path: str) -> bool:
    return path.startswith(("src/schemas/", "src/modules/", "src/repositories/")) and "/tests/" not in path


def _is_dependency_module(path: str) -> bool:
    return path.startswith(("src/modules/", "src/repositories/"))


def _unified(old: str, new: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{label} (base)",
            tofile=f"{label} (head)",
            n=20,
        ),
    )
