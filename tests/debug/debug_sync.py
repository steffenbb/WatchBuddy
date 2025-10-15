import sys
import json
import http.client

def post(path, payload=None, timeout=300):
    conn = http.client.HTTPConnection('127.0.0.1', 8000, timeout=timeout)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'} if payload is not None else {}
    conn.request('POST', path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    print(resp.status)
    print(data)
    return resp.status, data


def get(path, timeout=120):
    conn = http.client.HTTPConnection('127.0.0.1', 8000, timeout=timeout)
    conn.request('GET', path)
    resp = conn.getresponse()
    data = resp.read().decode()
    print(resp.status)
    print(data)
    return resp.status, data

async def test_bulk_provider():
    from app.services.bulk_candidate_provider import BulkCandidateProvider
    provider = BulkCandidateProvider(1)
    try:
        candidates = await provider.get_candidates(
            media_type="movies",
            limit=20,
            languages=["da"],
            genres=["action", "comedy", "drama"],
            discovery="balanced"
        )
        print(f"Candidates returned: {len(candidates)}")
        for c in candidates[:3]:
            print(f" - {c.get('title', 'No Title')} ({c.get('year')}) [{c.get('trakt_id')}]")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: debug_sync.py <list_id> [items|test]')
        sys.exit(1)
    list_id = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else 'sync'
    if mode == 'items':
        get(f'/api/smartlists/{list_id}/items?limit=10')
    elif mode == 'test':
        import asyncio
        asyncio.run(test_bulk_provider())
    else:
        post(f'/api/smartlists/sync/{list_id}', {'force_full': True})