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
