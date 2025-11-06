"""Test AI list rotation to verify different prompts generate different items."""
import http.client
import json
import time

def test_rotation():
    """Refresh a mood list and check if prompt changes."""
    
    # Get list of AI lists
    print("Fetching AI lists...")
    conn = http.client.HTTPConnection("localhost", 8000)
    conn.request("GET", "/api/ai/list?user_id=1")
    response = conn.getresponse()
    response_data = response.read().decode()
    print(f"Raw response: {response_data[:200]}")
    data = json.loads(response_data)
    conn.close()
    
    # Handle both list and dict responses
    if isinstance(data, dict):
        data = data.get("lists", [])
    
    # Find a mood list
    mood_lists = [l for l in data if isinstance(l, dict) and l.get("type") == "mood"]
    if not mood_lists:
        print("No mood lists found")
        return
    
    test_list = mood_lists[0]
    list_id = test_list["id"]
    original_prompt = test_list.get("prompt") or test_list.get("normalized_prompt")
    print(f"\nOriginal list: {list_id}")
    print(f"Original prompt: {original_prompt}")
    print(f"Original title: {test_list.get('generated_title')}")
    
    # Get current items
    conn = http.client.HTTPConnection("localhost", 8000)
    conn.request("GET", f"/api/ai/{list_id}/items")
    response = conn.getresponse()
    original_items = json.loads(response.read().decode())
    conn.close()
    original_titles = [item.get("title") for item in original_items[:10]]
    print(f"Original top 10 items: {original_titles}")
    
    # Trigger refresh
    print("\nTriggering refresh...")
    conn = http.client.HTTPConnection("localhost", 8000)
    headers = {"Content-Type": "application/json"}
    body = json.dumps({"user_id": 1})
    conn.request("POST", f"/api/ai/refresh/{list_id}", body, headers)
    response = conn.getresponse()
    refresh_result = json.loads(response.read().decode())
    conn.close()
    print(f"Refresh triggered: {refresh_result}")
    
    # Wait for processing (give it some time)
    print("\nWaiting 15 seconds for processing...")
    time.sleep(15)
    
    # Check new prompt and items
    conn = http.client.HTTPConnection("localhost", 8000)
    conn.request("GET", "/api/ai/list?user_id=1")
    response = conn.getresponse()
    data = json.loads(response.read().decode())
    conn.close()
    
    new_list = next((l for l in data if l["id"] == list_id), None)
    if not new_list:
        print("List not found after refresh")
        return
    
    new_prompt = new_list.get("prompt") or new_list.get("normalized_prompt")
    print(f"\nNew prompt: {new_prompt}")
    print(f"New title: {new_list.get('generated_title')}")
    print(f"Status: {new_list.get('status')}")
    
    # Get new items
    conn = http.client.HTTPConnection("localhost", 8000)
    conn.request("GET", f"/api/ai/{list_id}/items")
    response = conn.getresponse()
    new_items = json.loads(response.read().decode())
    conn.close()
    new_titles = [item.get("title") for item in new_items[:10]]
    print(f"New top 10 items: {new_titles}")
    
    # Analysis
    print("\n=== ANALYSIS ===")
    print(f"Prompt changed: {original_prompt != new_prompt}")
    print(f"Items changed: {original_titles != new_titles}")
    
    # Count how many titles are the same
    same_count = sum(1 for t in new_titles if t in original_titles)
    print(f"Same titles in top 10: {same_count}/10")
    
    if original_prompt == new_prompt:
        print("\n⚠️  ISSUE: Prompt did not change! Rotation may not be working.")
    elif same_count > 7:
        print("\n⚠️  WARNING: Most items are the same despite prompt change.")
        print("This could indicate FAISS/scoring is not respecting the new prompt.")
    else:
        print("\n✅ Rotation appears to be working correctly.")

if __name__ == "__main__":
    test_rotation()
