#!/usr/bin/env bash
set -euo pipefail

# 用法：
#   ./scripts/png_to_ico.sh SuperViewer
#   ./scripts/png_to_ico.sh MyApp

#APP_NAME="${1:-SuperViewer}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-${0}}")" && pwd)"
#ROOT_DIR="${SCRIPT_DIR%/scripts}"

python3 "${SCRIPT_DIR}/png_to_ico.py"
# "${APP_NAME}"

