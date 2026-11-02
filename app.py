import os
import json
import time
import socket
import random
import math
import threading
import sqlite3
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient

# --- CONFIGURACIÓN DISTRIBUIDA ---
ROLE = os.getenv('ROLE', 'MASTER')
MY_ZONE = os.getenv('ZONE', 'ZONA-CENTRAL (Master)')
MASTER_IP = os.getenv('MASTER_IP', '127.0.0.1')
MONGO_URI = os.getenv('MONGO_URI', '')
LOCAL_MODE = os.getenv('LOCAL_MODE', 'true').lower() in {'1', 'true', 'yes'}
LOCAL_DB_PATH = os.getenv('LOCAL_DB_PATH', 'lightbridge_local.db')
WEB_PORT = int(os.getenv('PORT', '5000'))

# IPs Regionales para enrutamiento desde el Master
NORTE_IP = os.getenv('NORTE_IP', '10.0.0.11')
SUR_IP = os.getenv('SUR_IP', '10.0.0.12')
ESTE_IP = os.getenv('ESTE_IP', '10.0.0.13')
OESTE_IP = os.getenv('OESTE_IP', '10.0.0.14')

app = Flask(__name__)

ALLOWED_ACTIONS = {"fail", "repair"}
ALLOWED_FAILURE_TYPES = {"PARPADEO_LDR", "APAGADA_CRITICA", "NO_ENCENDIO_RELE"}

# --- SINCRONIZACIÓN DE ESTADO (MUTEX Y WATCHDOG) ---
state_lock = threading.Lock()
active_failures = {}  # Ahora es un diccionario: { node_id: "TIPO_FALLO" }
historical_logs = []
last_seen = {}        # Watchdog: { zona: timestamp_ultimo_latido }

# --- BASE DE DATOS (Solo el Master escribe) ---
alerts_col = None
local_db_enabled = False
if ROLE == 'MASTER' and MONGO_URI:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        alerts_col = client['lightbridge_scada']['network_events']
        print("[DB] ✅ Conectado a MongoDB Atlas")
    except Exception as e:
        print(f"[DB] ❌ Error conectando a Mongo: {e}")

