#!/usr/bin/env python
"""
Manual test script for queue endpoints.
Run Django server first, then run this script to test the queue.

Usage:
    python test_queue_manually.py
"""

import requests
import time
import sys

# Configuration
DJANGO_API = "http://localhost:8000/api"
USERNAME = "your_username"  # Change this
PASSWORD = "your_password"  # Change this


def test_queue_endpoints():
    """Test all queue endpoints."""

    print("=" * 60)
    print("Testing Job Queue Endpoints")
    print("=" * 60)

    # Step 1: Get JWT token
    print("\n1. Getting JWT token...")
    try:
        response = requests.post(
            f"{DJANGO_API}/token/",
            json={"username": USERNAME, "password": PASSWORD}
        )
        response.raise_for_status()
        jwt_token = response.json()['access']
        print(f"✓ Got JWT token: {jwt_token[:20]}...")
    except Exception as e:
        print(f"✗ Failed to get JWT token: {e}")
        print("  Make sure Django server is running and USERNAME/PASSWORD are correct")
        return False

    headers_jwt = {"Authorization": f"Bearer {jwt_token}"}

    # Step 2: Submit URL to queue
    print("\n2. Submitting URL to queue...")
    try:
        response = requests.post(
            f"{DJANGO_API}/queue/submit",
            json={"url": "https://library.needham.ma.us/calendar/"},
            headers=headers_jwt
        )
        response.raise_for_status()
        job = response.json()
        print(f"✓ Job created: ID={job['id']}, status={job['status']}, priority={job['priority']}")
    except Exception as e:
        print(f"✗ Failed to submit job: {e}")
        return False

    # Step 3: Check queue status
    print("\n3. Checking queue status...")
    try:
        response = requests.get(
            f"{DJANGO_API}/queue/status",
            headers=headers_jwt
        )
        response.raise_for_status()
        status = response.json()
        print(f"✓ Queue status:")
        print(f"  - Pending: {status['queue_depth']}")
        print(f"  - Processing: {status['processing']}")
        print(f"  - Completed (24h): {status['completed_24h']}")
        print(f"  - Failed (24h): {status['failed_24h']}")
    except Exception as e:
        print(f"✗ Failed to get queue status: {e}")
        return False

    # Step 4: Bulk submit
    print("\n4. Testing bulk submit...")
    try:
        response = requests.post(
            f"{DJANGO_API}/queue/bulk-submit",
            json={"urls": [
                "https://example.com/events1",
                "https://example.com/events2",
                "https://example.com/events3"
            ]},
            headers=headers_jwt
        )
        response.raise_for_status()
        result = response.json()
        print(f"✓ Bulk submitted: {result['submitted']} jobs")
        print(f"  Job IDs: {result['job_ids']}")
    except Exception as e:
        print(f"✗ Failed bulk submit: {e}")
        return False

    # Step 5: Test service token auth (for workers)
    print("\n5. Testing service token endpoints...")
    print("   (This requires a service token - create one in Django admin)")
    print("   Then run: export SERVICE_TOKEN='your_token_here'")
    print("   Skip this test for now if you don't have one.")

    # Step 6: Summary
    print("\n" + "=" * 60)
    print("✓ All accessible endpoints working!")
    print("\nNext steps:")
    print("1. Create a service token in Django admin")
    print("2. Test the worker endpoints (/queue/next, /queue/{id}/complete)")
    print("3. Run the local worker: python local_worker.py")
    print("=" * 60)

    return True


if __name__ == "__main__":
    if USERNAME == "your_username":
        print("ERROR: Please edit this script and set USERNAME and PASSWORD")
        sys.exit(1)

    success = test_queue_endpoints()
    sys.exit(0 if success else 1)
