# M5StickCPlus 1.1 中継システム

M5StickCPlus 1.1 を Wi-Fi アクセスポイント（AP）兼リバーストンネル中継器として動かし、**サーバPC・クライアントPCのいずれも着信ポートを開放せずに** `eikoh-py-server` を利用できるようにする構成です。

## 通信構成

```text
[クライアントPC (ブラウザ)] ── Wi-Fi + TCP:8080 (発信のみ) ──▶ [M5StickCPlus 1.1]
                                                                 (AP: 192.168.4.1)
[サーバPC (server_bridge.py)] ─ Wi-Fi + TCP:9000 (発信のみ) ─▶ [M5StickCPlus 1.1]
        │
        └─▶ ローカルループバック 127.0.0.1:8080 (eikoh-py-server)
```

- **すべてのPCがマイコン宛て（`192.168.4.1`）にのみ接続要求（アウトバウンド）**を行います。
- サーバPC上の `eikoh-py-server` は `127.0.0.1:8080`（または `0.0.0.0:8080`）で動かし、`server_bridge.py` がマイコン宛てに多重化トンネルセッションを確立して HTTP / Cookie / WebSocket 通信をそのまま中継します。

## 1. M5StickCPlus 1.1 への書き込み（Arduino IDE）

1. Arduino IDE に ESP32 ボードパッケージ（`esp32 by Espressif Systems`）と `M5StickCPlus` ライブラリをインストールします。
2. [m5stickc_plus_relay.ino](m5stickc_plus_relay/m5stickc_plus_relay.ino) を Arduino IDE で開きます。
3. 必要に応じて先頭付近の `AP_SSID` / `AP_PASS`（8文字以上）/ `TUNNEL_TOKEN` を変更します。
4. ボードに **M5StickCPlus**（または **ESP32 Pico Kit**）を選択して書き込みます。

起動すると LCD に以下の情報が表示されます。

- `SSID`: `Eikoh-AP`
- `PASS`: `eikoh1234`
- `URL`: `http://192.168.4.1:8080`
- `SRV`: `WAIT PORT 9000` → サーバPC接続後に `ON (192.168.4.x)`
- ボタン操作：`BtnA` で画面ページ切替、`BtnB` で輝度切替（P1）／全クライアントセッションリセット（P2）

## 2. サーバPC側の起動

1. サーバPCを M5StickCPlus 1.1 の Wi-Fi（`Eikoh-AP`）に接続します。
2. ターミナル1で `eikoh-py-server` を起動します（ファイアウォール許可なしで動かす場合は `HOST=127.0.0.1` を推奨）。

   macOS / Linux:
   ```bash
   HOST=127.0.0.1 PORT=8080 ./start.sh
   ```
   Windows（コマンドプロンプト）:
   ```bat
   set HOST=127.0.0.1
   set PORT=8080
   start.bat
   ```

3. ターミナル2で中継スクリプトを実行します（Python 3.10+ 標準ライブラリのみで動作します）。

   ```bash
   python3 m5_relay/server_bridge.py
   ```
   （Windows では `py -3 m5_relay\server_bridge.py`）

   オプション一覧：
   - `--m5-host`（既定 `192.168.4.1`）
   - `--m5-port`（既定 `9000`）
   - `--target-host`（既定 `127.0.0.1`）
   - `--target-port`（既定 `8080`）
   - `--token`（既定 `eikoh-server-v1`、スケッチの `TUNNEL_TOKEN` と一致させる）

## 3. クライアントPCからの接続

1. クライアントPCを M5StickCPlus 1.1 の Wi-Fi（`Eikoh-AP`）に接続します。
2. ブラウザで `http://192.168.4.1:8080/` を開きます。
3. `/app/sns/` や `/app/werewolf/`（WebSocket含む）がそのまま動作します。

## 制約・注意点

- ESP32 の SoftAP 仕様上、Wi-Fi 同時接続端末数の上限は最大 **10台** です（サーバPC 1台 + クライアントPC 最大9台）。
- M5StickCPlus 1.1 の内蔵バッテリーは容量が小さいため、長時間利用時は USB 給電を推奨します。
- HTTP リクエストは ESP32 側のソケット枯渇を防ぐため、`server_bridge.py` が通常の HTTP 通信のみ `Connection: close` に正規化します（WebSocket の `Upgrade: websocket` はそのまま維持されます）。