if ROLE == 'MASTER' and alerts_col is None:
    try:
        with sqlite3.connect(LOCAL_DB_PATH) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS network_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    zone TEXT NOT NULL,
                    message TEXT NOT NULL,
                    node_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            ''')
        local_db_enabled = True
        print(f"[DB] ✅ Historial local SQLite: {LOCAL_DB_PATH}")
    except Exception as e:
        print(f"[DB] ❌ Error preparando SQLite: {e}")

# --- TOPOLOGÍA ---
nodes_df = pd.read_csv('main_network_nodes.csv')
links_df = pd.read_csv('main_network.csv')
degree_counts = pd.concat([links_df['Source'], links_df['Target']]).value_counts()
coords = {int(row['fid']): (float(row['X']), float(row['Y'])) for _, row in nodes_df.iterrows()}

SUPER_NODES = []
min_distance = math.hypot(nodes_df['X'].max() - nodes_df['X'].min(), nodes_df['Y'].max() - nodes_df['Y'].min()) / 2.0
while len(SUPER_NODES) < 5 and min_distance > 10:
    SUPER_NODES = []
    for nid in degree_counts.index:
        nid = int(nid)
        if nid not in coords: continue
        if not any(math.hypot(coords[nid][0]-coords[sn][0], coords[nid][1]-coords[sn][1]) < min_distance for sn in SUPER_NODES):
            SUPER_NODES.append(nid)
            if len(SUPER_NODES) == 5: break
    min_distance *= 0.9

zone_names = ["ZONA-CENTRAL (Master)", "ZONA-NORTE", "ZONA-SUR", "ZONA-ESTE", "ZONA-OESTE"]
NODE_ZONES = {nid: zone_names[SUPER_NODES.index(min(SUPER_NODES, key=lambda sn: math.hypot(coords[nid][0]-coords[sn][0], coords[nid][1]-coords[sn][1])))] for nid in coords.keys()}

my_local_nodes = [nid for nid, zone in NODE_ZONES.items() if zone == MY_ZONE]
if LOCAL_MODE and ROLE == 'MASTER':
    my_local_nodes = list(coords.keys())

# --- CAPA FÍSICA Y DE RED (UDP MOM) ---
udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_sock.bind(("0.0.0.0", 5001))

def log_event(zone, msg, node_id):
    timestamp = time.strftime('%H:%M:%S')
    status = "REPAIRED" if "TEST_OK" in msg else "CRITICAL_FAIL"

    with state_lock:
        if status == "REPAIRED":
            if node_id in active_failures:
                active_failures.pop(node_id, None)
            log_text = f"[{timestamp}] REPARACIÓN: Nodo {node_id} ({zone})"
        else:
            # Extraemos el tipo específico de fallo del mensaje UDP
            fail_type = msg.split('_NODO_')[0]
            active_failures[node_id] = fail_type
            log_text = f"[{timestamp}] FALLO: Nodo {node_id} ({zone})"

        historical_logs.insert(0, log_text)
        if len(historical_logs) > 30:
            historical_logs.pop()

    # La escritura de red se hace fuera del mutex para no bloquear las
    # consultas del dashboard si MongoDB tarda en responder.
    if alerts_col is not None:
        try:
            alerts_col.insert_one({
                "zone": zone,
                "message": msg,
                "node_id": node_id,
                "status": status,
                "timestamp": datetime.utcnow()
            })
        except Exception as e:
            print(f"[DB] Error guardando evento: {e}")
    elif local_db_enabled:
        try:
            with sqlite3.connect(LOCAL_DB_PATH) as conn:
                conn.execute(
                    '''INSERT INTO network_events
                       (zone, message, node_id, status, timestamp)
                       VALUES (?, ?, ?, ?, ?)''',
                    (zone, msg, node_id, status, datetime.utcnow().isoformat() + 'Z')
                )
        except Exception as e:
            print(f"[DB] Error guardando evento local: {e}")

def udp_listener():
    print(f"[{ROLE}] 📡 Escuchando UDP en puerto 5001 (Zona: {MY_ZONE})")
    while True:
        try:
            data, addr = udp_sock.recvfrom(2048)
            packet = json.loads(data.decode('utf-8'))

            if ROLE == 'MASTER':
                zone = packet.get('zone', 'UNKNOWN')
                # Actualiza el Watchdog al recibir CUALQUIER paquete de la región
                with state_lock:
                    last_seen[zone] = time.time()

                if packet.get('type') == 'sos':
                    msg = packet.get('message')
                    node_id = packet.get('source')
                    log_event(zone, msg, node_id)
            else:
                if packet.get('type') == 'control':
                    target = packet.get('target')
                    if target in my_local_nodes:
                        action = packet.get('action')
                        detail = packet.get('detail', 'FALLO_CRITICO')
                        msg = f"TEST_OK_NODO_{target}" if action == 'repair' else f"{detail}_NODO_{target}"
                        out_pkt = json.dumps({"type": "sos", "source": target, "zone": MY_ZONE, "message": msg})
                        udp_sock.sendto(out_pkt.encode('utf-8'), (MASTER_IP, 5001))
        except Exception as e:
            print(f"[UDP] Error de red: {e}")

threading.Thread(target=udp_listener, daemon=True).start()

# --- HEARTBEAT / WATCHDOG REGIONAL ---
def regional_heartbeat():
    while True:
        if ROLE != 'MASTER':
            pkt = json.dumps({"type": "heartbeat", "zone": MY_ZONE})
            udp_sock.sendto(pkt.encode('utf-8'), (MASTER_IP, 5001))
        else:
            with state_lock:
                if LOCAL_MODE:
                    # En local, un solo proceso representa las cinco regiones.
                    for zone in zone_names:
                        last_seen[zone] = time.time()
                else:
                    last_seen[MY_ZONE] = time.time()
        time.sleep(3) # Envía latido cada 3 segundos

threading.Thread(target=regional_heartbeat, daemon=True).start()

# --- SIMULACIÓN AUTÓNOMA ---
def simulate_local_failures():
    time.sleep(10)
    for node_id in my_local_nodes:
        if random.random() < 0.005:
            msg = f"NO_ENCENDIO_RELE_NODO_{node_id}"
            out_pkt = json.dumps({"type": "sos", "source": node_id, "zone": MY_ZONE, "message": msg})
            dest = "127.0.0.1" if ROLE == 'MASTER' else MASTER_IP
            udp_sock.sendto(out_pkt.encode('utf-8'), (dest, 5001))
            time.sleep(random.uniform(0.1, 0.5))

threading.Thread(target=simulate_local_failures, daemon=True).start()

# --- SERVIDOR WEB SCADA (Solo corre en el Master) ---
@app.route('/')
def dashboard():
    return render_template('index.html')

@app.route('/api/topology')
def get_topology():
    min_x, max_x = nodes_df['X'].min(), nodes_df['X'].max()
    min_y, max_y = nodes_df['Y'].min(), nodes_df['Y'].max()
    norm_coords = {nid: {"x": (nx-min_x)/(max_x-min_x), "y": (ny-min_y)/(max_y-min_y), "zone": NODE_ZONES[nid]} for nid, (nx, ny) in coords.items()}
    payload = {"nodes": norm_coords, "super_nodes": SUPER_NODES}

    # La red contiene cientos de miles de enlaces. Solo se incluyen cuando un
    # cliente los solicita expresamente para no ralentizar el dashboard actual.
    if request.args.get('include_links', '').lower() in {'1', 'true', 'yes'}:
        payload["links"] = [
            {"source": int(row['Source']), "target": int(row['Target'])}
            for _, row in links_df.iterrows()
        ]

    return jsonify(payload)

def build_state_snapshot():
    """Crea una copia consistente del estado que consumen los endpoints."""
    with state_lock:
        current_time = time.time()
        zone_status = {
            zone: "online" if (current_time - last_seen.get(zone, 0)) < 10 else "offline"
            for zone in zone_names
        }
        failures = dict(active_failures)
        logs = list(historical_logs)

    failure_types = {}
    for failure_type in failures.values():
        failure_types[failure_type] = failure_types.get(failure_type, 0) + 1

    return {
        "active_failures": failures,
        "logs": logs,
        "zone_status": zone_status,
        "summary": {
            "total_nodes": len(coords),
            "active_failures": len(failures),
            "online_zones": sum(status == "online" for status in zone_status.values()),
            "offline_zones": sum(status == "offline" for status in zone_status.values()),
            "failures_by_type": failure_types
        }
    }

@app.route('/api/state')
def get_state():
    return jsonify(build_state_snapshot())

@app.route('/api/dashboard')
def get_dashboard_data():
    """Endpoint agregado para dashboards nuevos sin romper los existentes."""
    state = build_state_snapshot()
    return jsonify({
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "role": ROLE,
        **state
    })

@app.route('/api/health')
def health_check():
    return jsonify({
        "status": "ok",
        "role": ROLE,
        "zone": MY_ZONE,
        "mode": "local" if LOCAL_MODE else "distributed",
        "database": "mongodb" if alerts_col is not None else (
            "sqlite" if local_db_enabled else "unavailable"
        )
    })

@app.route('/api/history')
def get_history():
    try:
        if alerts_col is not None:
            records = list(alerts_col.find({}, {"_id": 0}).sort("timestamp", -1).limit(200))
        elif local_db_enabled:
            with sqlite3.connect(LOCAL_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute('''
                    SELECT zone, message, node_id, status, timestamp
                    FROM network_events
                    ORDER BY id DESC
                    LIMIT 200
                ''').fetchall()
            records = [dict(row) for row in rows]
        else:
            return jsonify({"error": "No hay una base de datos disponible.", "data": []}), 503
        return jsonify({"data": records})
    except Exception as e:
        return jsonify({"error": str(e), "data": []})

@app.route('/api/control', methods=['POST'])
def control_node():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "El cuerpo debe ser JSON."}), 400

    try:
        target = int(data.get('node_id'))
    except (TypeError, ValueError):
        return jsonify({"error": "node_id debe ser un número entero."}), 400

    action = data.get('action')
    detail = data.get('detail', 'APAGADA_CRITICA')
    zone = NODE_ZONES.get(target)

    if target not in coords:
        return jsonify({"error": "El nodo solicitado no existe."}), 404
    if action not in ALLOWED_ACTIONS:
        return jsonify({"error": "action debe ser 'fail' o 'repair'."}), 400
    if action == "fail" and detail not in ALLOWED_FAILURE_TYPES:
        return jsonify({
            "error": "Tipo de fallo no permitido.",
            "allowed_failure_types": sorted(ALLOWED_FAILURE_TYPES)
        }), 400

    if LOCAL_MODE:
        message = (
            f"TEST_OK_NODO_{target}"
            if action == 'repair'
            else f"{detail}_NODO_{target}"
        )
        log_event(zone, message, target)
        return jsonify({
            "status": "command_applied_locally",
            "node_id": target,
            "action": action,
            "zone": zone
        }), 202

    dest_ip = "127.0.0.1"
    if zone == "ZONA-NORTE": dest_ip = NORTE_IP
    elif zone == "ZONA-SUR": dest_ip = SUR_IP
    elif zone == "ZONA-ESTE": dest_ip = ESTE_IP
    elif zone == "ZONA-OESTE": dest_ip = OESTE_IP

    cmd = json.dumps({"type": "control", "action": action, "target": target, "detail": detail})
    try:
        udp_sock.sendto(cmd.encode('utf-8'), (dest_ip, 5001))
    except OSError as e:
        return jsonify({"error": "No fue posible enrutar el comando.", "detail": str(e)}), 503

    return jsonify({
        "status": "command_routed",
        "node_id": target,
        "action": action,
        "zone": zone
    }), 202

if __name__ == '__main__':
    if ROLE == 'MASTER':
        print(f"[WEB] Abre http://localhost:{WEB_PORT}")
        app.run(host='0.0.0.0', port=WEB_PORT)
    else:
        while True: time.sleep(100)
