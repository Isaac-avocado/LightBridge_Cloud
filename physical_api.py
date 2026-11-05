"""Strict HTTP ingress for the ESP32 gateway."""
import hmac
import json
import os
import re
from flask import jsonify, request
from event_store import SequenceConflict

FIELDS = {'protocol_version', 'gateway_id', 'node_mac', 'ldr_state', 'alert_type',
          'flicker_count', 'sequence', 'uptime_ms'}


def normalize_mac(value):
    if not isinstance(value, str):
        raise ValueError('invalid MAC')
    if re.fullmatch(r'[0-9a-fA-F]{12}', value):
        value = ':'.join(value[i:i+2] for i in range(0, 12, 2))
    value = value.replace('-', ':').upper()
    if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', value):
        raise ValueError('invalid MAC')
    if int(value[:2], 16) & 1 or value == '00:00:00:00:00:00':
        raise ValueError('invalid unicast MAC')
    return value


def configured_nodes(coords):
    gateway = os.getenv('PHYSICAL_GATEWAY_ID', 'esp32-master-01')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', gateway):
        raise ValueError('invalid gateway configuration')
    nodes = []
    for i, default_id in ((1, 1), (2, 25)):
        nid = int(os.getenv(f'PHYSICAL_NODE_{i}_ID', str(default_id)))
        mac = normalize_mac(os.getenv(f'PHYSICAL_NODE_{i}_MAC', f'02:00:00:00:00:0{i}'))
        if nid not in coords:
            raise ValueError('physical node absent from topology')
        nodes.append(dict(node_id=nid, node_mac=mac, gateway_id=gateway, label=f'ESP8266 {i}'))
    if len({n['node_id'] for n in nodes}) != 2 or len({n['node_mac'] for n in nodes}) != 2:
        raise ValueError('duplicate physical registration')
    return nodes


def register_physical_api(app, store, nodes, zones, lock, on_event):
    def error(code, status):
        return jsonify(status='error', error={'code': code}), status

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    @app.post('/api/physical/events')
    def physical_events():
        key = os.getenv('PHYSICAL_API_KEY', '')
        if not key or store is None:
            return error('physical_not_configured', 503)
        if not hmac.compare_digest(request.headers.get('X-Device-Key', '').encode(), key.encode()):
            return error('unauthorized', 401)
        if request.mimetype != 'application/json':
            return error('unsupported_media_type', 415)
        if request.content_length is not None and request.content_length > 2048:
            return error('payload_too_large', 413)
        raw = request.stream.read(2049)
        if len(raw) > 2048:
            return error('payload_too_large', 413)
        try:
            data = json.loads(raw, object_pairs_hook=unique_object,
                              parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except (ValueError, UnicodeError, RecursionError):
            return error('invalid_json', 400)
        if not isinstance(data, dict):
            return error('invalid_json', 400)
        if set(data) != FIELDS:
            return error('invalid_fields', 422)
        limits = {'protocol_version': (1, 1), 'flicker_count': (0, 65535),
                  'sequence': (0, 4294967295), 'uptime_ms': (0, 4294967295)}
        for field, (low, high) in limits.items():
            if type(data[field]) is not int or not low <= data[field] <= high:
                return error('invalid_fields', 422)
        if not isinstance(data['gateway_id'], str) or data['gateway_id'] != nodes[0]['gateway_id']:
            return error('invalid_gateway', 422)
        if not isinstance(data['ldr_state'], str) or data['ldr_state'] not in ('LIGHT', 'DARK'):
            return error('invalid_ldr_state', 422)
        if not isinstance(data['alert_type'], str) or data['alert_type'] not in ('NORMAL', 'APAGADA_CRITICA', 'PARPADEO_LDR'):
            return error('invalid_alert_type', 422)
        try:
            data['node_mac'] = normalize_mac(data['node_mac'])
        except ValueError:
            return error('invalid_node_mac', 422)
        node = next((n for n in nodes if n['node_mac'] == data['node_mac']), None)
        if node is None:
            return error('unknown_node_mac', 404)
        try:
            # Serialize persistence and projection so concurrent events cannot reorder memory.
            with lock:
                event, duplicate = store.accept(data, node, zones[node['node_id']])
                if not duplicate:
                    on_event(event)
            return jsonify(status='duplicate' if duplicate else 'accepted', event_id=event['id'],
                           node_id=event['node_id'], zone=event['zone'], source='physical',
                           state_applied=bool(event['state_applied']) and not duplicate,
                           received_at=event['received_at']), 200 if duplicate else 201
        except SequenceConflict:
            return error('sequence_conflict', 409)
        except Exception:
            app.logger.error('Physical event processing failed')
            return error('internal_error', 500)
