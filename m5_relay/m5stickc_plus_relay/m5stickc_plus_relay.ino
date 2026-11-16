#include <M5StickCPlus.h>
#include <WiFi.h>
#include <errno.h>
#include <sys/socket.h>

// ============================================================
// M5StickCPlus 1.1 Wi-Fi AP + Reverse-Tunnel Relay for eikoh-py-server
//
// トポロジ:
//   [クライアントPC群] --(Wi-Fi + TCP:8080)--> [M5StickCPlus 1.1 (192.168.4.1)]
//   [サーバPC]         --(Wi-Fi + TCP:9000)--> [M5StickCPlus 1.1 (192.168.4.1)]
//     ※すべてのPCはマイコン宛てにのみ接続要求（アウトバウンド）を行い、
//       PC側での着信ポート待ち受け・ファイアウォール許可を必要としません。
// ============================================================

// ===== Wi-Fi AP 設定 =====
static const char* AP_SSID = "Eikoh-AP";
static const char* AP_PASS = "eikoh1234";  // WPA2-PSK のため 8文字以上
static const uint8_t AP_CHANNEL = 6;
static const uint8_t MAX_STA_CONN = 10;    // ESP32 SoftAP 最大接続端末数

static const IPAddress AP_IP(192, 168, 4, 1);
static const IPAddress AP_GATEWAY(192, 168, 4, 1);
static const IPAddress AP_SUBNET(255, 255, 255, 0);

// ===== 中継・トンネル設定 =====
static const uint16_t CLIENT_PORT = 8080;  // クライアントブラウザ向け待受ポート
static const uint16_t SERVER_PORT = 9000;  // サーバPC接続向け待受ポート
static const char* TUNNEL_TOKEN = "eikoh-server-v1";

static const size_t MAX_SESSIONS = 10;          // 同時中継クライアントセッション数上限
static const size_t CLIENT_TX_BUF_SIZE = 3072;  // セッションごとの送信リングバッファ (= 初期クレジット)
static const size_t TUNNEL_TX_BUF_SIZE = 4096;  // M5 -> サーバPC 送信リングバッファ
static const size_t TUNNEL_TX_CTRL_RESERVE = 512;  // 制御フレーム (ACK/OPEN/CLOSE/PONG) 用予約域
static const size_t MAX_CHUNK_SIZE = 1024;      // 1 DATAフレームの最大ペイロード長
static const uint32_t HANDSHAKE_TIMEOUT_MS = 3000;
static const uint32_t TUNNEL_IDLE_TIMEOUT_MS = 20000;
static const uint32_t ZERO_TRAFFIC_TIMEOUT_MS = 10000;  // ブラウザの投機的先行接続の掃除用
static const uint32_t UI_REFRESH_INTERVAL_MS = 250;

// ===== バイナリフレーム定義 (5バイトヘッダ: [type:1][session_id:2LE][length:2LE]) =====
static const uint8_t FRAME_HELLO = 0x01;
static const uint8_t FRAME_HELLO_ACK = 0x02;
static const uint8_t FRAME_HELLO_ERR = 0x03;
static const uint8_t FRAME_PING = 0x04;
static const uint8_t FRAME_PONG = 0x05;
static const uint8_t FRAME_OPEN = 0x10;
static const uint8_t FRAME_DATA = 0x11;
static const uint8_t FRAME_ACK = 0x12;
static const uint8_t FRAME_CLOSE = 0x13;
static const size_t FRAME_HEADER_SIZE = 5;

static const char HTTP_503_BODY[] =
    "HTTP/1.1 503 Service Unavailable\r\n"
    "Content-Type: text/html; charset=utf-8\r\n"
    "Connection: close\r\n"
    "\r\n"
    "<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
    "<title>503 サーバ未接続</title></head><body>"
    "<h1>サーバPCが接続されていません (503)</h1>"
    "<p>サーバPC側で eikoh-py-server と server_bridge.py を起動し、"
    "M5StickCPlus 1.1 の Wi-Fi AP に接続してください。</p>"
    "</body></html>";

static const char HTTP_503_BUSY_BODY[] =
    "HTTP/1.1 503 Service Unavailable\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "Connection: close\r\n"
    "\r\n"
    "中継セッションが上限に達しています。少し待ってから再読み込みしてください。\n";

