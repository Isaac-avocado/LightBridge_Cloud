#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_arduino_version.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <stddef.h>
#include <string.h>
#include "config.h"

#if ESP_ARDUINO_VERSION_MAJOR != 2 && ESP_ARDUINO_VERSION_MAJOR != 3
#error "This sketch targets Arduino-ESP32 2.x/3.x, not 4.x prereleases"
#endif

// Wire layout v1: identical declaration in esp8266_node.ino; little endian.
enum class LdrState : uint8_t { LIGHT = 0, DARK = 1 };
enum class AlertType : uint8_t { NORMAL = 0, APAGADA_CRITICA = 1, PARPADEO_LDR = 2 };
struct __attribute__((packed)) SensorPacket {
  uint8_t protocol_version;
  LdrState ldr_state;
  AlertType alert_type;
  uint16_t flicker_count;
  uint32_t uptime_ms;
  uint32_t packet_counter;
};
static_assert(sizeof(SensorPacket) == 13, "ESP-NOW wire packet must be 13 bytes");
static_assert(offsetof(SensorPacket, flicker_count) == 3, "Wire layout mismatch");
static_assert(offsetof(SensorPacket, uptime_ms) == 5, "Wire layout mismatch");
static_assert(offsetof(SensorPacket, packet_counter) == 9, "Wire layout mismatch");
static_assert(WIFI_CHANNEL >= 1 && WIFI_CHANNEL <= 13, "Invalid Wi-Fi channel");
static_assert(EVENT_QUEUE_LENGTH > 0 && MAX_HTTP_ATTEMPTS > 0, "Invalid queue/retry config");
static_assert(SEQUENCE_BLOCK_SIZE > 0 && SEQUENCE_START >= 1 && SEQUENCE_START <= UINT32_MAX, "Invalid sequence config");
static_assert(HTTP_TIMEOUT_MS > 0 && HTTP_CONNECT_TIMEOUT_MS > 0, "HTTP timeouts required");
static_assert(RETRY_BASE_MS > 0 && RETRY_MAX_MS >= RETRY_BASE_MS, "Invalid backoff");
static_assert(MAX_EVENT_AGE_MS > 0 && MAX_EVENT_AGE_MS < 0x80000000UL, "Invalid event lifetime");
static_assert(WIFI_CONNECT_TIMEOUT_MS > 0 && WIFI_RECONNECT_INTERVAL_MS > 0, "Wi-Fi timeouts required");

struct ReceivedEvent {
  SensorPacket packet;
  uint8_t mac[6];
  uint32_t receivedAt;
};
// Explicit declarations keep Arduino's generated prototypes after the custom types.
const char *alertName(AlertType alert);
String makeJson(const ReceivedEvent &event, uint32_t sequence);

QueueHandle_t events = nullptr;
portMUX_TYPE counterMux = portMUX_INITIALIZER_UNLOCKED;
uint32_t droppedPackets = 0;
uint32_t invalidPackets = 0;
Preferences preferences;
bool storageReady = false;
bool espNowReady = false;
bool havePending = false;
ReceivedEvent pendingEvent = {};
String pendingJson;
uint32_t pendingSequence = 0;
uint8_t pendingAttempts = 0;
uint32_t retryAt = 0;
uint64_t nextSequence = 0;
uint64_t reservedEnd = 0;
uint32_t connectStarted = 0;
uint32_t lastConnectAttempt = 0;
bool connecting = false;
bool wasConnected = false;
uint8_t radioChannel = WIFI_CHANNEL;
uint32_t lastStatsAt = 0;

const char *alertName(AlertType alert) {
  switch (alert) {
    case AlertType::APAGADA_CRITICA: return "APAGADA_CRITICA";
    case AlertType::PARPADEO_LDR: return "PARPADEO_LDR";
    default: return "NORMAL";
  }
}

void countDiscard(bool invalid) {
  portENTER_CRITICAL(&counterMux);
  if (invalid) ++invalidPackets;
  else ++droppedPackets;
  portEXIT_CRITICAL(&counterMux);
}

