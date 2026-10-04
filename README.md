# eikoh-py-server

授業用のPython/FastAPI試作サーバ。SNSでは、名前を入力して参加し、投稿・編集・削除ができます。ほかの人の操作もWebSocketで画面に反映されます。

## 起動

Python 3.10以上が必要です。リポジトリのルートで実行してください。初回は仮想環境と依存関係を自動で用意します。

```bash
./start.sh
```

- SNS：http://localhost:8000/app/sns/
- 生徒向けの説明：http://localhost:8000/about

停止は `Ctrl+C`。編集・削除は自分の投稿だけに使えます。

参加情報は `data/sessions.sqlite3` に保存され、標準で6時間有効です。サーバを再起動しても参加情報は残ります。投稿はメモリ上の最新100件で、再起動すると消えます。

## 説明書

- [教師向け](docs/teacher.md)：処理の流れ、保存先、設定
- [生徒向け](docs/student.md)：使い方と通信の仕組み
- [ファイル構成](docs/structure.md)：各ファイルの役割とアプリの追加方法

## テスト

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
```
