#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' '需要 Python 3.10+。请安装 Python 后运行 python3 lens.py。' >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else "需要 Python 3.10 或更新版本")'
exec python3 -B service.py start "$@"