bool allowedMac(const uint8_t *mac) {
  if (!ENFORCE_NODE_ALLOWLIST) return true;
  for (const auto &allowed : AUTHORIZED_NODE_MACS) {
    if (memcmp(allowed, mac, 6) == 0) return true;
  }
  return false;
}

void receivePacket(const uint8_t *mac, const uint8_t *data, int length) {
  if (!events || !mac || !data || length != sizeof(SensorPacket) || !allowedMac(mac)) {
    countDiscard(true);
    return;
  }
  ReceivedEvent event = {};
  memcpy(&event.packet, data, sizeof(event.packet));
  if (event.packet.protocol_version != 1 ||
      static_cast<uint8_t>(event.packet.ldr_state) > 1 ||
      static_cast<uint8_t>(event.packet.alert_type) > 2) {
    countDiscard(true);
    return;
  }
  memcpy(event.mac, mac, sizeof(event.mac));  // Source metadata, never payload.
  event.receivedAt = millis();
  // Wi-Fi task callback, not an ISR. Never block, HTTP, Serial or NVS here.
  if (xQueueSend(events, &event, 0) != pdTRUE) countDiscard(false);
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
void onReceive(const esp_now_recv_info_t *info, const uint8_t *data, int length) {
  receivePacket(info ? info->src_addr : nullptr, data, length);
}
#else
void onReceive(const uint8_t *mac, const uint8_t *data, int length) {
  receivePacket(mac, data, length);
}
#endif

bool reserveBlock() {
  const uint64_t limit = static_cast<uint64_t>(UINT32_MAX) + 1;
  if (reservedEnd >= limit) return false;
  const uint64_t start = reservedEnd;
  const uint64_t end = start + (SEQUENCE_BLOCK_SIZE < limit - start ? SEQUENCE_BLOCK_SIZE : limit - start);
  // Commit the exclusive upper bound BEFORE handing out any number in this block.
  if (preferences.putULong64("next_block", end) != sizeof(uint64_t) ||
      preferences.getULong64("next_block", 0) != end) return false;
  nextSequence = start;
  reservedEnd = end;
  return true;
}

bool initializeSequences() {
  if (!preferences.begin("lb-physical", false)) return false;
  const bool exists = preferences.isKey("next_block");
  if (exists && preferences.getType("next_block") != PT_U64) return false;
  const uint64_t stored = exists ? preferences.getULong64("next_block", UINT64_MAX) : SEQUENCE_START;
  if (stored < 1 || stored > static_cast<uint64_t>(UINT32_MAX) + 1) return false;
  reservedEnd = stored > SEQUENCE_START ? stored : SEQUENCE_START;
  return reserveBlock();  // On reboot discard all unused numbers in previous block.
}

bool allocateSequence(uint32_t &result) {
  if (!storageReady) return false;
  if (nextSequence == reservedEnd && !reserveBlock()) {
    storageReady = false;  // Fail closed; never fall back to a RAM-only sequence.
    return false;
  }
  result = static_cast<uint32_t>(nextSequence++);
  return true;
}

String jsonQuoted(const char *value) {
  String result = "\"";
  for (const unsigned char *p = reinterpret_cast<const unsigned char *>(value); *p; ++p) {
    if (*p == '"' || *p == '\\') { result += '\\'; result += static_cast<char>(*p); }
    else if (*p < 0x20) {
      char escaped[7];
      snprintf(escaped, sizeof(escaped), "\\u%04x", static_cast<unsigned>(*p));
      result += escaped;
    } else result += static_cast<char>(*p);
  }
  result += '"';
  return result;
}

String makeJson(const ReceivedEvent &event, uint32_t sequence) {
  char mac[18];
  snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
           event.mac[0], event.mac[1], event.mac[2], event.mac[3], event.mac[4], event.mac[5]);
  String json;
  json.reserve(320);
  json = "{\"protocol_version\":1,\"gateway_id\":";
  json += jsonQuoted(GATEWAY_ID);
  json += ",\"node_mac\":"; json += jsonQuoted(mac);
  json += ",\"ldr_state\":"; json += jsonQuoted(event.packet.ldr_state == LdrState::LIGHT ? "LIGHT" : "DARK");
  json += ",\"alert_type\":"; json += jsonQuoted(alertName(event.packet.alert_type));
  json += ",\"flicker_count\":"; json += String(event.packet.flicker_count);
  json += ",\"sequence\":"; json += String(sequence);
  json += ",\"uptime_ms\":"; json += String(event.packet.uptime_ms);
  json += '}';
  return json;
}

