1"""
Test script for Trakt redirect URI endpoints.
"""
import http.client
import json

def test_redirect_uri_endpoints():
    """Test GET and POST endpoints for Trakt redirect URI."""
    
    # Test GET endpoint
    print("=" * 60)
    print("Testing GET /api/trakt/redirect-uri")
    print("=" * 60)
    
    # Connect to backend service name from within Docker network
    conn = http.client.HTTPConnection("backend", 8000)
    conn.request("GET", "/api/trakt/redirect-uri")
    response = conn.getresponse()
    data = response.read()
    
    print(f"Status: {response.status}")
    print(f"Response: {data.decode('utf-8')}")
    
    if response.status == 200:
        result = json.loads(data)
        print(f"\nCurrent redirect_base: {result.get('redirect_base')}")
        print(f"Full redirect URI: {result.get('full_redirect_uri')}")
    
    conn.close()
    
    # Test POST endpoint with a custom value
    print("\n" + "=" * 60)
    print("Testing POST /api/trakt/redirect-uri (set to example.com)")
    print("=" * 60)
    
    conn = http.client.HTTPConnection("backend", 8000)
    headers = {"Content-Type": "application/json"}
    body = json.dumps({"redirect_uri": "example.com"})
    
    conn.request("POST", "/api/trakt/redirect-uri", body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    
    print(f"Status: {response.status}")
    print(f"Response: {data.decode('utf-8')}")
    
    if response.status == 200:
        result = json.loads(data)
        print(f"\nUpdated redirect_base: {result.get('redirect_base')}")
        print(f"Full redirect URI: {result.get('full_redirect_uri')}")
    
    conn.close()
    
    # Verify the change persisted
    print("\n" + "=" * 60)
    print("Verifying change persisted (GET again)")
    print("=" * 60)
    
    conn = http.client.HTTPConnection("backend", 8000)
    conn.request("GET", "/api/trakt/redirect-uri")
    response = conn.getresponse()
    data = response.read()
    
    print(f"Status: {response.status}")
    print(f"Response: {data.decode('utf-8')}")
    
    conn.close()
    
    # Reset to localhost
    print("\n" + "=" * 60)
    print("Resetting to localhost")
    print("=" * 60)
    
    conn = http.client.HTTPConnection("backend", 8000)
    headers = {"Content-Type": "application/json"}
    body = json.dumps({"redirect_uri": "localhost"})
    
    conn.request("POST", "/api/trakt/redirect-uri", body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    
    print(f"Status: {response.status}")
    print(f"Response: {data.decode('utf-8')}")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("✅ All tests completed!")
    print("=" * 60)

if __name__ == "__main__":
    test_redirect_uri_endpoints()
