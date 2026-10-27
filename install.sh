#!/bin/sh
# Install the local skill without overwriting an existing destination.
set -eu

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Erro: instale Python 3.11+ e disponibilize python3 no PATH.' >&2
    exit 1
fi

exec python3 - "$0" "$@" <<'PY'
import sys

if sys.version_info < (3, 11):
    print("Erro: Python 3.11+ é necessário para instalar e usar model-router.", file=sys.stderr)
    raise SystemExit(1)

import argparse
import os
from pathlib import Path
import shutil
import tempfile

parser = argparse.ArgumentParser(
    description="Instala a skill local model-router sem sobrescrever arquivos existentes.",
    epilog="Exemplos: sh install.sh | sh install.sh --dest '/caminho com espaços/model-router'",
)
parser.add_argument(
    "--dest", metavar="PATH", type=Path,
    help="Caminho final da skill (padrão: ~/.agents/skills/model-router). Deve não existir.",
)
args = parser.parse_args(sys.argv[2:])

try:
    source = (Path(sys.argv[1]).resolve().parent / "model-router").resolve()
    destination = args.dest.expanduser() if args.dest is not None else Path.home() / ".agents/skills/model-router"
    destination = Path(os.path.abspath(destination))
    required = ("SKILL.md", "routing.toml", "scripts/router.py", "scripts/telemetry.py",
                "scripts/telemetry_core.py", "scripts/telemetry_records.py", "scripts/router_telemetry.py",
                "scripts/telemetry_settings.py",
                "references/integration.md", "agents/openai.yaml")
    for relative in required:
        if not (source / relative).is_file():
            raise ValueError(f"Pacote incompleto: arquivo ausente {source / relative}")
    if os.path.lexists(destination):
        raise ValueError(
            f"Destino já existe: {destination}. Nenhum arquivo foi substituído. "
            "Escolha outro --dest ou faça backup e mova a instalação existente manualmente."
        )
    resolved_destination = destination.resolve()
    if resolved_destination == source or source in resolved_destination.parents:
        raise ValueError("Destino deve ficar fora da pasta-fonte model-router.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".model-router-install-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "model-router"
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        # Exclusive creation also refuses a destination created while staging, including symlinks.
        destination.mkdir()
        try:
            for child in staged.iterdir():
                child.rename(destination / child.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
    print(f"Skill instalada em: {destination}")
    print("Abra uma nova sessão e invoque $model-router. Consulte references/integration.md para ativação recorrente.")
except (OSError, ValueError) as exc:
    print(f"Erro na instalação: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
