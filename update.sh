#!/bin/sh
# Update an existing local model-router skill installation.
set -eu

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Erro: Python 3.11+ é necessário e deve estar disponível como python3.' >&2
    exit 1
fi

exec python3 - "$0" "$@" <<'PY'
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile

if sys.version_info < (3, 11):
    print("Erro: Python 3.11+ é necessário para atualizar model-router.", file=sys.stderr)
    raise SystemExit(1)

parser = argparse.ArgumentParser(
    description="Atualiza uma instalação existente da skill model-router.",
    epilog="Exemplo: sh update.sh --dest '/caminho com espaços/model-router'",
)
parser.add_argument(
    "--dest", metavar="PATH", type=Path,
    help="Instalação existente (padrão: ~/.agents/skills/model-router).",
)
args = parser.parse_args(sys.argv[2:])

backup = None
destination = None
try:
    source = (Path(sys.argv[1]).resolve().parent / "model-router").resolve()
    destination = args.dest.expanduser() if args.dest is not None else Path.home() / ".agents/skills/model-router"
    destination = Path(os.path.abspath(destination))
    required = (
        "SKILL.md", "routing.toml", "scripts/router.py", "scripts/telemetry.py",
        "scripts/telemetry_core.py", "scripts/telemetry_records.py", "scripts/router_telemetry.py",
        "scripts/telemetry_settings.py", "references/integration.md", "agents/openai.yaml",
    )
    for relative in required:
        if not (source / relative).is_file():
            raise ValueError(f"Pacote incompleto: arquivo ausente {source / relative}")
    if not os.path.lexists(destination) or not destination.is_dir():
        raise ValueError(f"Instalação inexistente ou inválida: {destination}")
    resolved_destination = destination.resolve()
    if resolved_destination == source or source in resolved_destination.parents:
        raise ValueError("Destino deve ficar fora da pasta-fonte model-router.")

    # Stage beside the destination so final renames stay on the same filesystem.
    with tempfile.TemporaryDirectory(prefix=".model-router-update-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        staged = temporary / "staged"
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))

        # Keep site-specific routing policy from the installed copy.
        installed_routing = destination / "routing.toml"
        if installed_routing.is_file():
            shutil.copy2(installed_routing, staged / "routing.toml")

        # Rename old installation aside, then put staged copy in place. Restore on failure.
        backup = temporary / "backup"
        destination.rename(backup)
        try:
            staged.rename(destination)
        except BaseException:
            backup.rename(destination)
            backup = None
            raise
        shutil.rmtree(backup)
        backup = None
    print(f"Skill atualizada em: {destination}")
except (OSError, ValueError) as exc:
    if backup is not None and destination is not None and not os.path.lexists(destination):
        try:
            backup.rename(destination)
        except OSError as restore_error:
            print(f"Erro ao restaurar backup {backup}: {restore_error}", file=sys.stderr)
    print(f"Erro na atualização: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
