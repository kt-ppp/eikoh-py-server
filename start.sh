#!/usr/bin/env bash
set -eu

cd -- "$(dirname -- "$0")"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi

if ! .venv/bin/python -c 'import fastapi, uvicorn, websockets, jinja2, python_multipart, markdown' >/dev/null 2>&1; then
    .venv/bin/python -m pip install -e .
fi

printf '投稿画面: http://localhost:8000/app/sns/\n説明: http://localhost:8000/about\n'
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
