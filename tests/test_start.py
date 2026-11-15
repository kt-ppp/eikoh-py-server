import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

import start


def test_venv_python_path_matches_platform(tmp_path):
    assert start.venv_python_path(tmp_path, os_name="posix") == tmp_path / "bin" / "python"
    assert start.venv_python_path(tmp_path, os_name="nt") == tmp_path / "Scripts" / "python.exe"


def test_select_venv_dir_avoids_overwriting_other_os_venv(tmp_path):
    assert start.select_venv_dir(tmp_path, os_name="posix") == tmp_path / ".venv"
    assert start.select_venv_dir(tmp_path, os_name="nt") == tmp_path / ".venv"

    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    assert start.select_venv_dir(tmp_path, os_name="nt") == tmp_path / ".venv-win"
    assert start.select_venv_dir(tmp_path, os_name="posix") == tmp_path / ".venv"

    (tmp_path / ".venv" / "bin").rmdir()
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    assert start.select_venv_dir(tmp_path, os_name="posix") == tmp_path / ".venv-posix"
    assert start.select_venv_dir(tmp_path, os_name="nt") == tmp_path / ".venv"


def test_ensure_venv_creates_missing_or_clears_broken_venv(tmp_path, monkeypatch):
    calls = []
    venv_dir = tmp_path / ".venv"
    venv_python = start.venv_python_path(venv_dir)

    monkeypatch.setattr(start, "is_usable_python", lambda path: False)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: calls.append((cmd, kwargs)))

    assert start.ensure_venv(venv_dir, base_python="py-exec") == venv_python
    assert calls == [([ "py-exec", "-m", "venv", str(venv_dir)], {"check": True})]

    calls.clear()
    venv_dir.mkdir()
    assert start.ensure_venv(venv_dir, base_python="py-exec") == venv_python
    assert calls == [(["py-exec", "-m", "venv", "--clear", str(venv_dir)], {"check": True})]


def test_ensure_venv_skips_when_already_usable(tmp_path, monkeypatch):
    calls = []
    venv_dir = tmp_path / ".venv"
    venv_python = start.venv_python_path(venv_dir)

    monkeypatch.setattr(start, "is_usable_python", lambda path: path == venv_python)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))

    assert start.ensure_venv(venv_dir) == venv_python
    assert calls == []


def test_ensure_dependencies_installs_only_when_missing(tmp_path, monkeypatch):
    calls = []
    venv_python = tmp_path / "python"

    monkeypatch.setattr(start, "has_dependencies", lambda path: True)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))
    start.ensure_dependencies(venv_python, root_dir=tmp_path)
    assert calls == []

    monkeypatch.setattr(start, "has_dependencies", lambda path: False)
    start.ensure_dependencies(venv_python, root_dir=tmp_path)
    assert calls == [[str(venv_python), "-m", "pip", "install", "-e", str(tmp_path)]]


def test_run_server_uses_execv_on_posix_and_popen_wait_on_windows(tmp_path, monkeypatch):
    venv_python = tmp_path / "python"
    expected_cmd = [
        str(venv_python),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]

    exec_calls = []
    monkeypatch.setattr(os, "execv", lambda path, cmd: exec_calls.append((path, cmd)))
    assert start.run_server(venv_python, os_name="posix") == 0
    assert exec_calls == [(str(venv_python), expected_cmd)]

    class FakeProcess:
        def __init__(self):
            self.wait_calls = 0

        def wait(self):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise KeyboardInterrupt
            return 0

    fake = FakeProcess()
    popen_calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: popen_calls.append(cmd) or fake)
    assert start.run_server(venv_python, os_name="nt") == 0
    assert popen_calls == [expected_cmd]
    assert fake.wait_calls == 2


