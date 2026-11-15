@echo off
setlocal
pushd "%~dp0" || exit /b 1

set "PYTHON_BIN="
if exist ".venv\Scripts\python.exe" (
    call ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_BIN=.venv\Scripts\python.exe"
)
if not defined PYTHON_BIN if exist ".venv-win\Scripts\python.exe" (
    call ".venv-win\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_BIN=.venv-win\Scripts\python.exe"
)
if not defined PYTHON_BIN (
    call py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_BIN=py -3"
)
if not defined PYTHON_BIN (
    call py -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_BIN=py"
)
if not defined PYTHON_BIN (
    call python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_BIN=python"
)
if not defined PYTHON_BIN (
    call python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_BIN=python3"
)
if not defined PYTHON_BIN goto :no_python

call %PYTHON_BIN% start.py
set "EXIT_CODE=%errorlevel%"
popd
exit /b %EXIT_CODE%

:no_python
popd
chcp 65001 >nul 2>&1
echo Python 3.10以上が見つかりません。Python 3.10以上をインストールしてください。 1>&2
exit /b 1
