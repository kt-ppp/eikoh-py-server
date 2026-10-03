# eikoh-py-server

授業でHTTP、一時ユーザー、投稿の共有を学ぶためのFastAPIモックです。

Python 3.10以上で、リポジトリのルートからインストールします。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

起動（1プロセス）：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 投稿画面：http://localhost:8000/app/sns/
- 生徒向け説明：http://localhost:8000/about
- [教師向け説明](docs/teacher.md)
- [生徒向け説明](docs/student.md)

テスト：

```bash
python -m pytest
```
