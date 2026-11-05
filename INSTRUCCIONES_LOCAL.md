# Light Bridge en modo local

El modo local permite ejecutar el dashboard, el MASTER, las regiones simuladas
y el historial sin AWS, Azure ni MongoDB Atlas.

## Opción rápida con Docker

Con Docker Desktop abierto, ejecuta dentro de la carpeta del proyecto:

```powershell
docker compose up --build
```

Después abre `http://localhost:5000`. Para detenerlo usa:

```powershell
docker compose down
```

El historial permanece guardado en el volumen `lightbridge_data`.

## Opción sin Docker en Windows

Abre una terminal dentro de la carpeta del proyecto y ejecuta:

```powershell
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Después abre en el navegador:

```text
http://localhost:5000
```

La aplicación crea automáticamente `lightbridge_local.db`. Ese archivo SQLite
conserva el historial de fallos y reparaciones aunque se cierre el programa.

### Ejecuciones posteriores

```powershell
venv\Scripts\activate
python app.py
```

Para detener el servidor usa `Ctrl+C` en la terminal.

## Endpoints del dashboard

| Método | Ruta | Función |
|---|---|---|
| GET | `/api/health` | Comprueba el modo y la base de datos |
| GET | `/api/topology` | Devuelve nodos, zonas y supernodos |
| GET | `/api/state` | Devuelve fallos, regiones, logs y métricas |
| GET | `/api/dashboard` | Entrega un resumen agregado para la interfaz |
| GET | `/api/history` | Consulta el historial SQLite |
| POST | `/api/control` | Aplica un fallo o una reparación |

## Modo distribuido futuro

Para volver a desplegar varios procesos o conectar nodos físicos, configura
`LOCAL_MODE=false`. La composición avanzada ya incluye MASTER, Norte, Sur,
Este y Oeste:

```powershell
docker compose -f docker-compose.distributed.yml up --build
```

## Fase 3: recepcion fisica (prototipo)

Copia `.env.example` a `.env` y establece una clave aleatoria privada en
`PHYSICAL_API_KEY`. El ejemplo deja la clave vacia: la ruta devuelve 503 hasta
configurarla. `.env` esta excluido de Git y del contexto Docker. No compartas
la salida de `docker compose config` cuando contenga una clave real.

Configura `PHYSICAL_GATEWAY_ID`, `PHYSICAL_NODE_1_MAC`, `PHYSICAL_NODE_1_ID`,
`PHYSICAL_NODE_2_MAC` y `PHYSICAL_NODE_2_ID`. Valores provisionales:
`esp32-master-01`, MAC `02:00:00:00:00:01` -> nodo 1 y MAC
`02:00:00:00:00:02` -> nodo 25. No cambies los CSV. Usa un solo proceso Flask.
Cambiar una asignacion no elimina su historial; un dispositivo nuevo empieza
sin datos y la secuencia no debe reutilizarse para la misma MAC y gateway.

El ESP32 envia POST a `http://IP_LOCAL_DE_LA_PC:5000/api/physical/events`,
con `Content-Type: application/json` y `X-Device-Key`. La PC y el ESP32 deben
poder comunicarse en la LAN. Permite TCP 5000 en Firewall de Windows solamente
en red privada, idealmente desde la IP del gateway. No publiques el puerto en
Internet. HTTP no cifra la clave; HTTPS y credenciales por dispositivo quedan
pendientes para produccion. No se implementa firmware en esta etapa.

Contrato (todos los campos obligatorios; sin campos extra):

```json
{"protocol_version":1,"gateway_id":"esp32-master-01","node_mac":"02:00:00:00:00:01","ldr_state":"DARK","alert_type":"APAGADA_CRITICA","flicker_count":0,"sequence":1001,"uptime_ms":60000}
```

Alertas: APAGADA_CRITICA, PARPADEO_LDR y NORMAL. NORMAL repara; no existe
soporte fisico para NO_ENCENDIO_RELE. LIGHT/DARK son observaciones semanticas;
la polaridad electrica y los umbrales deben calibrarse en firmware. Esta etapa
valida el vocabulario, sin inferir si la luminaria deberia estar encendida.
Secuencia y uptime: enteros uint32; flicker_count: uint16; booleanos rechazados.
La secuencia debe ser durable y creciente por MAC y gateway, incluso al reiniciar.
Los reintentos conservan el contenido y la secuencia originales. Eventos nuevos
atrasados se guardan sin revertir el estado actual. La recepcion esta limitada a
2 KiB. No hay limitador de frecuencia todavia; usar solo en una LAN controlada.

Respuestas: 201 accepted, 200 duplicate, 400 JSON invalido, 401 clave incorrecta,
404 MAC desconocida, 409 sequence_conflict, 415 Content-Type incorrecto,
422 campos invalidos, 503 sin configuracion, 500 fallo interno.

SQLite migra automaticamente sin borrar historial. Eventos anteriores: legacy;
controles virtuales: simulated; transiciones fisicas: physical. Los reportes
normales y fallos repetidos se conservan en physical_events sin inundar el
historial comun. El estado fisico se reconstruye al iniciar. Los nodos fisicos
estan reservados en el backend y en los controles del dashboard: los eventos
fisicos solo cambian con los reportes recibidos del dispositivo. La seccion
"Dashboard: nodos físicos" describe la visualizacion y los estados de conexion.

Pruebas sin hardware (base temporal, sin volumen de produccion):

```powershell
python -m unittest discover -s tests -v
```

Con las dependencias disponibles tambien en Docker, montar el proyecto en
`/app`, ejecutar ese mismo comando y no montar el volumen de datos real.
No ejecutar `docker compose down -v`: eliminaria el historial persistente.


## Dashboard: nodos físicos

La sección NODOS FÍSICOS muestra los dispositivos registrados, su MAC, zona,
sensor, lectura y última comunicación. Se actualiza con el sondeo existente de
`/api/state`. Sin reportes aparece SIN DATOS; después de 30 segundos sin una
recepción nueva aparece SIN COMUNICACIÓN. Una desconexión conserva la última
lectura y no significa automáticamente que la luminaria esté apagada.

Los mapas conservan los colores de zona para los nodos simulados. Los físicos
llevan anillo, centro mayor e identificadores F1/F25 (según el node_id configurado):
verde normal, rojo apagada crítica, naranja parpadeo, gris sin datos y morado sin
comunicación. Al pasar el cursor o tocar el nodo se muestra su información.
Las tarjetas se apilan en móvil y respetan los temas claro y oscuro.

Los controles individuales, Fallo aleatorio y Reparar todos solo modifican nodos
simulados. Los físicos dependen exclusivamente del estado recibido del backend.
El historial distingue FÍSICO, SIMULADO y LEGACY. Si falla la consulta, se conserva
la última lectura con un aviso de pérdida de comunicación hasta recuperarla.
