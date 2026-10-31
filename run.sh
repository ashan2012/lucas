#!/bin/sh
# Bootstrap a local venv (first run only) and hand over to the CLI.
set -e
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
VENV=.venv

if [ ! -x "$VENV/bin/python" ]; then
  echo "creating $VENV ..."
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
fi

if ! "$VENV/bin/python" -c "import PIL, pypinyin" >/dev/null 2>&1; then
  echo "installing requirements ..."
  "$VENV/bin/python" -m pip install --quiet -r requirements.txt
fi

exec "$VENV/bin/python" -m zige "$@"
