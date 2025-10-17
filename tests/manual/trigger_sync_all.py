#!/usr/bin/env python3
import json
import http.client
import traceback

def post(path, payload=None, timeout=600):
    try:
        conn = http.client.HTTPConnection('127.0.0.1', 8000, timeout=timeout)
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'Content-Type': 'application/json'} if payload is not None else {}
        conn.request('POST', path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode()
        print(f"Status: {resp.status}")
        print(f"Response: {data}")
        return resp.status, data
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        return None, None

if __name__ == '__main__':
    print("Triggering sync of all lists...")
    post('/api/smartlists/sync', {'force_full': True, 'user_id': 1})
