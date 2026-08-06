# FO Release Tools

FO Release Tools automates the repetitive work involved in coordinating releases across EVA and GitLab. It discovers task branches, prepares release branches and merge requests, and reports FastAPI endpoint changes across multiple projects.

By replacing manual repository checks with a consistent workflow, it saves release preparation time, reduces human error, and makes cross-project changes easier to review.

## Requirements

- Python `3.12` or newer
- `uv`
- Access to EVA and GitLab
- Tokens provided through environment variables or a `.env` file:

```dotenv
EVA_TOKEN=...
GITLAB_TOKEN=...
LLM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL=...
```

## Installation

Project dependencies are defined in `pyproject.toml` and locked in `uv.lock`.
Install them with:

```bash
uv sync
```

The following CLI commands are available:

```bash
uv run create-release-branches --help
uv run create-merge-request-links --help
uv run create-release-merge-request-links --help
uv run create-endpoint-report --help
```

## `create-release-branches`

Creates a GitLab release branch in repositories that contain task branches associated with an EVA release.
The command looks for branches matching `feature/<TASK>`, `bugfix/<TASK>`, and `hotfix/<TASK>`,
then creates `release/<release>` from the base branch. It runs in dry-run mode by default and does not make any changes.

Dry run without creating branches:

```bash
uv run create-release-branches --eva-release REL-005056 --release 20260604
```

Create the branches:

```bash
uv run create-release-branches --eva-release REL-005056 --release 20260604 --apply
```

Useful options:

```bash
uv run create-release-branches \
  --eva-release REL-005056 \
  --release 20260604 \
  --group fo \
  --base master \
  --prefix feature \
  --prefix bugfix \
  --prefix hotfix
```

Behavior:

- `--release 20260604` becomes the `release/20260604` branch.
- Without `--apply`, the command only shows where it would create branches.
- If a release branch already exists in a project, that project is skipped with a `SKIP` status.
- If the `master` base branch is not found, the command reports an error for that project and continues processing the remaining projects.

## `create-merge-request-links`

Asynchronously searches GitLab projects for task branches associated with an EVA release and prints links to the merge request creation form, targeting the release branch. This command is always a dry run: it does not create merge requests.

Create links for all tasks in a release:

```bash
uv run create-merge-request-links --eva-release REL-005056 --release 20260604
```

Filter EVA tasks by the assignee's full name:

```bash
uv run create-merge-request-links \
  --eva-release REL-005056 \
  --release 20260604 \
  --assignee "John Smith"
```

Useful options:

```bash
uv run create-merge-request-links \
  --eva-release REL-005056 \
  --release 20260604 \
  --group fo \
  --assignee "John Smith" \
  --prefix feature \
  --prefix bugfix \
  --prefix hotfix
```

Behavior:

- `--release 20260604` becomes the `release/20260604` target branch.
- `--assignee` may be specified multiple times; when omitted, all tasks in the release are processed.
- Task branches use the `feature`, `bugfix`, and `hotfix` prefixes unless custom `--prefix` values are provided.
- The command only prints links to the MR creation form and does not call the GitLab API to create an MR.

## `create-release-merge-request-links`

Prints merge request links from a release branch to `master` for every project where the release branch exists. It runs in dry-run mode by default and does not create merge requests.

Dry run that only prints links to the MR creation form:

```bash
uv run create-release-merge-request-links --release 20260604
```

Create merge requests:

```bash
uv run create-release-merge-request-links --release 20260604 --apply
```

Useful options:

```bash
uv run create-release-merge-request-links \
  --release 20260604 \
  --group fo \
  --target master \
  --apply
```

Behavior:

- `--release 20260604` becomes the `release/20260604` source branch.
- The default target branch is `master`; use `--target` to change it.
- Only projects containing the release branch are included.
- If an open MR from the release branch to the target branch already exists, the command prints `SKIP` and does not create a duplicate.
- Without `--apply`, the command only prints links to the MR creation form.

## `create-endpoint-report`

Compares FastAPI endpoints in selected GitLab projects between `base` and `head` using the merge base (`base...head`). The command downloads archives for both revisions, discovers FastAPI routes, and atomically writes a TXT report.

Run it for one project:

```bash
uv run create-endpoint-report \
  --head release/20260733 \
  --base master \
  --project crm \
  --output endpoint-changes.txt
```

Specify `--project` multiple times to process multiple projects.

Pass branch names, tags, or commit SHAs in full; the command does not add the `release/` prefix.

Options:

| Option | Description |
| --- | --- |
| `--head` | Required branch, tag, or commit SHA to analyze |
| `--base` | Required base branch, tag, or commit SHA |
| `--project` | Required project path or `path_with_namespace`; may be specified multiple times |
| `--group` | GitLab group; defaults to `GITLAB_GROUP` or `fo` |
| `--output` | Output TXT file; defaults to `endpoint-changes.txt` |
| `--concurrency` | Number of projects processed concurrently; defaults to `3` |
| `--max-context-chars` | Maximum context size for one endpoint; defaults to `60000` |
| `--verbose` | Print a traceback if the command fails |

## SSL

If your environment uses a corporate self-signed certificate and a command fails with `CERTIFICATE_VERIFY_FAILED`, you can disable certificate verification:

```bash
HTTP_VERIFY_SSL=false uv run create-release-branches --eva-release REL-005056 --release 20260604
HTTP_VERIFY_SSL=false uv run create-merge-request-links --eva-release REL-005056 --release 20260604
HTTP_VERIFY_SSL=false uv run create-release-merge-request-links --release 20260604
HTTP_VERIFY_SSL=false uv run create-endpoint-report --head release/20260733 --base master --project crm
```

## Development Commands

Install dependencies:

```bash
uv sync
```

Run the linter:

```bash
uv run pre-commit run -a
```

Run tests:

```bash
uv run pytest
```

Build the source distribution and wheel:

```bash
uv build --clear
```

## Project Structure

- `release_tools/` — main package.
- `release_tools/commands/` — executable CLI commands.
- `release_tools/clients/` — external API clients.
- `release_tools/common/` — shared runtime, HTTP/SSL helpers, and utilities.
- `release_tools/endpoint_report/` — FastAPI endpoint comparison and change reporting.
- `release_tools/release_branches/` — release branch creation logic.
- `release_tools/task_merge_requests/` — task MR link generation logic.
- `tests/` — unit tests that do not make real network requests.
