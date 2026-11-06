#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Для визарда нужен Python 3: установите python3 из пакетов вашей ОС.' >&2
    exit 1
fi
cd "$SCRIPT_DIR"
exec python3 -m installer.wizard "$@"