template <size_t CAPACITY>
struct RingBuffer {
  uint8_t data[CAPACITY];
  size_t head = 0;  // 次の書き込み位置
  size_t tail = 0;  // 次の読み込み位置
  size_t count = 0;

  void clear() {
    head = 0;
    tail = 0;
    count = 0;
  }

  size_t size() const { return count; }
  size_t freeSpace() const { return CAPACITY - count; }

  bool push(const uint8_t* src, size_t len) {
    if (len > freeSpace()) return false;
    size_t first = CAPACITY - head;
    if (first > len) first = len;
    memcpy(data + head, src, first);
    if (len > first) {
      memcpy(data, src + first, len - first);
    }
    head = (head + len) % CAPACITY;
    count += len;
    return true;
  }

  const uint8_t* contiguousReadPtr(size_t* outLen) const {
    if (count == 0) {
      *outLen = 0;
      return nullptr;
    }
    size_t contig = CAPACITY - tail;
    if (contig > count) contig = count;
    *outLen = contig;
    return data + tail;
  }

  void consume(size_t len) {
    if (len >= count) {
      clear();
      return;
    }
    tail = (tail + len) % CAPACITY;
    count -= len;
  }
};

struct SessionSlot {
  bool active = false;
  uint16_t sessionId = 0;
  WiFiClient client;
  RingBuffer<CLIENT_TX_BUF_SIZE> txBuf;  // サーバPC -> クライアントPC 送信待ちバッファ
  uint16_t pendingAck = 0;               // サーバPCへ返却予定の消費済みクレジット量
  bool remoteClosed = false;             // サーバPC側が CLOSE を通知済み
  bool hasTraffic = false;
  uint32_t openedAtMs = 0;

  void reset() {
    if (client) {
      client.stop();
    }
    active = false;
    sessionId = 0;
    txBuf.clear();
    pendingAck = 0;
    remoteClosed = false;
    hasTraffic = false;
    openedAtMs = 0;
  }
};

struct FrameParser {
  enum State { WAIT_HEADER, WAIT_PAYLOAD };
  State state = WAIT_HEADER;
  uint8_t header[FRAME_HEADER_SIZE];
  uint8_t type = 0;
  uint16_t sessionId = 0;
  uint16_t length = 0;
  size_t pos = 0;
  uint8_t payload[MAX_CHUNK_SIZE];

  void reset() {
    state = WAIT_HEADER;
    type = 0;
    sessionId = 0;
    length = 0;
    pos = 0;
  }
};

static WiFiServer clientServer(CLIENT_PORT, 8);
static WiFiServer serverServer(SERVER_PORT, 2);

static WiFiClient tunnelClient;
static bool tunnelReady = false;
static uint32_t tunnelLastRxMs = 0;
static FrameParser tunnelParser;
static RingBuffer<TUNNEL_TX_BUF_SIZE> tunnelTxBuf;

static WiFiClient pendingTunnel;
static uint32_t pendingConnectedMs = 0;
static FrameParser pendingParser;

static SessionSlot sessions[MAX_SESSIONS];
static uint16_t nextSessionId = 1;
static uint8_t readScratch[MAX_CHUNK_SIZE];

static uint64_t totalTxBytes = 0;  // クライアントへ中継した総バイト数
static uint64_t totalRxBytes = 0;  // クライアントから中継した総バイト数
static uint8_t uiPage = 0;
static bool lcdDim = false;
static uint32_t lastUiUpdateMs = 0;

// Arduino-ESP32 2.0.11+ / 3.x では accept()、それ以前は available() を使用
WiFiClient acceptClient(WiFiServer& srv) {
#ifdef ESP_ARDUINO_VERSION_VAL
  #if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(2, 0, 11)
    return srv.accept();
  #else
    return srv.available();
  #endif
#else
  return srv.available();
#endif
}

size_t nonBlockingWrite(WiFiClient& client, const uint8_t* data, size_t len, bool* fatalError) {
  *fatalError = false;
  if (len == 0) return 0;
  int fd = client.fd();
  if (fd < 0) {
    *fatalError = true;
    return 0;
  }
  ssize_t written = send(fd, data, len, MSG_DONTWAIT);
  if (written > 0) {
    return static_cast<size_t>(written);
  }
  if (written < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
    return 0;
  }
  if (written == 0) {
    return 0;
  }
  *fatalError = true;
  return 0;
}

