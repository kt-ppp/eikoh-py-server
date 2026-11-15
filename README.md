# eikoh-py-server

Python/FastAPI Server

## 起動

Python 3.10以上が必要です。初回は仮想環境の作成と依存関係のインストールを自動で行います。`python` / `py` / `python3` のいずれかが使えれば起動できます。標準では `127.0.0.1:8000` で待ち受けるため、管理者権限（ファイアウォール許可等）なしで起動できます。

macOS / Linux / Git Bash:
```bash
./start.sh
```

Windows（コマンドプロンプト）:
```bat
start.bat
```

Windows（PowerShell）:
```powershell
.\start.bat
```

停止は `Ctrl+C`



## 説明書

- [SNS](docs/apps/sns.md)：アプリの概要と使用技術
- [人狼](docs/apps/werewolf.md)：ルームの使い方と使用技術
- [簡易仕様書](docs/spec.md)：使い方、保存先、通信、設定
- [ファイル構成](docs/structure.md)：各ファイルの役割とアプリの追加方法

## テスト

macOS / Linux:
```bash
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest
```

Windows:
```bat
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest
```
