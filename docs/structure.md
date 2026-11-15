# ファイル構成

共通処理は `app/` の直下、各アプリのコードと素材は `app/apps/<アプリ名>/` に置きます。

```text
index.html                 # / で表示するアプリ一覧
app/
├── main.py                 # アプリの登録とタイマーの起動
├── catalog.py              # apps配下のroutesの検出と一覧
├── sessions.py             # SQLiteの参加情報とCookie
├── pages.py                # /、/index.html
├── static/style.css        # 共通CSS
└── apps/
    ├── sns/
    │   ├── routes.py       # HTMLフォームの処理
    │   ├── state.py        # 投稿と編集・削除の判定
    │   ├── realtime.py     # WebSocket接続と一覧の配信
    │   ├── templates/sns.html
    │   └── static/         # SNSのCSS、JavaScript
    └── werewolf/
        ├── routes.py       # 参加、ルーム作成、各画面
        ├── state.py        # 役職、夜の行動、投票、勝敗判定
        ├── realtime.py     # 本人用の配信と自動タイマー
        ├── templates/      # ルーム、設定、ルールのポップアップ
        └── static/         # 人狼のCSS、JavaScript

data/sessions.sqlite3      # 実行時に作成、Gitの管理対象外

docs/
├── apps/
│   ├── sns.md              # SNSの概要と使用技術
│   └── werewolf.md         # 人狼の使い方と使用技術
├── spec.md                 # 簡易仕様書
└── structure.md

tests/
├── conftest.py             # テスト用DB、ゲームの初期化、クライアント
├── test_sessions.py        # DB保存と失効
├── test_pages.py           # トップページと静的ファイル
├── test_registration.py    # アプリ間の参加情報の共有
└── apps/
    ├── conftest.py         # SNSの状態をテストごとに初期化
    ├── test_sns.py
    ├── test_mutations.py
    ├── test_websocket.py
    ├── test_werewolf_state.py
    └── test_werewolf_routes.py

start.sh
start.bat
start.py
pyproject.toml
README.md
```

Pythonパッケージのディレクトリには `__init__.py` も置いています。

## アプリの追加

例えば `quiz` を追加するなら、`app/apps/quiz/` に `__init__.py` と `routes.py` を作ります。

```python
from pathlib import Path
from fastapi import APIRouter

APP_DIR = Path(__file__).resolve().parent
APP_TITLE = "クイズ"
APP_DESCRIPTION = "問題に答えるアプリです。"
router = APIRouter(prefix="/app/quiz")
```

`@router.get("/")` で登録した画面は `/app/quiz/` になります。起動時に `catalog.py` が `routes.py` を検出して登録し、トップページの一覧にも表示します。

HTMLはそのアプリの `templates/`、CSSやJavaScriptは `static/` に置きます。SNSと同様に `APP_DIR = Path(__file__).resolve().parent` を定義し、Jinja2にテンプレートの場所を渡してください。

`static/` があれば、起動時に `/app/quiz/static/` で配信するよう登録します。

HTMLからは `/app/quiz/static/style.css` などを参照します。`pyproject.toml` の `[tool.setuptools.package-data]` にも素材を指定します。

```toml
"app.apps.quiz" = ["templates/*.html", "static/*.css", "static/*.js"]
```

参加者の確認には `app.sessions.current_user(request)` を使います。WebSocketでも同じ関数を使えます。参加フォームを作る場合は、名前を検証してから `create_session(name, response)` を呼びます。

名前の重複を拒否する場合は、SNSのように `require_unique_name=True` を指定し、409のエラーを参加フォームに表示します。指定しない場合は、共通のセッション処理で同名を許可します。

問題やゲームの状態など、アプリ固有のデータはアプリ内で管理してください。テストは `tests/apps/`、アプリの簡単な説明は `docs/apps/` に追加します。起動コマンドは `./start.sh`（Windowsでは `start.bat` または `.\start.bat`）です。
