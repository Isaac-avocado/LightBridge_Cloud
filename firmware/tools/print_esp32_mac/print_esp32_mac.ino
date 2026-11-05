#include <WiFi.h>

void setup() {
  Serial.begin(115200);
  delay(200);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.disconnect(false, false);
  Serial.println("ESP32 station MAC (set GATEWAY_MAC on BOTH ESP8266):");
  Serial.println(WiFi.macAddress());
  // No WiFi.begin(): no association or Internet connection.
}
void loop() { delay(1000); }
