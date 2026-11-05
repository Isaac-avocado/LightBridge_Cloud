# Light Bridge — firmware base de Fase 3

Este firmware conecta dos Wemos D1 Mini con LDR digital al gateway ESP32 WROOM-32:

```text
ESP8266 (ESP-NOW) -> ESP32 (Wi-Fi + HTTP) -> Flask/SQLite -> dashboard
```

Es una base de prototipo con compilación real validada en los cores indicados;
la validación con hardware real sigue pendiente. Consulta COMPILACION.md para
los resultados, opciones y límites de esa validación. No incluye credenciales reales ni cambia el backend.
Ambos Wemos ejecutan exactamente el mismo sketch: no llevan node_id ni necesitan
un identificador lógico diferente. Su MAC física STA distingue cada dispositivo.

## Versiones y dependencias

Objetivos de compatibilidad:

- ESP8266 Arduino Core **3.1.2**, placa **LOLIN(WEMOS) D1 R2 & mini**,
  FQBN `esp8266:esp8266:d1_mini`.
- Arduino-ESP32 **3.3.12**, placa **ESP32 Dev Module** para WROOM-32,
  FQBN `esp32:esp32:esp32`.
- Rama alternativa ESP32 **2.0.17**: el sketch selecciona por macro la firma
  antigua del callback. No se garantiza cualquier versión histórica 2.x/3.x.
- ESP32 4.x está fuera del alcance y genera un error explícito de compilación.

Solo se usan bibliotecas de los cores: ESP8266WiFi/espnow/user_interface y
WiFi/esp_now/esp_wifi/HTTPClient/Preferences/FreeRTOS. No se usa ArduinoJson.
ESP8266 3.1.2 y ESP32 3.3.12 se compilaron en Docker con Arduino CLI 1.5.1.
ESP32 2.0.17 conserva la rama alternativa de callback, pero no se compiló en esta
validación y no debe darse por certificado.

Referencias oficiales consultadas:

