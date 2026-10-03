# eikoh-py-server

授業でHTTP、一時ユーザー、投稿の共有を学ぶためのFastAPIモックです。

Python 3.10以上で、リポジトリのルートから起動します（1プロセス）。
初回は仮想環境の作成と必要な依存関係のインストールを自動で行います。

```bash
./start.sh
```

停止は `Ctrl+C` です。

- 投稿画面：http://localhost:8000/app/sns/
- 生徒向け説明：http://localhost:8000/about
- [教師向け説明](docs/teacher.md)
- [生徒向け説明](docs/student.md)

テスト：

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
```
