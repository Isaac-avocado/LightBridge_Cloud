#include <ESP8266WiFi.h>

void setup() {
  Serial.begin(115200);
  delay(200);
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  WiFi.setAutoReconnect(false);
  Serial.println("ESP8266 station MAC (register this MAC in the backend):");
  Serial.println(WiFi.macAddress());
  // No WiFi.begin(): no association or Internet connection.
}
void loop() { delay(1000); }
