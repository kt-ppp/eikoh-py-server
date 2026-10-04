# ファイル構成

共通処理は `app/` の直下、各アプリのコードと素材は `app/apps/<アプリ名>/` に置きます。

```text
app/
├── main.py                 # サーバ、router、静的ファイルの登録
├── sessions.py             # SQLiteの参加情報とCookie
├── pages.py                # /about
├── templates/about.html
├── static/style.css        # 共通CSS
└── apps/
    └── sns/
        ├── routes.py       # HTMLフォームの処理
        ├── state.py        # 投稿と編集・削除の判定
        ├── realtime.py     # WebSocket接続と一覧の配信
        ├── templates/sns.html
        └── static/
            ├── style.css
            └── sns.js      # WebSocketでの操作と画面の更新

data/sessions.sqlite3      # 実行時に作成、Gitの管理対象外

docs/
├── teacher.md
├── student.md              # /aboutの内容
└── structure.md

tests/
├── conftest.py             # テスト用DBとクライアント
├── test_sessions.py        # DB保存と失効
├── test_pages.py           # 説明ページと静的ファイル
├── test_registration.py    # アプリ間の参加情報の共有
└── apps/
    ├── conftest.py         # SNSの状態をテストごとに初期化
    ├── test_sns.py
    ├── test_mutations.py
    └── test_websocket.py

start.sh
pyproject.toml
README.md
```

Pythonパッケージのディレクトリには `__init__.py` も置いています。

## アプリの追加

例えば `quiz` を追加するなら、`app/apps/quiz/` に `__init__.py` と `routes.py` を作ります。`routes.py` に次のrouterを定義します。

```python
from fastapi import APIRouter

router = APIRouter(prefix="/app/quiz")
```

`@router.get("/")` で登録した画面は `/app/quiz/` になります。`main.py` で読み込み、登録してください。

```python
from app.apps.quiz import routes as quiz

app.include_router(quiz.router)
```

HTMLはそのアプリの `templates/`、CSSやJavaScriptは `static/` に置きます。SNSと同様に `APP_DIR = Path(__file__).resolve().parent` を定義し、Jinja2にテンプレートの場所を渡してください。

静的ファイルがあれば `main.py` に配信先も登録します。

```python
app.mount("/app/quiz/static", StaticFiles(directory=quiz.APP_DIR / "static"), name="quiz_static")
```

HTMLからは `/app/quiz/static/style.css` などを参照します。`pyproject.toml` の `[tool.setuptools.package-data]` にも素材を指定します。

```toml
"app.apps.quiz" = ["templates/*.html", "static/*.css", "static/*.js"]
```

参加者の確認には `app.sessions.current_user(request)` を使います。WebSocketでも同じ関数を使えます。参加フォームを作る場合は、名前を検証してから `create_session(name, response)` を呼びます。

問題やゲームの状態など、アプリ固有のデータはアプリ内で管理してください。テストは `tests/apps/` に追加します。起動コマンドは `./start.sh` です。
