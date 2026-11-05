"""SQLite persistence for physical telemetry; no device secrets are stored here."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

FIELDS = ('protocol_version', 'gateway_id', 'node_mac', 'ldr_state', 'alert_type',
          'flicker_count', 'sequence', 'uptime_ms')


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class SequenceConflict(Exception):
    pass


class EventStore:
    def __init__(self, path, nodes):
        self.path = path
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('''CREATE TABLE IF NOT EXISTS network_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, zone TEXT NOT NULL,
                message TEXT NOT NULL, node_id INTEGER NOT NULL,
                status TEXT NOT NULL, timestamp TEXT NOT NULL)''')
            if 'source' not in {r['name'] for r in c.execute('PRAGMA table_info(network_events)')}:
                c.execute("ALTER TABLE network_events ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'")
            c.execute('''CREATE TABLE IF NOT EXISTS physical_nodes (
                node_mac TEXT PRIMARY KEY, node_id INTEGER NOT NULL UNIQUE,
                gateway_id TEXT NOT NULL, label TEXT NOT NULL,
                enabled INTEGER NOT NULL, created_at TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS physical_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                protocol_version INTEGER NOT NULL, gateway_id TEXT NOT NULL,
                node_mac TEXT NOT NULL, ldr_state TEXT NOT NULL, alert_type TEXT NOT NULL,
                flicker_count INTEGER NOT NULL, sequence INTEGER NOT NULL, uptime_ms INTEGER NOT NULL,
                node_id INTEGER NOT NULL, zone TEXT NOT NULL, received_at TEXT NOT NULL,
                state_applied INTEGER NOT NULL, network_event_id INTEGER REFERENCES network_events(id),
                UNIQUE(gateway_id, node_mac, sequence))''')
            c.execute('CREATE INDEX IF NOT EXISTS physical_node_sequence ON physical_events(node_id, sequence DESC)')
            c.execute('CREATE INDEX IF NOT EXISTS physical_node_received ON physical_events(node_mac, id DESC)')
            c.execute('CREATE INDEX IF NOT EXISTS network_node_history ON network_events(node_id, id DESC)')
            c.execute('UPDATE physical_nodes SET enabled=0')
            # Configuration replaces registrations without deleting historical events.
            for node in nodes:
                c.execute('UPDATE physical_nodes SET node_mac=? WHERE node_id=? AND node_mac<>?',
                          (node['node_mac'], node['node_id'], node['node_mac']))
            for node in nodes:
                c.execute('''INSERT INTO physical_nodes VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(node_mac) DO UPDATE SET node_id=excluded.node_id,
                    gateway_id=excluded.gateway_id, label=excluded.label, enabled=1''',
                          (node['node_mac'], node['node_id'], node['gateway_id'], node['label'], utcnow()))

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def latest(self, node):
        identity = (node['node_mac'], node['node_id'], node['gateway_id'])
        with self.connect() as c:
            effective = c.execute('''SELECT * FROM physical_events WHERE node_mac=? AND node_id=?
                AND gateway_id=? AND state_applied=1 ORDER BY sequence DESC LIMIT 1''', identity).fetchone()
            if effective is None:
                return None
            result = dict(effective)
            receipt = c.execute('''SELECT received_at FROM physical_events WHERE node_mac=?
                AND node_id=? AND gateway_id=? ORDER BY id DESC LIMIT 1''', identity).fetchone()
            result['last_received_at'] = receipt['received_at']
            return result

    def accept(self, payload, node, zone):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('''SELECT * FROM physical_events
                WHERE gateway_id=? AND node_mac=? AND sequence=?''',
                (payload['gateway_id'], payload['node_mac'], payload['sequence'])).fetchone()
            if old:
                if any(old[k] != payload[k] for k in FIELDS):
                    raise SequenceConflict()
                return dict(old), True
            previous = c.execute('''SELECT * FROM physical_events WHERE gateway_id=?
                AND node_mac=? AND node_id=? AND state_applied=1
                ORDER BY sequence DESC LIMIT 1''',
                (payload['gateway_id'], payload['node_mac'], node['node_id'])).fetchone()
            applied = previous is None or payload['sequence'] > previous['sequence']
            before = previous['alert_type'] if previous else 'NORMAL'
            changed = applied and before != payload['alert_type']
            stamp = utcnow()
            history_id = None
            if changed:
                normal = payload['alert_type'] == 'NORMAL'
                message = ('TEST_OK' if normal else payload['alert_type']) + '_NODO_' + str(node['node_id'])
                cur = c.execute('''INSERT INTO network_events
                    (zone,message,node_id,status,timestamp,source) VALUES (?,?,?,?,?,'physical')''',
                    (zone, message, node['node_id'], 'REPAIRED' if normal else 'CRITICAL_FAIL', stamp))
                history_id = cur.lastrowid
            cur = c.execute('''INSERT INTO physical_events
                (protocol_version,gateway_id,node_mac,ldr_state,alert_type,flicker_count,
                sequence,uptime_ms,node_id,zone,received_at,state_applied,network_event_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                tuple(payload[k] for k in FIELDS) + (node['node_id'], zone, stamp, int(applied), history_id))
            event = dict(c.execute('SELECT * FROM physical_events WHERE id=?', (cur.lastrowid,)).fetchone())
        return event, False
