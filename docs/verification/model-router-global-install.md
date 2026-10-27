# Global model-router installation verification

- Status: successful.
- Source: `<checkout>/model-router`.
- Installed destination: `<CODEX_HOME>/..agents/skills/model-router` (default: `~/.agents/skills/model-router`).
- Installation: copied from source with `shutil.copytree`; destination did not exist before installation. No `.codex/skills` duplicate created.
- Product comparison: 5 files; relative paths and SHA-256 hashes match source exactly, excluding `__pycache__` and `*.pyc`.
- `routing.toml`: parsed successfully with Python `tomllib`.
- `SKILL.md`: YAML frontmatter delimiters present.
- `CODEX_HOME`: unset; defaulted to `~/.codex`.
- Active global instructions: `~/.codex/AGENTS.md`. `AGENTS.override.md` absent or empty, so `AGENTS.md` is active.
- Backup: `~/.codex/AGENTS.md.model-router-backup-<timestamp>`.
- Activation edit: appended one block identified by `<!-- MODEL_ROUTER_GLOBAL -->` and `<!-- /MODEL_ROUTER_GLOBAL -->`; exactly one start and end marker found.
- Preservation: original AGENTS bytes remain an exact prefix; file mode remains `0o600`.

## Smoke route

Installed CLI invoked in `route` mode with temporary JSON task: `task_type=operations`, `complexity=high`.

- Exit code: `0`.
- Resolved profile: `economy`.
- Resolved model: `gpt-6-luna`.
- Resolved reasoning effort: `low`.
- Route result reason: operations requires economical profile with low effort.
- The `route` command resolves policy only; it does not start a model.

## Limits

A new session is needed for application discovery. Discovery in the actual app was not tested. No application config, plugin, or running service was changed.