def test_server_config_defaults_to_loopback_and_validates_port(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert start.server_config() == ("127.0.0.1", 8000)

    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")
    assert start.server_config() == ("0.0.0.0", 9000)

    for bad_port in ("abc", "0", "65536"):
        monkeypatch.setenv("PORT", bad_port)
        with pytest.raises(ValueError):
            start.server_config()


def test_resolve_session_db_path_falls_back_when_sqlite_locking_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("SESSION_DB_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    db_path = tmp_path / "data" / "sessions.sqlite3"
    assert start.can_use_sqlite(db_path) is True
    assert start.resolve_session_db_path(tmp_path) is None

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as database:
        database.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY)")
    assert start.can_use_sqlite(db_path) is True

    monkeypatch.setattr(start, "can_use_sqlite", lambda path: False)
    expected = str(tmp_path / "local" / "eikoh-py-server" / "sessions.sqlite3")
    assert start.resolve_session_db_path(tmp_path) == expected

    monkeypatch.setenv("SESSION_DB_PATH", "/custom/sessions.sqlite3")
    assert start.resolve_session_db_path(tmp_path) is None


def test_main_rejects_old_python_and_runs_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(start.sys, "version_info", (3, 9, 0))
    assert start.main(root_dir=tmp_path) == 1
    assert "Python 3.10以上" in capsys.readouterr().err

    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(start, "ensure_venv", lambda venv_dir: tmp_path / "venv-python")
    monkeypatch.setattr(start, "ensure_dependencies", lambda py, root_dir: None)
    run_calls = []
    monkeypatch.setattr(start, "run_server", lambda py, **kwargs: run_calls.append((py, kwargs)) or 0)
    assert start.main(root_dir=tmp_path) == 0
    assert run_calls == [(tmp_path / "venv-python", {"host": "127.0.0.1", "port": 8000})]
    assert "アプリ一覧: http://localhost:8000/" in capsys.readouterr().out

    def fail_deps(py, root_dir):
        raise subprocess.CalledProcessError(5, [str(py), "-m", "pip", "install"])

    monkeypatch.setattr(start, "ensure_dependencies", fail_deps)
    assert start.main(root_dir=tmp_path) == 5
    assert "起動準備に失敗しました（終了コード 5）" in capsys.readouterr().err


@pytest.mark.parametrize("command_name, expected_flag", [
    ("python3", ""),
    ("python", ""),
    ("py", "-3"),
])
def test_start_sh_finds_whichever_python_command_exists(tmp_path, command_name, expected_flag):
    bash_path = shutil.which("bash")
    if bash_path is None:
        pytest.skip("bash not available")

    repo_root = Path(__file__).resolve().parents[1]
    shutil.copy(repo_root / "start.sh", tmp_path / "start.sh")
    (tmp_path / "start.py").write_text(
        "import os, sys\nprint(f'CWD={os.getcwd()} ARGS=' + ' '.join(sys.argv))\n",
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / command_name
    if command_name == "py":
        wrapper.write_text(
            f"#!{bash_path}\n"
            f'if [ "${{1:-}}" = "-3" ]; then shift; echo "FLAG=-3"; fi\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
    else:
        wrapper.write_text(
            f'#!{bash_path}\nexec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
    wrapper.chmod(0o755)

    # 実環境のpython/python3を隠すため、偽コマンド以外のpython系を除外したPATHを用意する。
    isolated_bin = tmp_path / "system-bin"
    isolated_bin.mkdir()
    for tool in ("dirname",):
        found = shutil.which(tool)
        if found:
            (isolated_bin / tool).symlink_to(found)
    env = {"PATH": f"{bin_dir}:{isolated_bin}"}

    result = subprocess.run(
        [bash_path, str(tmp_path / "start.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    if expected_flag:
        assert lines[0] == f"FLAG={expected_flag}"
    assert lines[-1] == f"CWD={tmp_path} ARGS=start.py"


def test_start_sh_skips_old_python_and_fails_when_none_found(tmp_path):
    bash_path = shutil.which("bash")
    if bash_path is None:
        pytest.skip("bash not available")

    repo_root = Path(__file__).resolve().parents[1]
    shutil.copy(repo_root / "start.sh", tmp_path / "start.sh")
    (tmp_path / "start.py").write_text("print('OK')\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("dirname",):
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)

    old_python = bin_dir / "python"
    old_python.write_text(f"#!{bash_path}\nexit 1\n", encoding="utf-8")
    old_python.chmod(0o755)

    env = {"PATH": str(bin_dir)}
    fail = subprocess.run([bash_path, str(tmp_path / "start.sh")], env=env, capture_output=True, text=True)
    assert fail.returncode == 1
    assert "Python 3.10以上が見つかりません" in fail.stderr

    good_python3 = bin_dir / "python3"
    good_python3.write_text(f'#!{bash_path}\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    good_python3.chmod(0o755)

    ok = subprocess.run([bash_path, str(tmp_path / "start.sh")], env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip() == "OK"


def test_start_bat_uses_crlf_and_checks_windows_python_commands():
    bat_bytes = (Path(__file__).resolve().parents[1] / "start.bat").read_bytes()
    assert b"\r\n" in bat_bytes
    assert b"\n" not in bat_bytes.replace(b"\r\n", b"")
    text = bat_bytes.decode("utf-8")
    for expected in ('pushd "%~dp0"', "popd", ".venv\\Scripts\\python.exe", ".venv-win\\Scripts\\python.exe", "py -3", "py ", "python ", "python3 ", "start.py"):
        assert expected in text
