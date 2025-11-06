"""
call_api_refresh.py

Simple helper to POST to the refresh phases endpoint from inside the container.
"""
import http.client
import json

HOST = "127.0.0.1"
PORT = 8000
PATH = "/api/users/1/phases/refresh"

def main():
    conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
    try:
        payload = json.dumps({"user_id": 1}).encode()
        headers = {"Content-Type": "application/json"}
        conn.request("POST", PATH, body=payload, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode()
        print(f"status={resp.status}")
        print(body)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
