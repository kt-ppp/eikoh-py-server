# 教師向け：サーバの使い方と処理

名前を入力して参加する、授業用の試作サーバです。

## 起動と保存先

リポジトリのルートで `./start.sh` を実行します。初回は仮想環境の作成と依存関係のインストールを行い、Uvicornを起動します。停止は `Ctrl+C` です。

SNSは `/app/sns/`、生徒向けの説明は `/about` にあります。

| データ | 保存先 | 再起動後 |
| --- | --- | --- |
| 参加情報 | SQLiteの `data/sessions.sqlite3` | 有効期限まで使える |
| 投稿 | Pythonのメモリ、最新100件 | 消える |
| WebSocket接続 | 起動中のPythonプロセス | ブラウザが再接続する |

投稿と接続はプロセス間で共有していないため、1プロセスで起動してください。DBファイルはGitの管理対象から外しています。

## 参加者の識別

`app/sessions.py` がCookieとDBを扱います。SNS以外のアプリも同じ処理を使えます。

- `name`：画面に表示する名前。1〜30文字で、同名も許可します。
- `user_id`：サーバが発行する参加者の番号。投稿の所有者を判定します。
- session token：参加時に発行するランダム値。ブラウザは `lesson_session` Cookieに保存します。

`create_session()` は参加情報をSQLiteの `sessions` テーブルへ登録します。保存するのはtokenのSHA-256ハッシュ、`user_id`、名前、有効期限です。tokenそのものはDBに保存しません。

`current_user()` はHTTPとWebSocketのCookieからtokenを読み、DBを照合します。期限切れの行は照合時や新規参加時に削除します。名前を入力して参加する方式で、パスワードによる本人認証は行いません。

Cookieには `HttpOnly`、`SameSite=Lax` を設定しています。有効期限は参加時に決まり、アクセスしても延びません。
期限切れの後に参加し直すと新しい `user_id` になります。そのため、以前の参加情報で書いた投稿は編集・削除できなくなります。

## SNSの処理

`app/apps/sns/state.py` が投稿の追加・編集・削除を担当します。投稿は `id`、`user_id`、`name`、`text` を持つ辞書です。前後の空白を除いた本文が1〜500文字か確認し、最新100件を残します。

編集・削除では、保存済みの投稿の `user_id` とセッションの `user_id` を比較します。画面からボタンを隠すだけでなく、HTTPやWebSocketへ直接操作を送った場合もサーバで拒否します。他人の投稿への操作は403、存在しない投稿への操作は404です。

名前と本文はJinja2の自動escapeで表示します。WebSocketで届いた内容も、JavaScriptの `textContent` で表示します。投稿にHTMLが含まれていても、文字として扱います。

HTTPフォームの処理は `routes.py` にあります。

| URL | 処理 |
| --- | --- |
| `GET /app/sns/` | 参加フォームまたは投稿画面を表示 |
| `POST /app/sns/join` | 参加情報とCookieを作成 |
| `POST /app/sns/post` | 投稿を追加 |
| `POST /app/sns/posts/{id}/edit` | 自分の投稿を編集 |
| `POST /app/sns/posts/{id}/delete` | 自分の投稿を削除 |

正常に処理すると303 redirectで投稿画面へ戻します。最後の表示がGETになるので、再読み込みによるフォームの再送信を防げます。JavaScriptが使えない場合も、このフォームで操作できます。

## WebSocket

参加後のブラウザは `/app/sns/ws` へ接続します。接続処理と一覧の配信は `realtime.py`、画面の更新は `static/sns.js` にあります。

ブラウザから送るメッセージは次の形式です。1操作につき1つのJSONを送信します。

```json
{"action": "post", "text": "こんにちは"}
{"action": "edit", "id": 1, "text": "書き直した文章"}
{"action": "delete", "id": 1}
```

`id` は実際に表示された投稿の番号を使います。投稿者の名前や `user_id` はCookieから判定するため、メッセージで指定しても変更できません。

接続直後と投稿の変更時に、`type: "posts"` と投稿一覧を配信します。操作したブラウザには成功を示す `type: "ok"`、入力や権限の問題には `type: "error"` を返します。HTTPフォームによる変更も接続中のブラウザへ配信します。

接続時にはCookieとOriginを確認します。参加情報は操作のたびにも照合し、有効期限を迎えた接続は閉じます。ブラウザは参加画面へ戻ります。通信が途切れた場合は再接続し、最新の一覧を受け取ります。

## 設定

起動前の環境変数で変更できます。

| 変数 | 標準値 | 用途 |
| --- | --- | --- |
| `LESSON_SESSION_TTL_SECONDS` | `21600` | 参加情報の有効時間、秒 |
| `SESSION_DB_PATH` | `data/sessions.sqlite3` | SQLiteファイルの保存先 |
| `COOKIE_SECURE` | `false` | `true` でCookieをHTTPSに限定 |

```bash
LESSON_SESSION_TTL_SECONDS=21600 ./start.sh
```

HTTPS環境では `COOKIE_SECURE=true ./start.sh` を使います。HTTPS終端はCaddy等のリバースプロキシで行い、WebSocketも転送してください。ブラウザはHTTPSのページから `wss://` で接続します。

## ファイルを読む順序

`main.py` でアプリの登録を確認し、`sessions.py` で参加情報の保存と照合を読みます。SNSの処理は `apps/sns/routes.py`、`state.py`、`realtime.py` の順に追えます。

`/about` は `pages.py` が `docs/student.md` をHTMLへ変換して表示します。構成とアプリの追加方法は [structure.md](structure.md) にあります。
