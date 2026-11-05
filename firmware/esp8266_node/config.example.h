#pragma once
#include <Arduino.h>

// Copy to config.h (ignored). Replace with the ESP32 station MAC, not its AP MAC.
static uint8_t GATEWAY_MAC[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x03};
constexpr uint8_t WIFI_CHANNEL = 6;
constexpr uint8_t LDR_PIN = D1;  // Wemos D1 Mini D1 == GPIO5.
constexpr uint8_t LDR_DARK_LEVEL = LOW;  // Calibrate using Serial and your module.
constexpr bool DEMO_MODE = true;
constexpr uint32_t SAMPLE_WINDOW_MS = 2000;
constexpr uint32_t REPORT_INTERVAL_MS = 10000;
constexpr uint32_t DEEP_SLEEP_SECONDS = 60;
constexpr uint16_t FLICKER_THRESHOLD = 6;  // Strictly MORE than this count.
constexpr uint32_t DEBOUNCE_MS = 50;
constexpr uint32_t SEND_TIMEOUT_MS = 1500;
constexpr char NODE_LABEL[] = "Wemos-LDR";  // Diagnostic only; never a backend ID.
