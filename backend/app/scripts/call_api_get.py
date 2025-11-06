"""
call_api_get.py

Simple helper to GET phases endpoints from inside the container.
"""
import http.client

HOST = "127.0.0.1"
PORT = 8000

ENDPOINTS = [
    "/api/users/1/phases",
    "/api/users/1/phases/current",
    "/api/users/1/phases/timeline",
]

def main():
    for path in ENDPOINTS:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read().decode()
            print(f"GET {path} -> {resp.status}")
            print(body)
        finally:
            conn.close()

if __name__ == "__main__":
    main()
