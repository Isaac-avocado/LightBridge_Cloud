"""Integration tests use only disposable SQLite files, no Docker volumes."""
import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class PhysicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {'LOCAL_DB_PATH': cls.tmp.name + '/events.db',
            'PHYSICAL_API_KEY': 'test-only-key', 'PHYSICAL_GATEWAY_ID': 'esp32-master-01',
            'PHYSICAL_NODE_1_MAC': '02:00:00:00:00:01', 'PHYSICAL_NODE_1_ID': '1',
            'PHYSICAL_NODE_2_MAC': '02:00:00:00:00:02', 'PHYSICAL_NODE_2_ID': '25',
            'LIGHTBRIDGE_TESTING': '1', 'ROLE': 'MASTER', 'MONGO_URI': '', 'LOCAL_MODE': 'true'})
        cls.env.start()
        cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.module.udp_sock.close()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.m = self.module
        self.client = self.m.app.test_client()
        with self.m.physical_store.connect() as c:
            c.execute('DELETE FROM physical_events')
            c.execute('DELETE FROM network_events')
        self.m.active_failures.clear()
        self.m.physical_state.clear()
        self.m.physical_last_seen.clear()
        self.m.historical_logs.clear()
        self.payload = dict(protocol_version=1, gateway_id='esp32-master-01',
            node_mac='02:00:00:00:00:01', ldr_state='DARK', alert_type='APAGADA_CRITICA',
            flicker_count=0, sequence=1001, uptime_ms=60000)

    def post(self, data=None, key='test-only-key'):
        return self.client.post('/api/physical/events', json=self.payload if data is None else data,
            headers={} if key is None else {'X-Device-Key': key})

    def count(self, table):
        with self.m.physical_store.connect() as c:
            return c.execute('SELECT count(*) FROM ' + table).fetchone()[0]

    def test_critical(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.m.active_failures[1], 'APAGADA_CRITICA')
        self.assertEqual(self.client.get('/api/history').json['data'][0]['source'], 'physical')

    def test_flicker(self):
        self.payload.update(alert_type='PARPADEO_LDR', flicker_count=8)
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.m.active_failures[1], 'PARPADEO_LDR')

    def test_repair_and_repeated_normal(self):
        self.post()
        self.payload.update(sequence=1002, alert_type='NORMAL', ldr_state='LIGHT')
        self.assertEqual(self.post().status_code, 201)
        self.assertNotIn(1, self.m.active_failures)
        self.payload['sequence'] = 1003
        self.post()
        self.assertEqual(self.count('network_events'), 2)

    def test_duplicate(self):
        self.post()
        logs = list(self.m.historical_logs)
        result = self.post()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['status'], 'duplicate')
        self.assertEqual(self.count('physical_events'), 1)
        self.assertEqual(self.count('network_events'), 1)
        self.assertEqual(logs, self.m.historical_logs)

    def test_conflict(self):
        self.post()
        self.payload['uptime_ms'] += 1
        self.assertEqual(self.post().status_code, 409)

    def test_unknown_mac(self):
        self.payload['node_mac'] = '02:00:00:00:00:03'
        self.assertEqual(self.post().status_code, 404)

    def test_gateway(self):
        self.payload['gateway_id'] = 'other'
        self.assertEqual(self.post().status_code, 422)

    def test_auth(self):
        for key in (None, 'wrong'):
            with self.subTest(key=key):
                self.assertEqual(self.post(key=key).status_code, 401)
        self.assertEqual(self.count('physical_events'), 0)

    def test_unconfigured(self):
        with patch.dict(os.environ, {'PHYSICAL_API_KEY': ''}):
            self.assertEqual(self.post().status_code, 503)

    def test_missing_fields(self):
        for field in self.payload:
            with self.subTest(field=field):
                data = dict(self.payload)
                del data[field]
                self.assertEqual(self.post(data).status_code, 422)

    def test_additional(self):
        self.assertEqual(self.post({**self.payload, 'node_id': 35}).status_code, 422)

    def test_types_ranges(self):
        for field in ('protocol_version', 'sequence', 'uptime_ms', 'flicker_count'):
            for value in (True, False, None, -1, 4294967296, 1.5, '1', [], {}):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.post({**self.payload, field: value}).status_code, 422)
        for field in ('gateway_id', 'node_mac', 'ldr_state', 'alert_type'):
            for value in (None, [], {}, 1):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.post({**self.payload, field: value}).status_code, 422)
        self.assertEqual(self.post({**self.payload, 'flicker_count': 65536}).status_code, 422)
        self.assertEqual(self.post({**self.payload, 'protocol_version': 2}).status_code, 422)

    def test_relay_rejected(self):
        self.payload['alert_type'] = 'NO_ENCENDIO_RELE'
        self.assertEqual(self.post().status_code, 422)

    def test_control_reserved(self):
        for nid in (1, 25):
            result = self.client.post('/api/control', json={'node_id': nid, 'action': 'repair'})
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json['error']['code'], 'physical_node_managed')

    def test_virtual_control(self):
        result = self.client.post('/api/control', json={'node_id': 35, 'action': 'fail'})
        self.assertEqual(result.status_code, 202)
        self.assertIn(35, self.m.active_failures)
        self.assertEqual(self.client.get('/api/history').json['data'][0]['source'], 'simulated')

    def test_restart(self):
        self.post()
        self.m.udp_sock.close()
        self.m = self.__class__.module = importlib.reload(self.m)
        self.assertEqual(self.m.active_failures[1], 'APAGADA_CRITICA')
        self.assertEqual(self.m.physical_state[1]['sequence'], 1001)
        self.client = self.m.app.test_client()
        self.assertEqual(self.post().status_code, 200)

    def test_regression(self):
        for path in ('/', '/api/health', '/api/dashboard', '/api/topology', '/api/state', '/api/history'):
            with self.subTest(path=path):
                result = self.client.get(path)
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.is_json if path != '/' else b'<html' in result.data.lower())
        state = self.client.get('/api/state').json
        self.assertTrue({'active_failures','logs','zone_status','summary','physical_nodes'} <= state.keys())
        self.assertEqual(state['physical_nodes'][0]['status'], 'no_data')

    def test_bad_json_content_size(self):
        for body, content, status in (('{', 'application/json', 400),
                ('[]','application/json',400), ('{}','text/plain',415),
                (' ' * 2049,'application/json',413),
                ('{"x":1,"x":2}','application/json',400)):
            with self.subTest(status=status, content=content):
                r = self.client.post('/api/physical/events', data=body, content_type=content,
                    headers={'X-Device-Key':'test-only-key'})
                self.assertEqual(r.status_code,status)

    def test_late_and_disconnect(self):
        self.payload['sequence'] = 2000
        self.post()
        self.payload.update(sequence=1000, alert_type='NORMAL', ldr_state='LIGHT')
        self.assertFalse(self.post().json['state_applied'])
        self.assertEqual(self.m.active_failures[1], 'APAGADA_CRITICA')

    def test_connectivity_boundaries(self):
        self.post()
        from datetime import datetime, timedelta
        from unittest.mock import Mock
        received = datetime.fromisoformat(self.m.physical_last_seen[1].rstrip('Z'))
        clock = Mock()
        clock.fromisoformat = datetime.fromisoformat
        for seconds, online in ((30, True), (31, False)):
            clock.utcnow.return_value = received + timedelta(seconds=seconds)
            with patch.object(self.m, 'datetime', clock):
                state = self.client.get('/api/state').json['physical_nodes'][0]
                self.assertEqual(state['online'], online)
                self.assertEqual(state['current_alert'], 'APAGADA_CRITICA')
        self.assertEqual(self.count('network_events'), 1)

    def test_normalization(self):
        self.payload['node_mac'] = '020000000001'
        self.assertEqual(self.post().status_code, 201)
        self.payload['node_mac'] = '02-00-00-00-00-01'
        self.assertEqual(self.post().status_code, 200)

    def test_udp_simulated_protected(self):
        self.m.log_event('ZONA-NORTE','TEST_OK_NODO_1',1)
        self.assertEqual(self.count('network_events'),0)
        self.assertNotIn(1,self.m.my_local_nodes)
        self.assertNotIn(25,self.m.my_local_nodes)

    def test_atomic_failure(self):
        with patch.object(self.m.physical_store, 'accept', side_effect=sqlite3.OperationalError('secret detail')):
            result = self.post()
        self.assertEqual(result.status_code,500)
        self.assertNotIn(b'secret detail',result.data)
        self.assertNotIn(1,self.m.active_failures)
        self.assertEqual(self.count('physical_events'),0)

    def test_concurrent_duplicate(self):
        from concurrent.futures import ThreadPoolExecutor
        def send(_):
            with self.m.app.test_client() as client:
                return client.post('/api/physical/events', json=self.payload,
                    headers={'X-Device-Key': 'test-only-key'}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(send, range(2))), [200, 201])
        self.assertEqual(self.count('physical_events'), 1)
        self.assertEqual(self.count('network_events'), 1)

    def test_transaction_rollback(self):
        with self.m.physical_store.connect() as c:
            c.execute("CREATE TRIGGER fail_physical BEFORE INSERT ON physical_events BEGIN SELECT RAISE(ABORT, 'test'); END")
        try:
            self.assertEqual(self.post().status_code, 500)
            self.assertEqual(self.count('network_events'), 0)
            self.assertNotIn(1, self.m.active_failures)
        finally:
            with self.m.physical_store.connect() as c:
                c.execute('DROP TRIGGER fail_physical')

    def test_configuration_validation(self):
        from physical_api import configured_nodes
        for env in ({'PHYSICAL_NODE_1_ID': '999999'},
                    {'PHYSICAL_NODE_1_MAC': 'invalid'},
                    {'PHYSICAL_NODE_2_ID': '1'}):
            with self.subTest(env=env), patch.dict(os.environ, env):
                with self.assertRaises(ValueError):
                    configured_nodes(self.m.coords)

    def test_legacy_migration(self):
        from event_store import EventStore
        path = self.tmp.name + '/legacy.db'
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE network_events (id INTEGER PRIMARY KEY,zone TEXT,message TEXT,node_id INTEGER,status TEXT,timestamp TEXT)')
            c.execute("INSERT INTO network_events VALUES(1,'zone','old',35,'REPAIRED','old')")
        for _ in range(2):
            store = EventStore(path,self.m.physical_nodes)
        with store.connect() as c:
            row = c.execute('SELECT * FROM network_events').fetchone()
            self.assertEqual(row['source'],'legacy')
            self.assertEqual(row['message'],'old')
            self.assertEqual(c.execute('SELECT count(*) FROM physical_nodes WHERE enabled=1').fetchone()[0],2)


if __name__ == '__main__':
    unittest.main()
