# Compilación real del firmware — 5 de noviembre de 2026

Validación realizada exclusivamente en Docker Desktop Linux/amd64, sin instalar
Arduino CLI ni cores globales en Windows y sin cargar firmware en placas reales.

## Herramientas y versiones

- Arduino CLI 1.5.1, commit 01f3d4f2b; distribución Linux_64bit verificada con el
  SHA-256 publicado en el asset oficial `1.5.1-checksums.txt` del mismo release.
- ESP8266 Arduino Core 3.1.2, `esp8266:esp8266:d1_mini`.
- ESP32 Arduino Core 3.3.12, `esp32:esp32:esp32`.
- Índices oficiales: arduino.esp8266.com y espressif.github.io/arduino-esp32.
- Opciones de las compilaciones: `--clean --warnings all --jobs 2`, build-path
  distinto para cada sketch. Se eliminaron sus directorios de build antes de compilar.
- Solo bibliotecas de los cores. pyserial 3.5 se instaló dentro de la imagen Linux.
- No se compiló el core ESP32 2.0.17: su rama alternativa no está certificada aquí.

Los config.h se generaron únicamente en las copias del contenedor desde
config.example.h. En el gateway se sustituyeron los placeholders CHANGE_ME por
valores ficticios BUILD_ONLY para mantener las rutas de publicación habilitadas
al optimizar y obtener tamaños representativos. No eran credenciales reales y
no se ejecutó el firmware ni se enviaron eventos HTTP al backend durante la
compilación. Los config.h temporales se eliminaron al terminar cada tanda.

## Resultados finales

Flash y RAM en bytes. RAM es el consumo estático informado por cada core;
no equivale a una medición del heap disponible en ejecución.

| Sketch / variante | Core | Resultado | Flash usada / límite | RAM estática / límite | Warnings |
|---|---|---|---:|---:|---:|
| esp8266_node | ESP8266 3.1.2 | OK | 242,192 / 1,048,576 | 28,692 / 80,192 | 0 |
| esp32_gateway | ESP32 3.3.12 | OK | 935,932 / 1,310,720 | 48,472 / 327,680 | 0 |
| print_esp8266_mac | ESP8266 3.1.2 | OK | 238,256 / 1,048,576 | 28,208 / 80,192 | 0 |
| print_esp32_mac | ESP32 3.3.12 | OK | 876,492 / 1,310,720 | 45,672 / 327,680 | 0 |
| esp8266_energy | ESP8266 3.1.2 | OK | 242,224 / 1,048,576 | 28,736 / 80,192 | 0 |

En ESP8266, la salida separa flash IROM, RAM de datos e IRAM:

- Nodo demo y ahorro: IRAM 59,811 / 65,536 bytes (91%). Incluye 32,768 bytes
  reservados para caché y 27,043 bytes de código IRAM; quedan 5,725 bytes.
- Herramienta MAC ESP8266: IRAM 59,747 / 65,536 bytes (91%), con 26,979 bytes
  de código IRAM además de la caché. Tener en cuenta ese margen al ampliar firmware.

No se emitieron errores ni warnings de compilación en los cuatro sketches finales
ni en la variante adicional de ahorro. No se suprimieron avisos mediante casts
inseguros ni se cambió el nivel `--warnings all`.

## Corrección realizada y repetición

La primera tanda completa compiló los cuatro sketches. En la revisión lógica se
identificó que, con timeout e intervalo Wi-Fi de 15 s, un intento fallido podía
iniciar otra conexión inmediatamente. Se actualiza `lastConnectAttempt` al fallar,
tanto al arrancar como en maintainWifi(), para respetar el intervalo de escucha
antes de volver a intentar. Después se recompilaron los cuatro desde cero: todos
aprobaron. Finalmente se compiló también el nodo con DEMO_MODE=false.

La preparación inicial de la herramienta recibió HTTP 404 al buscar el checksum
con un nombre incorrecto; se corrigió la URL al asset oficial del release antes
de instalar los cores. No fue un error de los sketches.

