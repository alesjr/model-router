#!/bin/sh
# Install the personal launcher; dependencies and Codex installation stay separate.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/model-router/scripts/statusbar_install.py" "$@"