bool validConfiguration() {
  if (!WIFI_SSID[0] || strncmp(WIFI_SSID, "CHANGE_ME", 9) == 0 ||
      !PHYSICAL_API_KEY[0] || strncmp(PHYSICAL_API_KEY, "CHANGE_ME", 9) == 0) return false;
  if (strncmp(BACKEND_URL, "http://", 7) != 0) return false;  // LAN prototype only.
  const size_t length = strlen(GATEWAY_ID);
  if (length == 0 || length > 64) return false;
  for (const char *p = GATEWAY_ID; *p; ++p) {
    if (!((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') ||
          (*p >= '0' && *p <= '9') || *p == '-' || *p == '_')) return false;
  }
  return true;
}

void updateConnectedChannel() {
  const uint8_t actual = WiFi.channel();
  if (actual < 1 || actual > 13) return;
  if (actual != radioChannel || !wasConnected) {
    Serial.printf("Wi-Fi IP=%s actual_channel=%u\n", WiFi.localIP().toString().c_str(), actual);
    if (actual != WIFI_CHANNEL) {
      Serial.printf("WARNING: configured channel %u differs. ESP-NOW uses AP channel %u; update BOTH ESP8266.\n", WIFI_CHANNEL, actual);
    }
    radioChannel = actual;  // Associated radio is already on this channel; do not force it.
  }
}

void maintainWifi() {
  const uint32_t now = millis();
  if (WiFi.status() == WL_CONNECTED) {
    updateConnectedChannel();
    wasConnected = true;
    connecting = false;
    return;
  }
  if (wasConnected) {
    Serial.println("Wi-Fi disconnected; ESP-NOW remains initialized");
    wasConnected = false;
  }
  if (connecting && static_cast<uint32_t>(now - connectStarted) >= WIFI_CONNECT_TIMEOUT_MS) {
    WiFi.disconnect(false, false);  // Do not power off radio or erase credentials.
    connecting = false;
    lastConnectAttempt = now;  // Keep a listening interval after a failed attempt.
    esp_wifi_set_channel(radioChannel, WIFI_SECOND_CHAN_NONE);
    Serial.println("Wi-Fi connection timeout; listening again on last channel");
  }
  if (!connecting && static_cast<uint32_t>(now - lastConnectAttempt) >= WIFI_RECONNECT_INTERVAL_MS) {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD, radioChannel);
    lastConnectAttempt = connectStarted = now;
    connecting = true;  // Return immediately; callbacks still enqueue during HTTP/reconnect.
    Serial.println("Wi-Fi reconnect started (bounded; channel changes may lose packets)");
  }
}

bool temporaryHttpError(int code) {
  return code < 0 || code == 408 || code == 429 || code >= 500;
}

void postPending() {
  if (WiFi.status() != WL_CONNECTED || static_cast<int32_t>(millis() - retryAt) < 0) return;
  WiFiClient client;
  HTTPClient http;
  int code = -1;
  http.setConnectTimeout(HTTP_CONNECT_TIMEOUT_MS);
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setReuse(false);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);  // Never forward device key to another URL.
  ++pendingAttempts;
  if (http.begin(client, BACKEND_URL)) {
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Key", PHYSICAL_API_KEY);
    code = http.POST(pendingJson);
  }
  http.end();  // Do not print response body, key, SSID/password or request headers.
  Serial.printf("HTTP=%d sequence=%lu attempt=%u\n", code,
                static_cast<unsigned long>(pendingSequence), pendingAttempts);
  if (code == 201 || code == 200) {
    Serial.println(code == 201 ? "accepted" : "duplicate");
    havePending = false;
  } else if (!temporaryHttpError(code)) {
    Serial.println("Permanent HTTP/config error; discard this event, check backend registration/key/contract");
    havePending = false;
  } else if (pendingAttempts >= MAX_HTTP_ATTEMPTS) {
    Serial.println("HTTP retry limit reached; event discarded");
    havePending = false;
  } else {
    uint32_t backoff = static_cast<uint32_t>(pendingAttempts) * RETRY_BASE_MS;
    if (backoff > RETRY_MAX_MS) backoff = RETRY_MAX_MS;
    retryAt = millis() + backoff;
    Serial.printf("Temporary failure; retry in %lu ms with SAME sequence\n", static_cast<unsigned long>(backoff));
  }
  if (!havePending) pendingJson = String();
}

