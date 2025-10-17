import requests
import time

# Get all lists
response = requests.get("http://localhost:8000/api/lists/?user_id=1")
lists = response.json()

print(f"Found {len(lists)} lists to sync")

for lst in lists:
    list_id = lst['id']
    title = lst['title']
    print(f"\n=== Syncing list {list_id}: {title} ===")
    
    try:
        sync_response = requests.post(
            f"http://localhost:8000/api/smartlists/sync/{list_id}",
            json={"user_id": 1, "force_full": True}
        )
        
        if sync_response.status_code == 200:
            print(f"✓ List {list_id} sync initiated")
        else:
            print(f"✗ List {list_id} failed: {sync_response.status_code} - {sync_response.text}")
    except Exception as e:
        print(f"✗ List {list_id} error: {e}")
    
    # Small delay between syncs
    time.sleep(2)

print("\n=== All sync requests sent ===")
print("Waiting for syncs to complete (60 seconds)...")
time.sleep(60)
print("Done waiting.")
