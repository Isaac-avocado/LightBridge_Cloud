#include <ESP8266WiFi.h>
extern "C" {
#include <espnow.h>
#include <user_interface.h>
}
#include <stddef.h>
#include "config.h"

// Wire layout v1: identical declaration in esp32_gateway.ino; little endian.
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
static_assert(DEBOUNCE_MS > 0 && SAMPLE_WINDOW_MS > DEBOUNCE_MS, "Invalid sampling times");
static_assert(REPORT_INTERVAL_MS >= SAMPLE_WINDOW_MS, "Report interval too small");
static_assert(DEEP_SLEEP_SECONDS > 0 && SEND_TIMEOUT_MS > 0, "Invalid sleep/send time");
static_assert(LDR_DARK_LEVEL == HIGH || LDR_DARK_LEVEL == LOW, "Invalid LDR polarity");

// Explicit declarations avoid generated prototypes before the wire enum definitions.
const char *alertName(AlertType alert);
bool sendReport(LdrState ldr, AlertType alert, uint16_t flickerCount);

volatile bool sendFinished = false;
volatile uint8_t sendStatus = 1;
bool radioReady = false;
bool hasReport = false;
uint8_t candidateLevel = HIGH;
uint8_t stableLevel = HIGH;
uint32_t candidateSince = 0;
uint32_t windowStarted = 0;
uint32_t lastReportAt = 0;
uint32_t packetCounter = 0;  // Diagnostic only; resets on deep-sleep wake.
uint16_t transitions = 0;
LdrState lastLdr = LdrState::LIGHT;
AlertType lastAlert = AlertType::NORMAL;

const char *alertName(AlertType alert) {
  switch (alert) {
    case AlertType::APAGADA_CRITICA: return "APAGADA_CRITICA";
    case AlertType::PARPADEO_LDR: return "PARPADEO_LDR";
    default: return "NORMAL";
  }
}

void onSent(uint8_t *mac, uint8_t status) {
  (void)mac;
  sendStatus = status;
  sendFinished = true;  // No Serial or slow operations inside Wi-Fi callback.
}

void sleepNow() {
  Serial.printf("Deep Sleep: %lu s (D0/GPIO16 must be wired to RST)\n",
                static_cast<unsigned long>(DEEP_SLEEP_SECONDS));
  Serial.flush();
  ESP.deepSleep(static_cast<uint64_t>(DEEP_SLEEP_SECONDS) * 1000000ULL, WAKE_RF_DEFAULT);
  // Never continue sending if sleep cannot be entered.
  while (true) delay(100);
}

bool sendReport(LdrState ldr, AlertType alert, uint16_t flickerCount) {
  SensorPacket packet = {1, ldr, alert, flickerCount, millis(), ++packetCounter};
  Serial.printf("LDR=%s alert=%s transitions=%u packet=%lu uptime=%lu\n",
                ldr == LdrState::DARK ? "DARK" : "LIGHT", alertName(alert),
                static_cast<unsigned>(flickerCount),
                static_cast<unsigned long>(packet.packet_counter),
                static_cast<unsigned long>(packet.uptime_ms));
  if (!radioReady) { Serial.println("ESP-NOW unavailable; report not sent"); return false; }
  sendFinished = false;
  if (esp_now_send(GATEWAY_MAC, reinterpret_cast<uint8_t *>(&packet), sizeof(packet)) != 0) {
    Serial.println("ESP-NOW send rejected locally");
    return false;
  }
  const uint32_t started = millis();
  while (!sendFinished && static_cast<uint32_t>(millis() - started) < SEND_TIMEOUT_MS) delay(1);
  if (!sendFinished) {
    // Stop this radio instance: a late callback must not acknowledge another report.
    esp_now_unregister_send_cb();
    esp_now_deinit();
    radioReady = false;
    Serial.println("ESP-NOW send timeout; radio disabled until reboot");
    return false;
  }
  Serial.println(sendStatus == 0 ? "ESP-NOW link delivery OK (not an HTTP acknowledgement)"
                                : "ESP-NOW link delivery FAILED");
  return sendStatus == 0;
}

void setup() {
  Serial.begin(115200);
  delay(100);
  pinMode(LDR_PIN, INPUT);
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();  // No WiFi.begin(): ESP-NOW only; do not associate to an AP.
  WiFi.setAutoReconnect(false);
  delay(10);
  const bool channelSet = wifi_set_channel(WIFI_CHANNEL);
  Serial.printf("%s STA MAC=%s channel=%u mode=%s DO=%u\n", NODE_LABEL,
                WiFi.macAddress().c_str(), static_cast<unsigned>(wifi_get_channel()),
                DEMO_MODE ? "DEMO awake" : "POWER SAVE", digitalRead(LDR_PIN));
  bool validMac = !(GATEWAY_MAC[0] & 1);
  bool anyByte = false;
  for (uint8_t value : GATEWAY_MAC) anyByte |= value != 0;
  if (channelSet && validMac && anyByte && esp_now_init() == 0) {
    radioReady = esp_now_set_self_role(ESP_NOW_ROLE_CONTROLLER) == 0 &&
                 esp_now_register_send_cb(onSent) == 0 &&
                 esp_now_add_peer(GATEWAY_MAC, ESP_NOW_ROLE_SLAVE, WIFI_CHANNEL, nullptr, 0) == 0;
  }
  if (!radioReady) Serial.println("ERROR: ESP-NOW initialization/configuration failed");
  candidateLevel = stableLevel = digitalRead(LDR_PIN);
  candidateSince = windowStarted = millis();
}

void loop() {
  const uint32_t now = millis();
  const uint8_t raw = digitalRead(LDR_PIN);
  if (raw != candidateLevel) { candidateLevel = raw; candidateSince = now; }
  if (candidateLevel != stableLevel && static_cast<uint32_t>(now - candidateSince) >= DEBOUNCE_MS) {
    stableLevel = candidateLevel;
    if (transitions < UINT16_MAX) ++transitions;
  }
  if (static_cast<uint32_t>(now - windowStarted) >= SAMPLE_WINDOW_MS) {
    const LdrState ldr = stableLevel == LDR_DARK_LEVEL ? LdrState::DARK : LdrState::LIGHT;
    const AlertType alert = transitions > FLICKER_THRESHOLD ? AlertType::PARPADEO_LDR
        : (ldr == LdrState::DARK ? AlertType::APAGADA_CRITICA : AlertType::NORMAL);
    const bool changed = !hasReport || alert != lastAlert || ldr != lastLdr;
    const bool periodic = static_cast<uint32_t>(now - lastReportAt) >= REPORT_INTERVAL_MS;
    if (!DEMO_MODE || changed || periodic) {
      sendReport(ldr, alert, transitions);
      hasReport = true;
      lastAlert = alert;
      lastLdr = ldr;
      lastReportAt = millis();
    }
    if (!DEMO_MODE) sleepNow();
    // Non-overlapping sampling windows. Classification latency <= one window.
    transitions = 0;
    windowStarted = millis();
  }
  delay(1);  // Yield for the ESP8266 radio/watchdog.
}
