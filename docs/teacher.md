# 教師向け：SNSモックの仕組み

## 1. 今回の位置付け

今回は本番用通信基盤ではなく、仕組みを理解するためのモックです。最大20人程度の授業で、ユーザー識別、サーバ上の状態保持、複数人からの投稿を確認します。
意図的にDB、外部向けAPI、外部APIへの接続、WebSocket、SSE、リアルタイム更新を使っていません。ゲームも実装していません。

## 2. 全体構成

```text
Browser
   │
   │ HTTP GET / POST
   ▼
FastAPI（Uvicornで起動）
   │
   ├── sessions：token → user_id、name、expires_at
   └── posts：投稿のlist
          │
          ▼
      Python memory
```

処理は `app/main.py` にまとめています。HTMLはJinja2、CSSは `app/static/style.css`、説明ページはPython-Markdownで変換します。
`GET /about` はリポジトリ内の `docs/student.md` を読み、HTMLとして表示します。生徒向けの説明を直すときはこのMarkdownを編集します。

**必ず1つのプロセス、1 workerで起動してください。** メモリはプロセス間では共有されないため、複数workerでは参加情報や投稿がばらばらになります。

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

授業ごとにサーバを再起動すると、前の授業の参加情報と投稿が消えます。セッションの期限切れだけでは投稿は消えません。

## 3. `/app/sns/` の画面と処理

- `GET /app/sns/`：Cookieを確認し、未参加なら名前のフォーム、参加済みなら投稿フォームと一覧を表示します。
- `POST /app/sns/join`：名前を確認し、一時ユーザーとセッションを作り、Cookieを発行します。
- `POST /app/sns/post`：セッションと文章を確認し、投稿を追加します。

参加と投稿の成功後は、303 redirectで `GET /app/sns/` に戻ります。入力が不正なら400で同じ画面に説明を表示し、保存しません。
未参加・期限切れの投稿は保存せず、参加画面へredirectします。

## 4. POST/Redirect/GET

```text
フォームからPOST
↓
サーバで保存
↓
303 redirect
↓
GETでページを表示
```

最後に表示しているページをGETにすることで、再読み込み時に同じ投稿を再送信するのを防ぎます。

## 5. ユーザー識別

```text
表示用：name
内部識別：user_id
参加済みか確認するための合い言葉：session token
ブラウザでtokenを保持する場所：Cookie
```

`name` は1〜30文字です。同名でも別々の `user_id` が発行されます。
`user_id` は公開して構いませんが、session tokenは他人に渡さないでください。
tokenは `secrets.token_urlsafe(32)` で作る、推測されにくいランダム値です。本格的な本人認証やアカウント登録は行いません。
投稿者は送られた名前やIDではなく、Cookieのtokenから引いたセッション情報で決まります。

## 6. セッション

`sessions` はメモリ上の辞書です。tokenに対して `user_id`、`name`、`expires_at` を保存し、DBには書きません。
Cookieの名前は `lesson_session` です。`HttpOnly` によりJavaScriptから読めず、`SameSite=Lax` を設定しています。
Cookieの有効期間とサーバ側の期限は、参加時から標準で21600秒（6時間）です。アクセスのたびには延長しません。
期限切れのセッションは参加・投稿・投稿画面へのアクセス時に辞書から取り除きます。期限切れやサーバ再起動後には参加し直します。

起動前の環境変数で設定できます。

```bash
LESSON_SESSION_TTL_SECONDS=21600 uvicorn app.main:app --host 0.0.0.0 --port 8000
```

HTTPS環境では `COOKIE_SECURE=true` を設定して、CookieをHTTPSだけで送るようにします。

```bash
COOKIE_SECURE=true uvicorn app.main:app --host 0.0.0.0 --port 8000
```

教室でのHTTPS終端は、必要になったときに次の構成を使えます。今回はプロキシの構築は行っていません。

```text
Internet / LAN
      ↓ HTTPS
Caddy等のリバースプロキシ
      ↓ HTTP
FastAPI
```

## 7. 投稿

```text
Browser
↓
HTML form POST
↓
FastAPI
↓
sessionを確認
↓
投稿をlistへ追加
↓
redirect
↓
GET
↓
投稿一覧表示
```

`posts` は `id`、`user_id`、`name`、`text` を持つ辞書のlistです。
入力の前後の空白を除き、空文字・空白だけ・500文字を超える文章を拒否します。名前も前後の空白を除いて検証します。
最新100件を保持し、古い投稿から取り除きます。再起動するとすべて消えます。

投稿本文と名前はJinja2の自動escapeで表示します。`<script>` などは文章として表示され、HTMLとして実行されません。
HTMLとして扱うのは、開発者が管理する `docs/student.md` の変換結果だけです。投稿はMarkdownとしても処理しません。

## 8. なぜAPIを作っていないか

今回はモックであり、HTTPの基本的なrequest（お願い）とresponse（返事）だけで十分です。
最初からAPIと画面を分けると、理解すべき要素が増えます。
POST routeはHTMLフォームの処理用で、汎用APIではありません。`/api/messages` などは作らず、自動生成されるAPI説明画面も無効にしています。

## 9. なぜリアルタイム通信をしないか

確認したいのは次の基本構造です。

```text
client（ブラウザ）
↓
server
↓
shared state（共有する投稿）
```

WebSocketやSSEを加えると、接続の維持など別の仕組みも必要になります。今回は再読み込みで共有状態を確認できれば十分です。
JavaScript、自動更新、pollingは使っていません。

## 10. 将来的な拡張

必要になった段階で、例えば次の順に書き直すことが考えられます。

```text
HTML Form
↓
HTTP API
↓
SSE / WebSocket / WebTransport
```

これは将来の選択肢であり、現在は実装していません。教室のネットワーク制約と用途を確認してから通信方法を選びます。

## 11. ゲームへの発展

人狼なら、投稿の代わりに投票・役職アクション・準備完了を送るようになります。
2Dアクションゲームなら、移動・ジャンプ・攻撃などの情報を送ることが考えられます。
サーバが共有状態を持つという考え方は同じですが、ゲームのルールや通信の仕組みは用途に合わせて作り直す必要があります。今回はこれらを実装していません。