## Revisión del protocolo y modos

- Nombres de carpetas y .ino coincidentes en los cuatro sketches.
- Paquete packed idéntico de **13 bytes**: uint8 protocol_version, enums uint8
  LIGHT=0/DARK=1 y NORMAL=0/APAGADA_CRITICA=1/PARPADEO_LDR=2, uint16 flicker_count,
  uint32 uptime_ms y uint32 packet_counter. Offsets 0,1,2,3,5,9; static_assert
  evaluados por los dos compiladores. Wire little endian en ambas plataformas.
- Tamaño validado antes de memcpy. Callback ESP32 3.x con esp_now_recv_info_t;
  la MAC se copia de src_addr. Cola sin espera, sin HTTP, NVS ni Serial en callback.
- GPIO5/D1 para DO, antirrebote, saturación de transiciones y canal 1..13.
  Antes de registrar el peer se rechazan MAC multicast/broadcast/cero; el array
  de configuración tiene exactamente seis bytes. Una MAC unicast con formato
  válido todavía debe corresponder al gateway físico real.
- JSON con los ocho campos exactos, sin node_id; MAC de metadatos y uptime_ms
  uint32 del ESP8266. Buffer MAC de 18 bytes y escape de controles de 7 bytes;
  el resto usa String dinámico y GATEWAY_ID validado de hasta 64 caracteres.
- Un único JSON/sequence permanece inmutable durante los reintentos limitados.
  La reserva NVS guarda y verifica el límite exclusivo antes de asignar números;
  un reinicio salta los números restantes del bloque. La garantía requiere conservar
  NVS; reemplazos, borrados o restauraciones antiguas requieren un piso superior
  al máximo guardado en SQLite. No hay outbox durable de eventos.
- DEMO_MODE=true nunca llama a sleepNow(). Cambios de alerta/lectura y periodo
  se evalúan en cada cierre de ventana; con 2 s de ventana, periodo de 10 s y
  espera de envío limitada a 1.5 s, los reportes se intentan antes de 30 s en
  funcionamiento normal. Un fallo de radio/red puede impedir su llegada.
- Luz estable con 0 transiciones produce NORMAL; oscuridad estable con 0 produce
  APAGADA_CRITICA; más de 6 transiciones filtradas por ventana produce PARPADEO_LDR.
  Las ventanas se reinician y no bloquean permanentemente reportes posteriores.
- DEMO_MODE=false muestrea una ventana, envía con timeout y entra a ESP.deepSleep.
  El despertar requiere el puente físico D0/GPIO16 -> RST. Se detecta parpadeo
  solo durante la ventana despierta. Con sueño de 60 s, SIN COMUNICACIÓN después
  de 30 s es esperado y está documentado; no es diagnóstico de luminaria apagada.

Estas confirmaciones de comportamiento son revisión de flujo y compilación,
no pruebas de ejecución, enlace radio, consumo, despertar o lámpara reales.

## Regresión y limpieza

Las **28 pruebas backend aprobaron, 0 fallidas**, con código montado en modo de
solo lectura y SQLite temporal. No se modificaron Flask, dashboard ni CSV.

Entorno de compilación: contenedor e imagen `lb-fw-compile-0642768e`, fuente
montada de solo lectura, carpetas internas separadas /cli-config, /cli-data,
/cli-downloads, /cli-user, /build y /work. No se crearon volúmenes Docker.
El contenedor, su imagen y el directorio auxiliar del checkout se eliminaron al
terminar; datos, descargas, salidas y config.h estaban dentro de ese entorno.
No se borraron ni detuvieron recursos ajenos ni bases o volúmenes de Light Bridge.

config.h sigue ignorado por Git y Docker; config.example.h puede versionarse.
La revisión de secretos y git diff --check resultaron correctas. No se hizo commit,
push, merge, rebase, reset ni cambio de rama.
