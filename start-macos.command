#!/bin/zsh
set -eu
cd "${0:A:h}"
if ! command -v python3 >/dev/null 2>&1; then
  print -u2 '需要 Python 3.10+。请安装 Python 后在此目录运行 python3 lens.py。'
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else "需要 Python 3.10 或更新版本")'
exec python3 -B service.py start "$@"
