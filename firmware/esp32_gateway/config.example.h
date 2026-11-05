#pragma once
#include <Arduino.h>

// Copy to config.h (ignored). These are placeholders, never real credentials.
constexpr char WIFI_SSID[] = "CHANGE_ME_SSID";
constexpr char WIFI_PASSWORD[] = "CHANGE_ME_WIFI_PASSWORD";
constexpr uint8_t WIFI_CHANNEL = 6;  // Match both ESP8266; real AP channel wins.
constexpr char BACKEND_URL[] = "http://192.168.1.100:5000/api/physical/events";
constexpr char PHYSICAL_API_KEY[] = "CHANGE_ME_DEVICE_KEY";
constexpr char GATEWAY_ID[] = "esp32-master-01";
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 15000;
constexpr uint32_t WIFI_RECONNECT_INTERVAL_MS = 15000;
constexpr uint16_t HTTP_CONNECT_TIMEOUT_MS = 2000;
constexpr uint16_t HTTP_TIMEOUT_MS = 3000;
constexpr uint8_t MAX_HTTP_ATTEMPTS = 3;
constexpr uint32_t RETRY_BASE_MS = 1000;
constexpr uint32_t RETRY_MAX_MS = 5000;
constexpr uint32_t MAX_EVENT_AGE_MS = 20000;
constexpr uint16_t EVENT_QUEUE_LENGTH = 16;
constexpr uint64_t SEQUENCE_BLOCK_SIZE = 100;
// First sequence floor, not an automatic reset. Raise above backend MAX(sequence)
// if provisioning a replacement gateway or after loss/erasure of its NVS.
constexpr uint64_t SEQUENCE_START = 10000;
// Enable with real station MACs once known. Empty examples accept valid v1 packets.
constexpr bool ENFORCE_NODE_ALLOWLIST = false;
static const uint8_t AUTHORIZED_NODE_MACS[][6] = {
  {0x02, 0x00, 0x00, 0x00, 0x00, 0x01},
  {0x02, 0x00, 0x00, 0x00, 0x00, 0x02}
};
