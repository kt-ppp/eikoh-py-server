#!/usr/bin/env bash
set -eu

cd -- "$(dirname -- "$0")"

run_if_python310() {
    if command -v "$1" >/dev/null 2>&1 && "$@" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        exec "$@" start.py
    fi
}

run_if_python310 .venv/bin/python
run_if_python310 .venv-posix/bin/python
run_if_python310 .venv/Scripts/python.exe
run_if_python310 python3
run_if_python310 python
run_if_python310 py -3
run_if_python310 py
for cmd in python3.13 python3.12 python3.11 python3.10; do
    run_if_python310 "$cmd"
done

echo "Python 3.10以上が見つかりません。Python 3.10以上をインストールしてください。" >&2
exit 1
