from __future__ import annotations

from contextlib import closing, suppress
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT_DIR = Path(os.path.abspath(__file__)).parent
REQUIRED_MODULES = ("fastapi", "uvicorn", "websockets", "jinja2", "python_multipart")
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
VERSION_CHECK = "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"


def server_config() -> tuple[str, int]:
    """環境変数から待ち受けホストとポートを取得する。"""
    host = os.getenv("HOST", "").strip() or DEFAULT_HOST
    raw_port = os.getenv("PORT", "").strip() or str(DEFAULT_PORT)
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("PORT は 1〜65535 の整数にしてください。") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT は 1〜65535 の整数にしてください。")
    return host, port


def venv_python_path(venv_dir: Path, *, os_name: str | None = None) -> Path:
    """OSごとの仮想環境内Python実行ファイルのパスを返す。"""
    target_os = os.name if os_name is None else os_name
    if target_os == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def select_venv_dir(root_dir: Path, *, os_name: str | None = None) -> Path:
    """別OSの仮想環境を壊さないよう使用する仮想環境ディレクトリを選ぶ。"""
    target_os = os.name if os_name is None else os_name
    default_dir = root_dir / ".venv"
    if target_os == "nt":
        if (default_dir / "bin").exists() and not (default_dir / "Scripts" / "python.exe").exists():
            return root_dir / ".venv-win"
        return default_dir
    if (default_dir / "Scripts").exists() and not (default_dir / "bin" / "python").exists():
        return root_dir / ".venv-posix"
    return default_dir


def is_usable_python(python_path: Path) -> bool:
    """指定パスのPythonが実行可能かつ3.10以上か確認する。"""
    if not python_path.is_file():
        return False
    result = subprocess.run(
        [str(python_path), "-c", VERSION_CHECK],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def ensure_venv(venv_dir: Path, *, base_python: str | None = None) -> Path:
    """必要に応じて仮想環境を作成または再構築し、そのPythonパスを返す。"""
    venv_python = venv_python_path(venv_dir)
    if is_usable_python(venv_python):
        return venv_python
    print(f"仮想環境を作成しています: {venv_dir}", flush=True)
    command = [base_python or sys.executable, "-m", "venv"]
    if venv_dir.exists():
        command.append("--clear")
    command.append(str(venv_dir))
    subprocess.run(command, check=True)
    return venv_python


def has_dependencies(venv_python: Path) -> bool:
    """必要な依存ライブラリが仮想環境からimportできるか確認する。"""
    imports = ", ".join(REQUIRED_MODULES)
    result = subprocess.run(
        [str(venv_python), "-c", f"import {imports}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def ensure_dependencies(venv_python: Path, root_dir: Path = ROOT_DIR) -> None:
    """依存ライブラリが不足していればプロジェクトをeditable installする。"""
    if has_dependencies(venv_python):
        return
    print("依存ライブラリをインストールしています...", flush=True)
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-e", str(root_dir)],
        check=True,
    )


def local_session_db_path() -> Path:
    """共有フォルダ等でSQLiteが使えない場合に使用するローカルDBパスを返す。"""
    base = os.getenv("LOCALAPPDATA", "").strip() or tempfile.gettempdir()
    return Path(base) / "eikoh-py-server" / "sessions.sqlite3"


def can_use_sqlite(db_path: Path) -> bool:
    """指定パスでSQLiteのロックと書き込みが行えるか確認する。"""
    existing = db_path.exists()
    target = db_path if existing else db_path.parent / f".sqlite-probe-{os.getpid()}.sqlite3"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target, timeout=1.0)) as database:
            if existing:
                database.execute("BEGIN IMMEDIATE")
                database.rollback()
            else:
                database.execute("CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY)")
                database.commit()
        return True
    except (sqlite3.Error, OSError):
        return False
    finally:
        if not existing:
            with suppress(OSError):
                target.unlink()


def resolve_session_db_path(root_dir: Path) -> str | None:
    """既定のSQLiteが使えない環境ではローカルの保存先を返す。"""
    if os.getenv("SESSION_DB_PATH", "").strip():
        return None
    if can_use_sqlite(root_dir / "data" / "sessions.sqlite3"):
        return None
    return str(local_session_db_path())


def run_server(
    venv_python: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    os_name: str | None = None,
) -> int:
    """OSに応じた方法でUvicornサーバを起動する。"""
    target_os = os.name if os_name is None else os_name
    command = [
        str(venv_python),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if target_os != "nt":
        os.execv(str(venv_python), command)
        return 0
    process = subprocess.Popen(command)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            pass


def main(root_dir: Path = ROOT_DIR) -> int:
    """仮想環境と依存関係を準備してUvicornサーバを起動する。"""
    if sys.version_info < (3, 10):
        print("Python 3.10以上が必要です。", file=sys.stderr)
        return 1
    try:
        host, port = server_config()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    os.chdir(root_dir)
    try:
        venv_python = ensure_venv(select_venv_dir(root_dir))
        ensure_dependencies(venv_python, root_dir=root_dir)
    except subprocess.CalledProcessError as exc:
        print(f"起動準備に失敗しました（終了コード {exc.returncode}）: {exc.cmd}", file=sys.stderr)
        return exc.returncode or 1
    except OSError as exc:
        print(f"起動準備に失敗しました: {exc}", file=sys.stderr)
        return 1
    fallback_db = resolve_session_db_path(root_dir)
    if fallback_db is not None:
        os.environ["SESSION_DB_PATH"] = fallback_db
        print(f"セッションDBをローカルに保存します: {fallback_db}", flush=True)
    display_host = "localhost" if host in ("127.0.0.1", "0.0.0.0", "localhost") else host
    print(f"アプリ一覧: http://{display_host}:{port}/", flush=True)
    exit_code = run_server(venv_python, host=host, port=port)
    if exit_code != 0:
        print(f"サーバが終了しました（終了コード {exit_code}）。", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
