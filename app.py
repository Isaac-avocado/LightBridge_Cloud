import os
import json
import time
import socket
import random
import math
import threading
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient

# --- CONFIGURACIÓN DISTRIBUIDA ---
ROLE = os.getenv('ROLE', 'MASTER')
MY_ZONE = os.getenv('ZONE', 'ZONA-CENTRAL (Master)')
MASTER_IP = os.getenv('MASTER_IP', '127.0.0.1')
MONGO_URI = os.getenv('MONGO_URI', '')

# IPs Regionales para enrutamiento desde el Master
NORTE_IP = os.getenv('NORTE_IP', '10.0.0.11')
SUR_IP = os.getenv('SUR_IP', '10.0.0.12')
ESTE_IP = os.getenv('ESTE_IP', '10.0.0.13')
OESTE_IP = os.getenv('OESTE_IP', '10.0.0.14')

app = Flask(__name__)
active_failures = set()
historical_logs = []

# --- BASE DE DATOS (Solo el Master escribe) ---
alerts_col = None
if ROLE == 'MASTER' and MONGO_URI:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        alerts_col = client['lightbridge_scada']['network_events']
        print("[DB] ✅ Conectado a MongoDB Atlas")
    except Exception as e:
        print(f"[DB] ❌ Error conectando a Mongo: {e}")

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

# Nodos asignados a esta máquina EC2 específica
my_local_nodes = [nid for nid, zone in NODE_ZONES.items() if zone == MY_ZONE]

# --- CAPA FÍSICA Y DE RED (UDP MOM) ---
udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_sock.bind(("0.0.0.0", 5001))

def log_event(zone, msg, node_id):
    timestamp = time.strftime('%H:%M:%S')
    if "TEST_OK" in msg:
        if node_id in active_failures: active_failures.remove(node_id)
        log_text = f"[{timestamp}] REPARACIÓN: Nodo {node_id} ({zone})"
        status = "REPAIRED"
    else:
        active_failures.add(node_id)
        log_text = f"[{timestamp}] FALLO: Nodo {node_id} ({zone})"
        status = "CRITICAL_FAIL"
    
    historical_logs.insert(0, log_text)
    if len(historical_logs) > 30: historical_logs.pop()

    if alerts_col is not None:
        alerts_col.insert_one({"zone": zone, "message": msg, "node_id": node_id, "status": status, "timestamp": datetime.utcnow()})

def udp_listener():
    print(f"[{ROLE}] 📡 Escuchando UDP en puerto 5001 (Zona: {MY_ZONE})")
    while True:
        data, addr = udp_sock.recvfrom(2048)
        packet = json.loads(data.decode('utf-8'))
        
        if ROLE == 'MASTER':
            # El Master recibe las alertas de las Regiones
            if packet.get('type') == 'sos':
                msg = packet.get('message')
                node_id = packet.get('source')
                log_event(packet.get('zone'), msg, node_id)
        else:
            # Una región recibe un comando de la web (fallo provocado manual)
            if packet.get('type') == 'control':
                target = packet.get('target')
                if target in my_local_nodes:
                    action = packet.get('action')
                    msg = f"TEST_OK_NODO_{target}" if action == 'repair' else f"FALLO_NODO_{target}"
                    # Envía el SOS al Master por la red
                    out_pkt = json.dumps({"type": "sos", "source": target, "zone": MY_ZONE, "message": msg})
                    udp_sock.sendto(out_pkt.encode('utf-8'), (MASTER_IP, 5001))

threading.Thread(target=udp_listener, daemon=True).start()

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
    return jsonify({"nodes": norm_coords, "super_nodes": SUPER_NODES})

@app.route('/api/state')
def get_state():
    return jsonify({"active_failures": list(active_failures), "logs": historical_logs})

@app.route('/api/control', methods=['POST'])
def control_node():
    data = request.json
    target = data.get('node_id')
    action = data.get('action')
    zone = NODE_ZONES.get(target)
    
    # --- ENRUTAMIENTO HACIA LAS MÁQUINAS AWS (IPs Elásticas) ---
    dest_ip = "127.0.0.1" 
    if zone == "ZONA-NORTE": dest_ip = NORTE_IP
    elif zone == "ZONA-SUR": dest_ip = SUR_IP
    elif zone == "ZONA-ESTE": dest_ip = ESTE_IP
    elif zone == "ZONA-OESTE": dest_ip = OESTE_IP
    
    cmd = json.dumps({"type": "control", "action": action, "target": target})
    udp_sock.sendto(cmd.encode('utf-8'), (dest_ip, 5001))
    return jsonify({"status": "Command routed"})

if __name__ == '__main__':
    if ROLE == 'MASTER':
        app.run(host='0.0.0.0', port=80)
    else:
        while True: time.sleep(100)