int findSlotBySessionId(uint16_t sessionId) {
  if (sessionId == 0) return -1;
  for (size_t i = 0; i < MAX_SESSIONS; ++i) {
    if (sessions[i].active && sessions[i].sessionId == sessionId) {
      return static_cast<int>(i);
    }
  }
  return -1;
}

int findFreeSlot() {
  for (size_t i = 0; i < MAX_SESSIONS; ++i) {
    if (!sessions[i].active) {
      return static_cast<int>(i);
    }
  }
  return -1;
}

size_t activeSessionCount() {
  size_t count = 0;
  for (size_t i = 0; i < MAX_SESSIONS; ++i) {
    if (sessions[i].active) ++count;
  }
  return count;
}

uint16_t allocateSessionId() {
  for (uint32_t tries = 0; tries < 65535; ++tries) {
    uint16_t candidate = nextSessionId++;
    if (nextSessionId == 0) nextSessionId = 1;
    if (candidate != 0 && findSlotBySessionId(candidate) < 0) {
      return candidate;
    }
  }
  return 0;
}

void writeU16LE(uint8_t* dst, uint16_t value) {
  dst[0] = static_cast<uint8_t>(value & 0xFF);
  dst[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
}

uint16_t readU16LE(const uint8_t* src) {
  return static_cast<uint16_t>(src[0]) | (static_cast<uint16_t>(src[1]) << 8);
}

bool enqueueTunnelFrame(uint8_t type, uint16_t sessionId, const uint8_t* payload, uint16_t length) {
  if (!tunnelReady) return false;
  if (tunnelTxBuf.freeSpace() < FRAME_HEADER_SIZE + length) {
    return false;
  }
  uint8_t hdr[FRAME_HEADER_SIZE];
  hdr[0] = type;
  writeU16LE(hdr + 1, sessionId);
  writeU16LE(hdr + 3, length);
  tunnelTxBuf.push(hdr, FRAME_HEADER_SIZE);
  if (length > 0 && payload != nullptr) {
    tunnelTxBuf.push(payload, length);
  }
  return true;
}

void closeAllSessions() {
  for (size_t i = 0; i < MAX_SESSIONS; ++i) {
    sessions[i].reset();
  }
}

void resetTunnel() {
  closeAllSessions();
  if (tunnelClient) {
    tunnelClient.stop();
  }
  tunnelReady = false;
  tunnelLastRxMs = 0;
  tunnelParser.reset();
  tunnelTxBuf.clear();
  Serial.println("[TUNNEL] Disconnected");
}

void closeSessionSlot(size_t index, bool notifyRemote) {
  if (!sessions[index].active) return;
  uint16_t sid = sessions[index].sessionId;
  bool alreadyRemoteClosed = sessions[index].remoteClosed;
  sessions[index].reset();
  if (notifyRemote && !alreadyRemoteClosed && tunnelReady) {
    enqueueTunnelFrame(FRAME_CLOSE, sid, nullptr, 0);
  }
}

void sendStaticResponseAndClose(WiFiClient& client, const char* responseText) {
  while (client.available() > 0) {
    client.read(readScratch, sizeof(readScratch));
  }
  client.setNoDelay(true);
  client.print(responseText);
  client.flush();
  client.stop();
}

void handleTunnelFrame(uint8_t type, uint16_t sessionId, const uint8_t* payload, uint16_t length) {
  switch (type) {
    case FRAME_PING:
      enqueueTunnelFrame(FRAME_PONG, 0, nullptr, 0);
      break;

    case FRAME_PONG:
      break;

    case FRAME_DATA: {
      int idx = findSlotBySessionId(sessionId);
      if (idx < 0) {
        // 既に閉じたセッション宛の遅延パケットは破棄する
        return;
      }
      SessionSlot& slot = sessions[idx];
      slot.hasTraffic = true;
      if (length > slot.txBuf.freeSpace()) {
        Serial.printf("[TUNNEL] Credit overflow on session %u\n", sessionId);
        closeSessionSlot(static_cast<size_t>(idx), true);
        return;
      }
      slot.txBuf.push(payload, length);
      totalTxBytes += length;
      break;
    }

    case FRAME_CLOSE: {
      int idx = findSlotBySessionId(sessionId);
      if (idx >= 0) {
        sessions[idx].remoteClosed = true;
      }
      break;
    }

    default:
      Serial.printf("[TUNNEL] Unexpected frame type=0x%02X\n", type);
      break;
  }
}

bool stepFrameParser(WiFiClient& sock, FrameParser& parser, bool* hasDataOut,
                     void (*onFrame)(uint8_t, uint16_t, const uint8_t*, uint16_t, void*),
                     void* ctx, bool* protocolError, bool stopAfterOneFrame = false) {
  *hasDataOut = false;
  *protocolError = false;

  int avail = sock.available();
  if (avail <= 0) return true;

  *hasDataOut = true;
  int remainingBudget = 4096;
  while (avail > 0 && remainingBudget > 0) {
    if (parser.state == FrameParser::WAIT_HEADER) {
      size_t need = FRAME_HEADER_SIZE - parser.pos;
      int n = sock.read(parser.header + parser.pos, need);
      if (n <= 0) break;
      parser.pos += static_cast<size_t>(n);
      remainingBudget -= n;
      if (parser.pos == FRAME_HEADER_SIZE) {
        parser.type = parser.header[0];
        parser.sessionId = readU16LE(parser.header + 1);
        parser.length = readU16LE(parser.header + 3);
        if (parser.length > MAX_CHUNK_SIZE) {
          *protocolError = true;
          return false;
        }
        parser.pos = 0;
        if (parser.length == 0) {
          onFrame(parser.type, parser.sessionId, nullptr, 0, ctx);
          parser.reset();
          if (stopAfterOneFrame) break;
        } else {
          parser.state = FrameParser::WAIT_PAYLOAD;
        }
      }
    } else {
      size_t need = parser.length - parser.pos;
      int n = sock.read(parser.payload + parser.pos, need);
      if (n <= 0) break;
      parser.pos += static_cast<size_t>(n);
      remainingBudget -= n;
      if (parser.pos == parser.length) {
        onFrame(parser.type, parser.sessionId, parser.payload, parser.length, ctx);
        parser.reset();
        if (stopAfterOneFrame) break;
      }
    }
    avail = sock.available();
  }
  return true;
}

void onTunnelFrameCallback(uint8_t type, uint16_t sessionId, const uint8_t* payload, uint16_t length, void*) {
  handleTunnelFrame(type, sessionId, payload, length);
}

struct PendingHandshakeCtx {
  bool done = false;
  bool accepted = false;
};

void onPendingFrameCallback(uint8_t type, uint16_t sessionId, const uint8_t* payload, uint16_t length, void* rawCtx) {
  PendingHandshakeCtx* ctx = static_cast<PendingHandshakeCtx*>(rawCtx);
  if (ctx->done) return;
  ctx->done = true;

  if (type != FRAME_HELLO || sessionId != 0) {
    ctx->accepted = false;
    return;
  }
  size_t expectedLen = strlen(TUNNEL_TOKEN);
  if (length == expectedLen && memcmp(payload, TUNNEL_TOKEN, expectedLen) == 0) {
    ctx->accepted = true;
  } else {
    ctx->accepted = false;
  }
}

void acceptNewTunnelCandidate() {
  WiFiClient candidate = acceptClient(serverServer);
  if (!candidate) return;

  candidate.setNoDelay(true);
  if (pendingTunnel) {
    pendingTunnel.stop();
  }
  pendingTunnel = candidate;
  pendingConnectedMs = millis();
  pendingParser.reset();
  Serial.printf("[TUNNEL] Incoming handshake from %s\n", pendingTunnel.remoteIP().toString().c_str());
}

void processPendingTunnel(bool* didWork) {
  if (!pendingTunnel) return;

  if (millis() - pendingConnectedMs > HANDSHAKE_TIMEOUT_MS) {
    Serial.println("[TUNNEL] Handshake timeout");
    pendingTunnel.stop();
    pendingParser.reset();
    return;
  }

  bool hasData = false;
  bool protoErr = false;
  PendingHandshakeCtx hsCtx;
  stepFrameParser(pendingTunnel, pendingParser, &hasData, onPendingFrameCallback, &hsCtx, &protoErr, true);
  if (hasData) *didWork = true;

  if (protoErr) {
    Serial.println("[TUNNEL] Handshake protocol error");
    pendingTunnel.stop();
    pendingParser.reset();
    return;
  }

  if (hsCtx.done) {
    if (hsCtx.accepted) {
      // 既存のトンネル接続があれば切り替える（サーバ再起動・再接続対応）
      if (tunnelReady || tunnelClient) {
        resetTunnel();
      }
      tunnelClient = pendingTunnel;
      pendingTunnel = WiFiClient();  // 所有権移動（stopしない）
      tunnelReady = true;
      tunnelLastRxMs = millis();
      tunnelParser.reset();
      tunnelTxBuf.clear();

      uint8_t ackPayload[4];
      writeU16LE(ackPayload, static_cast<uint16_t>(MAX_SESSIONS));
      writeU16LE(ackPayload + 2, static_cast<uint16_t>(CLIENT_TX_BUF_SIZE));
      enqueueTunnelFrame(FRAME_HELLO_ACK, 0, ackPayload, sizeof(ackPayload));
      Serial.printf("[TUNNEL] Established with %s\n", tunnelClient.remoteIP().toString().c_str());
    } else {
      uint8_t errHdr[FRAME_HEADER_SIZE];
      errHdr[0] = FRAME_HELLO_ERR;
      writeU16LE(errHdr + 1, 0);
      writeU16LE(errHdr + 3, 0);
      pendingTunnel.write(errHdr, sizeof(errHdr));
      pendingTunnel.flush();
      pendingTunnel.stop();
      Serial.println("[TUNNEL] Auth failed");
    }
    pendingParser.reset();
    return;
  }

  if (!pendingTunnel.connected() && pendingTunnel.available() <= 0) {
    pendingTunnel.stop();
    pendingParser.reset();
  }
}

void flushTunnelTx(bool* didWork) {
  if (!tunnelReady || !tunnelClient) return;

  for (int pass = 0; pass < 2 && tunnelTxBuf.size() > 0; ++pass) {
    size_t chunkLen = 0;
    const uint8_t* ptr = tunnelTxBuf.contiguousReadPtr(&chunkLen);
    if (chunkLen == 0) break;

    bool fatal = false;
    size_t sent = nonBlockingWrite(tunnelClient, ptr, chunkLen, &fatal);
    if (fatal) {
      resetTunnel();
      return;
    }
    if (sent > 0) {
      tunnelTxBuf.consume(sent);
      *didWork = true;
    }
    if (sent < chunkLen) {
      break;
    }
  }
}

void processTunnelRx(bool* didWork) {
  if (!tunnelReady || !tunnelClient) return;

  bool hasData = false;
  bool protoErr = false;
  stepFrameParser(tunnelClient, tunnelParser, &hasData, onTunnelFrameCallback, nullptr, &protoErr);
  if (hasData) {
    tunnelLastRxMs = millis();
    *didWork = true;
  }
  if (protoErr) {
    Serial.println("[TUNNEL] Protocol error");
    resetTunnel();
    return;
  }
  if (!tunnelClient.connected() && tunnelClient.available() <= 0) {
    Serial.println("[TUNNEL] Peer closed");
    resetTunnel();
    return;
  }
  if (millis() - tunnelLastRxMs > TUNNEL_IDLE_TIMEOUT_MS) {
    Serial.println("[TUNNEL] Keepalive timeout");
    resetTunnel();
    return;
  }
}

void acceptNewClients(bool* didWork) {
  for (int guard = 0; guard < 4; ++guard) {
    WiFiClient newClient = acceptClient(clientServer);
    if (!newClient) break;
    *didWork = true;

    if (!tunnelReady) {
      sendStaticResponseAndClose(newClient, HTTP_503_BODY);
      continue;
    }

    int freeIdx = findFreeSlot();
    if (freeIdx < 0) {
      sendStaticResponseAndClose(newClient, HTTP_503_BUSY_BODY);
      continue;
    }

    uint16_t sid = allocateSessionId();
    if (sid == 0) {
      sendStaticResponseAndClose(newClient, HTTP_503_BUSY_BODY);
      continue;
    }

    newClient.setNoDelay(true);
    SessionSlot& slot = sessions[freeIdx];
    slot.reset();
    slot.active = true;
    slot.sessionId = sid;
    slot.client = newClient;
    slot.openedAtMs = millis();

    if (!enqueueTunnelFrame(FRAME_OPEN, sid, nullptr, 0)) {
      slot.reset();
      sendStaticResponseAndClose(newClient, HTTP_503_BUSY_BODY);
      continue;
    }
  }
}

void processClientSessions(bool* didWork) {
  if (!tunnelReady) return;
  uint32_t now = millis();

  for (size_t i = 0; i < MAX_SESSIONS; ++i) {
    SessionSlot& slot = sessions[i];
    if (!slot.active) continue;

    // 1. サーバPCから受信済みのデータをクライアントPCへ非ブロッキング送信
    uint16_t flushedBytes = 0;
    bool slotClosed = false;
    for (int pass = 0; pass < 2 && slot.txBuf.size() > 0; ++pass) {
      size_t chunkLen = 0;
      const uint8_t* ptr = slot.txBuf.contiguousReadPtr(&chunkLen);
      if (chunkLen == 0) break;

      bool fatal = false;
      size_t sent = nonBlockingWrite(slot.client, ptr, chunkLen, &fatal);
      if (fatal) {
        closeSessionSlot(i, true);
        slotClosed = true;
        break;
      }
      if (sent > 0) {
        slot.txBuf.consume(sent);
        flushedBytes += static_cast<uint16_t>(sent);
        *didWork = true;
      }
      if (sent < chunkLen) {
        break;
      }
    }
    if (slotClosed) {
      continue;
    }
    if (flushedBytes > 0) {
      slot.pendingAck += flushedBytes;
    }

    // 2. クレジット返却 (ACKフレームは予約域を優先利用し取りこぼさない)
    if (slot.pendingAck > 0 && tunnelTxBuf.freeSpace() >= FRAME_HEADER_SIZE + 2) {
      uint8_t ackPayload[2];
      writeU16LE(ackPayload, slot.pendingAck);
      if (enqueueTunnelFrame(FRAME_ACK, slot.sessionId, ackPayload, sizeof(ackPayload))) {
        slot.pendingAck = 0;
        *didWork = true;
      }
    }

    // 3. クライアントPCからデータ読み取り -> トンネルへ送信
    if (!slot.remoteClosed) {
      int avail = slot.client.available();
      if (avail > 0) {
        size_t maxEnqueueable = 0;
        size_t freeTunnel = tunnelTxBuf.freeSpace();
        if (freeTunnel > FRAME_HEADER_SIZE + TUNNEL_TX_CTRL_RESERVE) {
          maxEnqueueable = freeTunnel - (FRAME_HEADER_SIZE + TUNNEL_TX_CTRL_RESERVE);
          if (maxEnqueueable > MAX_CHUNK_SIZE) {
            maxEnqueueable = MAX_CHUNK_SIZE;
          }
        }
        if (maxEnqueueable > 0) {
          size_t toRead = static_cast<size_t>(avail);
          if (toRead > maxEnqueueable) toRead = maxEnqueueable;
          int n = slot.client.read(readScratch, toRead);
          if (n > 0) {
            slot.hasTraffic = true;
            totalRxBytes += static_cast<uint64_t>(n);
            enqueueTunnelFrame(FRAME_DATA, slot.sessionId, readScratch, static_cast<uint16_t>(n));
            *didWork = true;
          }
        }
      }
    }

    // 4. クローズ判定
    if (slot.remoteClosed && slot.txBuf.size() == 0) {
      // バッファ内の全HTTP/WSレスポンスを送り切ってからソケットを閉じる
      closeSessionSlot(i, false);
      continue;
    }
    if (!slot.client.connected() && slot.client.available() <= 0) {
      closeSessionSlot(i, true);
      continue;
    }
    if (!slot.hasTraffic && (now - slot.openedAtMs > ZERO_TRAFFIC_TIMEOUT_MS)) {
      // 1バイトも送らず放置されたブラウザの先行接続を解放する
      closeSessionSlot(i, true);
      continue;
    }
  }
}

void drawDisplay(bool fullRedraw) {
  if (fullRedraw) {
    M5.Lcd.fillScreen(BLACK);
  }
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(CYAN, BLACK);
  M5.Lcd.setCursor(4, 4);
  M5.Lcd.printf("EIKOH AP RELAY [P%u/2]   ", uiPage + 1);

  if (uiPage == 0) {
    M5.Lcd.setTextColor(WHITE, BLACK);
    M5.Lcd.setCursor(4, 20);
    M5.Lcd.printf("SSID: %-18s", AP_SSID);
    M5.Lcd.setCursor(4, 34);
    M5.Lcd.printf("PASS: %-18s", AP_PASS);
    M5.Lcd.setCursor(4, 48);
    M5.Lcd.printf("URL : http://192.168.4.1:%u", CLIENT_PORT);

    M5.Lcd.setCursor(4, 68);
    M5.Lcd.print("SRV : ");
    if (tunnelReady) {
      M5.Lcd.setTextColor(GREEN, BLACK);
      char ipStr[24];
      snprintf(ipStr, sizeof(ipStr), "ON (%s)", tunnelClient.remoteIP().toString().c_str());
      M5.Lcd.printf("%-26s", ipStr);
    } else {
      M5.Lcd.setTextColor(YELLOW, BLACK);
      M5.Lcd.printf("WAIT PORT %-16u", SERVER_PORT);
    }

    M5.Lcd.setTextColor(WHITE, BLACK);
    M5.Lcd.setCursor(4, 84);
    M5.Lcd.printf("WiFi STA: %2d / %u  SES: %2u / %u ",
                  WiFi.softAPgetStationNum(), MAX_STA_CONN,
                  static_cast<unsigned>(activeSessionCount()),
                  static_cast<unsigned>(MAX_SESSIONS));

    M5.Lcd.setCursor(4, 100);
    M5.Lcd.printf("UP: %5llu KB  DN: %5llu KB   ",
                  static_cast<unsigned long long>(totalRxBytes / 1024ULL),
                  static_cast<unsigned long long>(totalTxBytes / 1024ULL));

    M5.Lcd.setTextColor(0xBDF7, BLACK);
    M5.Lcd.setCursor(4, 120);
    M5.Lcd.print("BtnA:Page  BtnB:Dim/Bright");
  } else {
    M5.Lcd.setTextColor(WHITE, BLACK);
    M5.Lcd.setCursor(4, 20);
    M5.Lcd.printf("FreeHeap: %6u bytes      ", ESP.getFreeHeap());
    M5.Lcd.setCursor(4, 34);
    M5.Lcd.printf("TunnelTx: %4u / %u B       ",
                  static_cast<unsigned>(tunnelTxBuf.size()),
                  static_cast<unsigned>(TUNNEL_TX_BUF_SIZE));

    for (size_t i = 0; i < 6; ++i) {
      M5.Lcd.setCursor(4, 50 + static_cast<int>(i) * 11);
      if (sessions[i].active) {
        M5.Lcd.printf("#%u ID:%5u TXQ:%4u %s ",
                      static_cast<unsigned>(i),
                      sessions[i].sessionId,
                      static_cast<unsigned>(sessions[i].txBuf.size()),
                      sessions[i].remoteClosed ? "CLOSING" : "OPEN   ");
      } else {
        M5.Lcd.printf("#%u (idle)                     ", static_cast<unsigned>(i));
      }
    }
    M5.Lcd.setTextColor(0xBDF7, BLACK);
    M5.Lcd.setCursor(4, 120);
    M5.Lcd.print("BtnA:Page  BtnB:ResetSess");
  }
}

void handleButtons() {
  M5.update();
  if (M5.BtnA.wasPressed()) {
    uiPage = (uiPage + 1) % 2;
    drawDisplay(true);
  }
  if (M5.BtnB.wasPressed()) {
    if (uiPage == 0) {
      lcdDim = !lcdDim;
      M5.Axp.ScreenBreath(lcdDim ? 7 : 11);
    } else {
      closeAllSessions();
      drawDisplay(true);
    }
  }
}

void setup() {
  M5.begin();
  M5.Lcd.setRotation(1);
  M5.Axp.ScreenBreath(11);
  Serial.begin(115200);

  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS, AP_CHANNEL, 0, MAX_STA_CONN);
  delay(100);
  WiFi.softAPConfig(AP_IP, AP_GATEWAY, AP_SUBNET);
  WiFi.setSleep(false);

  clientServer.begin();
  serverServer.begin();

  Serial.println("=== Eikoh M5StickCPlus 1.1 Relay Started ===");
  Serial.printf("AP SSID: %s  IP: %s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
  Serial.printf("Client Port: %u  Server Tunnel Port: %u\n", CLIENT_PORT, SERVER_PORT);

  drawDisplay(true);
}

void loop() {
  bool didWork = false;

  acceptNewTunnelCandidate();
  processPendingTunnel(&didWork);
  processTunnelRx(&didWork);
  acceptNewClients(&didWork);
  processClientSessions(&didWork);
  flushTunnelTx(&didWork);

  handleButtons();

  uint32_t now = millis();
  if (now - lastUiUpdateMs >= UI_REFRESH_INTERVAL_MS) {
    lastUiUpdateMs = now;
    drawDisplay(false);
  }

  if (!didWork) {
    delay(1);
  }
}