void setup() {
  Serial.begin(115200);
  delay(100);
  if (!validConfiguration()) {
    Serial.println("ERROR: copy config.example.h to config.h and configure Wi-Fi, backend, key and gateway ID");
    return;
  }
  events = xQueueCreate(EVENT_QUEUE_LENGTH, sizeof(ReceivedEvent));
  storageReady = initializeSequences();
  if (!events || !storageReady) {
    Serial.println("ERROR: queue/NVS unavailable or sequence exhausted; no events will be forwarded");
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.setSleep(false);
  Serial.printf("Gateway STA MAC=%s\n", WiFi.macAddress().c_str());
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, WIFI_CHANNEL);
  lastConnectAttempt = connectStarted = millis();
  connecting = true;
  while (WiFi.status() != WL_CONNECTED &&
         static_cast<uint32_t>(millis() - connectStarted) < WIFI_CONNECT_TIMEOUT_MS) delay(20);
  if (WiFi.status() == WL_CONNECTED) {
    updateConnectedChannel();
    wasConnected = true;
    connecting = false;
  } else {
    WiFi.disconnect(false, false);
    connecting = false;
    lastConnectAttempt = millis();  // Do not immediately restart scanning after setup timeout.
    esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    Serial.println("Wi-Fi unavailable; ESP-NOW starts on configured channel, reconnect scheduled");
  }
  // Initialize ESP-NOW only after the initial channel has been established.
  espNowReady = esp_now_init() == ESP_OK && esp_now_register_recv_cb(onReceive) == ESP_OK;
  if (!espNowReady) Serial.println("ERROR: ESP-NOW initialization failed");
}

void loop() {
  if (!espNowReady || !storageReady) { delay(100); return; }
  maintainWifi();
  if (!havePending && xQueueReceive(events, &pendingEvent, 0) == pdTRUE) {
    if (static_cast<uint32_t>(millis() - pendingEvent.receivedAt) >= MAX_EVENT_AGE_MS) {
      Serial.println("Stale queued observation discarded");
    } else if (!allocateSequence(pendingSequence)) {
      Serial.println("ERROR: sequence reservation failed; forwarding stopped");
    } else {
      pendingJson = makeJson(pendingEvent, pendingSequence);
      havePending = true;
      pendingAttempts = 0;
      retryAt = millis();
      Serial.printf("From %02X:%02X:%02X:%02X:%02X:%02X alert=%s sequence=%lu packet=%lu\n",
                    pendingEvent.mac[0], pendingEvent.mac[1], pendingEvent.mac[2],
                    pendingEvent.mac[3], pendingEvent.mac[4], pendingEvent.mac[5],
                    alertName(pendingEvent.packet.alert_type),
                    static_cast<unsigned long>(pendingSequence),
                    static_cast<unsigned long>(pendingEvent.packet.packet_counter));
    }
  }
  if (havePending) {
    if (static_cast<uint32_t>(millis() - pendingEvent.receivedAt) >= MAX_EVENT_AGE_MS) {
      havePending = false;
      pendingJson = String();
      Serial.println("Pending event expired; discarded without reusing sequence");
    } else postPending();
  }
  if (static_cast<uint32_t>(millis() - lastStatsAt) >= 10000) {
    uint32_t dropped, invalid;
    portENTER_CRITICAL(&counterMux);
    dropped = droppedPackets;
    invalid = invalidPackets;
    portEXIT_CRITICAL(&counterMux);
    Serial.printf("Queue drops=%lu invalid/unauthorized=%lu\n",
                  static_cast<unsigned long>(dropped), static_cast<unsigned long>(invalid));
    lastStatsAt = millis();
  }
  delay(2);
}
