# ファイル構成とアプリの追加方法

1つのFastAPIサーバに、授業用のアプリを追加していく構成です。
共通処理は `app/` の直下、アプリ固有のコードと素材は `app/apps/<アプリ名>/` に置きます。

```text
app/
├── main.py                 # サーバ設定、ページ・アプリ・静的ファイルの登録
├── sessions.py             # 共通の一時ユーザー識別とCookie
├── pages.py                # サイト共通のページ（/about）
├── templates/
│   └── about.html
├── static/
│   └── style.css           # 全体で使う基本的なCSS
└── apps/
    └── sns/
        ├── routes.py       # /app/sns/ の処理と投稿データ
        ├── templates/
        │   └── sns.html
        └── static/
            └── style.css   # SNS固有のCSS

docs/
├── teacher.md
├── student.md              # /about で表示する説明
└── structure.md

tests/
├── conftest.py             # 共通のテスト用ブラウザとセッション初期化
├── test_pages.py           # サイト共通ページと静的ファイルの確認
├── test_registration.py    # 別アプリの登録と共通ユーザーの確認
└── apps/
    └── test_sns.py          # SNSの参加・投稿の確認

start.sh                    # 1コマンドで起動
pyproject.toml
README.md
```

Pythonパッケージの各ディレクトリには空の `__init__.py` も置いています。

## アプリを追加するとき

以下は、将来 `quiz` というアプリを追加する場合の手順です。現在このアプリは実装していません。

1. `app/apps/quiz/` を作り、`__init__.py` と `routes.py` を置きます。
2. `routes.py` で `APIRouter(prefix="/app/quiz")` を作り、そのアプリのGET・POST処理を書きます。`@router.get("/")` の画面は `/app/quiz/` になります。
3. 画面が必要なら同じディレクトリに `templates/` を作り、`Jinja2Templates` にその場所を渡します。CSS等が必要なら `static/` へ置きます。
4. `app/main.py` で読み込み、routerを登録します。

```python
from app.apps.quiz import routes as quiz

app.include_router(quiz.router)
```

静的ファイルがある場合は、`routes.py` でSNSと同じように `APP_DIR = Path(__file__).resolve().parent` を定義して、`main.py` に以下も追加します。

```python
app.mount("/app/quiz/static", StaticFiles(directory=quiz.APP_DIR / "static"), name="quiz_static")
```

テンプレートからは `/app/quiz/static/style.css` のように参照します。アプリごとにテンプレートの読み込み先と静的ファイルのURLを分けるので、同じファイル名を使っても混ざりません。
配布用パッケージに素材を含めるため、`pyproject.toml` の `[tool.setuptools.package-data]` にもSNSと同様に追加します。

```toml
"app.apps.quiz" = ["templates/*.html", "static/*.css"]
```

参加済みのユーザーが必要な場合は、`app.sessions.current_user(request)` を使います。新しい参加フォームを作る場合も、入力を検証してから `create_session(name, response)` を呼びます。Cookieの名前や発行処理をアプリごとにコピーする必要はありません。

投稿、問題、ゲームの状態など、そのアプリだけが使うデータはアプリ内に置きます。テストは `tests/apps/test_quiz.py` に追加します。
アプリを増やしても起動は `./start.sh` のままです。メモリ上のデータを共有するため、引き続き1プロセスで動かします。
