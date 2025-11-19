"""
Test script for UserTextProfile generation

Tests the new generate_user_text_profile task and API endpoints.
Run from host with: docker exec -i watchbuddy-backend-1 python /app/tests/manual/test_user_profile.py
"""
import http.client
import json
import time


def test_user_profile_status():
    """Check if UserTextProfile exists for user 1."""
    print("\n=== Testing User Profile Status ===")
    conn = http.client.HTTPConnection("localhost", 8000)
    
    try:
        conn.request("GET", "/api/maintenance/user-profile-status?user_id=1")
        response = conn.getresponse()
        data = json.loads(response.read().decode())
        
        print(f"Status Code: {response.status}")
        print(f"Profile Exists: {data.get('exists')}")
        
        if data.get('exists'):
            print(f"Age (days): {data.get('age_days')}")
            print(f"Summary Length: {data.get('summary_length')}")
            print(f"Tags Count: {data.get('tags_count')}")
            print(f"Created: {data.get('created_at')}")
            print(f"Updated: {data.get('updated_at')}")
        
        return data
    finally:
        conn.close()


def test_generate_profile(force=False):
    """Trigger UserTextProfile generation."""
    print(f"\n=== Testing Profile Generation (force={force}) ===")
    conn = http.client.HTTPConnection("localhost", 8000)
    
    try:
        endpoint = f"/api/maintenance/generate-user-profile?user_id=1&force={'true' if force else 'false'}"
        conn.request("POST", endpoint)
        response = conn.getresponse()
        data = json.loads(response.read().decode())
        
        print(f"Status Code: {response.status}")
        print(f"Response Status: {data.get('status')}")
        print(f"Message: {data.get('message')}")
        
        if data.get('task_id'):
            print(f"Task ID: {data.get('task_id')}")
            return data.get('task_id')
        
        return None
    finally:
        conn.close()


def test_profile_retrieval():
    """Retrieve full UserTextProfile from database."""
    print("\n=== Testing Profile Retrieval ===")
    
    from app.core.database import SessionLocal
    from app.models import UserTextProfile
    
    db = SessionLocal()
    try:
        profile = db.query(UserTextProfile).filter_by(user_id=1).first()
        
        if profile:
            print(f"✅ Profile found for user 1")
            print(f"\nSummary Text ({len(profile.summary_text)} chars):")
            print(f"  {profile.summary_text}")
            
            if profile.tags_json:
                import json
                tags = json.loads(profile.tags_json)
                print(f"\nTags ({len(tags)} total):")
                print(f"  {', '.join(tags[:10])}{'...' if len(tags) > 10 else ''}")
            
            print(f"\nTimestamps:")
            print(f"  Created: {profile.created_at}")
            print(f"  Updated: {profile.updated_at}")
        else:
            print("❌ No profile found for user 1")
    finally:
        db.close()


def main():
    """Run all tests."""
    print("=" * 60)
    print("UserTextProfile Generation Test Suite")
    print("=" * 60)
    
    # Test 1: Check current status
    status = test_user_profile_status()
    
    # Test 2: Trigger generation (skip if recent)
    if not status.get('exists') or status.get('age_days', 0) >= 7:
        task_id = test_generate_profile(force=False)
        if task_id:
            print(f"\n⏳ Waiting 20 seconds for task completion...")
            time.sleep(20)
    else:
        print("\n⏩ Skipping generation - profile is recent")
    
    # Test 3: Retrieve profile from database
    test_profile_retrieval()
    
    # Test 4: Force regeneration
    print("\n" + "=" * 60)
    response = input("\nForce regenerate profile? (y/N): ")
    if response.lower() == 'y':
        task_id = test_generate_profile(force=True)
        if task_id:
            print(f"\n⏳ Waiting 20 seconds for task completion...")
            time.sleep(20)
            test_profile_retrieval()
    
    print("\n" + "=" * 60)
    print("✅ Test suite complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