- [Core ESP8266 3.1.2](https://github.com/esp8266/Arduino/releases/tag/3.1.2)
- [API espnow ESP8266 3.1.2](https://github.com/esp8266/Arduino/blob/3.1.2/tools/sdk/include/espnow.h)
- [Core ESP32 3.3.12](https://github.com/espressif/arduino-esp32/releases/tag/3.3.12)
- [ESP-NOW y callbacks en la tarea Wi-Fi](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32/api-reference/network/esp_now.html)
- [Preferences](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/preferences.html)

## Preparación paso a paso

1. En Arduino IDE, abre Preferencias y agrega estas URLs al gestor de placas:

   ```text
   https://arduino.esp8266.com/stable/package_esp8266com_index.json
   https://espressif.github.io/arduino-esp32/package_esp32_index.json
   ```

   Instala ESP8266 3.1.2 y ESP32 3.3.12 desde el gestor, en tu equipo de desarrollo.
   Esta tarea no instala software global automáticamente.

2. Selecciona D1 R2 & mini para los Wemos y ESP32 Dev Module para el gateway.
   Selecciona el puerto USB correspondiente en cada carga. Abre Serial a 115200.
   El diagnóstico ROM del ESP8266 puede aparecer ilegible antes del sketch.

3. Obtén las **tres MAC STA reales** antes de preparar los sketches principales:

   - Carga `tools/print_esp8266_mac/print_esp8266_mac.ino` en el Wemos 1.
   - Repite en el Wemos 2 y anota cuál corresponde al nodo 1 y al 25.
   - Carga `tools/print_esp32_mac/print_esp32_mac.ino` en el ESP32.

   Estas herramientas solo activan la interfaz de estación para leer su MAC;
   no llaman WiFi.begin ni se conectan al router o a Internet. No uses la MAC
   SoftAP, Bluetooth o la MAC del adaptador de la computadora.

4. Crea las configuraciones privadas en tu copia local:

   ```powershell
   Copy-Item firmware/esp8266_node/config.example.h firmware/esp8266_node/config.h
   Copy-Item firmware/esp32_gateway/config.example.h firmware/esp32_gateway/config.h
   ```

   No sobrescribas un config.h existente sin revisarlo. Los dos config.h están
   ignorados; config.example.h sí puede versionarse. No están creados en esta
   entrega. Un sketch principal sin config.h falla de manera explícita al compilar.

5. En el config.h del Wemos, configura `GATEWAY_MAC` con los seis bytes de la MAC
   STA del ESP32. Ejemplo de formato, **no una MAC real**:
   `{0x02,0x00,0x00,0x00,0x00,0x03}`. Esa dirección provisional no funcionará
   hasta sustituirla. Los dos Wemos pueden compartir la misma configuración.

6. En el ESP32 configura `WIFI_SSID`, `WIFI_PASSWORD`, `BACKEND_URL`,
   `PHYSICAL_API_KEY` y `GATEWAY_ID`. Usa la IP LAN de la PC en BACKEND_URL,
   terminando en `/api/physical/events`; no uses localhost ni la IP del contenedor.
   La clave debe coincidir exactamente con el backend. No se imprime por Serial.
   El ESP32 rechaza placeholders de SSID/clave; la configuración constante evita
   mantener una copia adicional del secreto en String. HTTPClient internamente
   sí construye las cabeceras durante la petición.

7. Configura inicialmente `WIFI_CHANNEL` igual en los tres equipos. Carga primero
   el ESP32: Serial muestra IP y **canal real del router**. Si difiere del configurado,
   usa ese canal real en ambos Wemos y vuelve a cargarlos. El ESP32 no fuerza otro
   canal mientras está asociado al AP. Se recomienda fijar el canal de 2.4 GHz
   en el router; un cambio automático puede dejar a los Wemos sin comunicación.

8. En el `.env` privado del backend registra las MAC reales:

   ```dotenv
   PHYSICAL_GATEWAY_ID=esp32-master-01
   PHYSICAL_NODE_1_MAC=MAC_STA_REAL_WEMOS_1
   PHYSICAL_NODE_1_ID=1
   PHYSICAL_NODE_2_MAC=MAC_STA_REAL_WEMOS_2
   PHYSICAL_NODE_2_ID=25
   PHYSICAL_API_KEY=CLAVE_PRIVADA_COMPARTIDA_CON_ESP32
   ```

   Son placeholders: no pegarlos literalmente ni versionar el archivo. Si no
   existe .env, parte de `.env.example`; conserva el existente si ya lo tienes.
   No muestres `docker compose config` completo con una clave real.
   Para reservar secuencias en una instalación con historial, lee primero el
   apartado de secuencias de este documento.

9. Permite entrada TCP 5000 en Firewall de Windows para red privada,
   preferiblemente solo desde la IP del gateway. PC y ESP32 deben compartir una
   LAN que permita comunicación entre clientes. No abras el puerto en el router.

10. Desde la raíz del proyecto, ejecuta:

    ```powershell
    docker compose up --build -d
    ```

    Abre `http://localhost:5000/` en la PC. Desde otro equipo usa la IP LAN de
    la PC. `/api/health` debe indicar SQLite y modo local.

11. Carga primero el sketch del gateway con la configuración lista. Activa luego
    `ENFORCE_NODE_ALLOWLIST=true` y configura las dos MAC STA en
    `AUTHORIZED_NODE_MACS` para limitar la recepción del prototipo a tus Wemos.
    El ejemplo la deja desactivada: acepta paquetes de formato válido y el backend
    sigue rechazando MAC no registradas. Una lista MAC no es autenticación criptográfica.
    Carga después el mismo sketch esp8266_node en los dos Wemos.

12. Conecta el LDR digital: DO a **D1/GPIO5**, masa común y alimentación/señales
    compatibles con **3.3 V**. No apliques una salida DO de 5 V al ESP8266.
    **D0/GPIO16 -> RST** debe estar conectado para despertar de Deep Sleep.
    Revisa la placa, el módulo LDR y el sistema de carga solar antes de alimentar
    desde batería; este firmware no mide tensión ni controla la carga.

13. Con `DEMO_MODE=true`, ilumina y cubre el sensor. La lectura se filtra por
    DEBOUNCE_MS y se clasifica al terminar cada SAMPLE_WINDOW_MS (2 s de ejemplo).
    Debe aparecer NORMAL con luz, APAGADA_CRITICA con oscuridad o PARPADEO_LDR
    si hay **más** de FLICKER_THRESHOLD transiciones válidas en esa ventana.
    Cambia luz/oscuridad suficientes veces; pulsos menores al antirrebote se ignoran.
    Reporta al cambiar alerta/lectura y periódicamente cada REPORT_INTERVAL_MS
    (10 s, evaluado al cierre de ventana). El flicker_count pertenece a la última
    ventana, no es acumulado. El Serial del Wemos confirma entrega de enlace;
    únicamente HTTP 201/200 en el gateway confirma recepción del backend.

14. El modo oscuro del dashboard es independiente del nivel del LDR. NORMAL
    repara el nodo físico; no se envía NO_ENCENDIO_RELE porque no hay relé.
    Esta demo supone una lámpara que debe permanecer encendida: oscuridad ambiental,
    sombras o luz solar pueden confundir esa interpretación. Calibra el umbral
    mecánico del módulo y `LDR_DARK_LEVEL` con el Serial.

15. Para ahorro, establece `DEMO_MODE=false` y vuelve a cargar los Wemos.
    Cada despertar muestrea una ventana, hace un envío, espera el callback con
    SEND_TIMEOUT_MS y duerme DEEP_SLEEP_SECONDS. El contador diagnóstico vuelve
    a cero; no es sequence. Con 60 s de sueño, el dashboard mostrará
    SIN COMUNICACIÓN después de 30 s: es el comportamiento esperado del timeout
    backend, no prueba de una lámpara averiada. Durante el sueño no se detecta
    parpadeo. No se altera el timeout del backend en esta etapa.

## Paquete ESP-NOW v1: exactamente 13 bytes

Ambos sketches contienen la misma declaración `packed` y static_assert de tamaño
u offsets. El formato se transmite en little endian en estas dos plataformas:

| Offset | Campo | Tamaño | Valores |
|---:|---|---:|---|
| 0 | protocol_version | 1 | 1 |
| 1 | ldr_state | 1 | LIGHT=0, DARK=1 |
| 2 | alert_type | 1 | NORMAL=0, APAGADA_CRITICA=1, PARPADEO_LDR=2 |
| 3 | flicker_count | 2 | uint16, saturación 65535 |
| 5 | uptime_ms | 4 | uint32, millis() del ESP8266 |
| 9 | packet_counter | 4 | uint32 diagnóstico, reiniciable |

No se envían node_id, MAC, credenciales ni una sequence HTTP en este paquete.
El ESP32 toma la MAC de los metadatos de recepción y copia todo a una cola de
16 entradas, sin Serial, HTTP, NVS ni esperas dentro del callback Wi-Fi.
No se usa FromISR porque el callback se ejecuta en una tarea, no en una ISR.
Los contadores de inválidos y cola llena se consultan por Serial cada 10 s.
Se admiten los callbacks ESP32 2.x y 3.x mediante ESP_ARDUINO_VERSION_MAJOR.

El ESP32 convierte enums y construye solo los ocho campos JSON exigidos por Flask,
escapando cadenas; uptime_ms viene del paquete, nunca del reloj del gateway.
La clave solo viaja en X-Device-Key. No hay dependencias JSON externas.

## Secuencias durables y límites de entrega

Preferences utiliza namespace `lb-physical`, clave uint64 `next_block` como límite
superior **exclusivo** del bloque reservado. El contador HTTP es global al gateway
(compartido por ambos nodos), por lo que tampoco repite secuencias por MAC.

Con SEQUENCE_START=10000 y bloques de 100:

- Antes de emitir 10000 escribe/verifica 10100 en NVS.
- Emite 10000..10099 desde RAM; no escribe NVS por evento.
- Al reiniciar toma 10100 y reserva hasta 10200, aunque sobraran números anteriores.
- Nunca retrocede por cambiar SEQUENCE_START a un valor menor.
- Si NVS no abre, tiene tipo/valor inválido, falla una reserva o se agota uint32,
  detiene la publicación. No usa una secuencia RAM como alternativa.
- Cada evento pendiente guarda una única sequence y un JSON inmutable para todos
  sus reintentos. packet_counter no participa en la deduplicación HTTP.

La garantía supone que **se conserva NVS**. Borrar toda la flash, cambiar el ESP32
o restaurar una copia antigua de NVS puede reutilizar números. Antes de hacerlo,
consulta de forma privada `MAX(sequence)` de physical_events para ese gateway y
establece SEQUENCE_START por encima del máximo. No borres historial para resolverlo.
El piso de ejemplo 10000 evita los eventos de demostración 1..4, pero no sustituye
consultar el historial real. Al agotar uint32 hay que planificar una nueva identidad
de gateway/protocolo; no hacer wrap ni reiniciar contadores automáticamente.

No hay outbox durable de eventos: un corte de energía pierde la cola y el evento
pendiente, aunque no reutiliza su secuencia. ESP-NOW aquí no lleva cifrado ni un ACK
aplicativo. Un callback de envío exitoso no garantiza que HTTP/SQLite hayan guardado
el evento. No hay reenvío automático del mismo paquete por parte del Wemos; si la
radio no devuelve callback a tiempo, se desactiva hasta reiniciar para impedir que
un callback tardío confirme otro paquete. En demo, errores de enlace normales se
recuperan con reportes posteriores. El parpadeo y los cambios durante el envío/sueño
pueden perderse; no es un sistema de registro sin pérdidas.

## Wi-Fi, reintentos y Serial

El gateway intenta conectar al arrancar con timeout de 15 s. Inicializa ESP-NOW
sobre el canal asociado, o sobre el configurado si el router está ausente. Después
realiza reconexiones acotadas sin desinicializar ESP-NOW ni apagar el radio.
Después de un intento fallido espera WIFI_RECONNECT_INTERVAL_MS antes de empezar
otro; así evita reiniciar el escaneo inmediatamente al vencer el timeout.
El escaneo/reasociación puede ocasionar pérdidas temporales; no existe garantía de
recepción simultánea en canales diferentes con una sola radio.

HTTP usa timeout de conexión de 2 s y lectura de 3 s, máximo 3 intentos por evento,
espera incremental de 1 y 2 s (acotada a 5 s), sin bloqueo infinito. Mientras HTTP
espera, la tarea Wi-Fi sigue encolando; una cola llena descarta el paquete nuevo.
Los eventos tienen edad máxima de 20 s en RAM para no presentar observaciones
muy antiguas como comunicación reciente; una operación HTTP ya iniciada puede
terminar después de ese plazo, hasta sus timeouts. No se amplía el contrato HTTP.

Serial del gateway muestra MAC propia, IP, canal, MAC remitente, alerta, sequence,
packet_counter, código HTTP, reintentos y contadores. No muestra claves, contraseñas,
SSID, cabeceras ni cuerpos HTTP. Usa nivel de depuración del core **None** para
no habilitar logs internos de bibliotecas que puedan volcar información adicional.
No se siguen redirecciones HTTP para evitar propagar la cabecera de autenticación.

| Mensaje/código | Interpretación y acción |
|---|---|
| 201 accepted | Backend aceptó y persistió el evento |
| 200 duplicate | Reintento ya registrado; entrega confirmada |
| Error de red, 408, 429, 5xx | Reintentos limitados conservando sequence |
| 400/415/422 | JSON/versión/tipos/alerta incorrectos; revisar contrato, no bucle infinito |
| 401 | Clave vacía o diferente; corregir config.h/.env sin imprimirla |
| 404 / MAC desconocida | Registrar MAC STA real del Wemos en el backend |
| 409 | Secuencia reutilizada con otros datos; revisar NVS y piso de secuencia |
| Cola llena / evento expirado | Backend lento o sin Wi-Fi; ajustar intervalos y revisar conectividad |
| NVS unavailable / sequence reservation failed | Publicación detenida para evitar duplicados; no borrar NVS a ciegas |
| ESP-NOW no recibe | Revisar MAC STA del gateway, alimentación, canal y whitelist |
| WARNING channel differs | Copiar el canal real del router a ambos Wemos y recargar |
| SIN COMUNICACIÓN | Sin evento nuevo por más de 30 s; revisar Serial, sueño, LAN y backend |
| LDR invertido | Cambiar LDR_DARK_LEVEL HIGH/LOW y recalibrar el módulo |

HTTP sin TLS y ESP-NOW sin cifrado son decisiones limitadas a una LAN de demo.
Una allowlist MAC no impide spoofing. Seguridad de producción, cifrado ESP-NOW,
HTTPS, entrega durable y administración remota quedan pendientes.

## Compilación posterior con Arduino CLI

No se instala Arduino CLI ni cores globales como parte de esta entrega. Si ya
están instalados, comprobar con `arduino-cli version` y `arduino-cli core list`.
Después de crear los dos config.h privados, desde la raíz del repositorio:

```powershell
arduino-cli compile --fqbn esp8266:esp8266:d1_mini firmware/esp8266_node
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/esp32_gateway
arduino-cli compile --fqbn esp8266:esp8266:d1_mini firmware/tools/print_esp8266_mac
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/tools/print_esp32_mac
```

Prueba la rama alternativa 2.0.17 por separado si la necesitas; no confundas una
revisión de firmas con una compilación real. Los sketches tools no requieren config.h.
En Arduino IDE abre el .ino del directorio con el mismo nombre y compila con la
placa/core correctos. Mantén los config.h fuera de Git y de cualquier intercambio.

## Información que falta del montaje real

Necesitamos las tres MAC STA, canal y credenciales de Wi-Fi de 2.4 GHz, IP LAN
estable de la PC, clave compartida, polaridad y ajuste del módulo LDR, resultado
de pruebas de antirrebote/parpadeo, intervalo deseado, características del sistema
solar/batería/regulador y versiones de core efectivamente instaladas. Contraseñas
y claves deben cargarse localmente, nunca enviarse en capturas o commits.
