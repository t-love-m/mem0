#!/usr/bin/env python3
"""
Official Client SDK Compatibility Verification Script

模拟官方 SDK（如 mem0ai npm 包与 Python client）向自建兼容服务发送请求，
用于在二次开发过程中自动化验证接口协议是否与官方云平台完全吻合。
"""

import sys
import requests

BASE_URL = "http://127.0.0.1:8888"
HEADERS = {
    "Authorization": "Token test_api_key",
    "Content-Type": "application/json",
}


def test_ping():
    print("\n[1] Testing GET /v1/ping/ ...")
    url = f"{BASE_URL}/v1/ping/"
    res = requests.get(url, headers=HEADERS)
    print(f"Status: {res.status_code}")
    print(f"Response: {res.text}")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert data.get("status") == "ok", "Expected status: ok"
    assert data.get("userEmail"), "Expected non-empty userEmail"
    print("✓ ping passed!")


def test_add():
    print("\n[2] Testing POST /v3/memories/add/ ...")
    url = f"{BASE_URL}/v3/memories/add/"
    payload = {
        "messages": [
            {"role": "user", "content": "I like writing code in TypeScript and Python."},
            {"role": "assistant", "content": "Got it! I will remember your language preferences."}
        ],
        "user_id": "test_user",
        "app_id": "my_project",
        "run_id": "session_123",
        "infer": True
    }
    res = requests.post(url, json=payload, headers=HEADERS)
    print(f"Status: {res.status_code}")
    print(f"Response: {res.text}")
    assert res.status_code in (200, 201), f"Expected 200/201, got {res.status_code}"
    data = res.json()
    assert "results" in data, "Expected 'results' key in response"
    print("✓ add passed!")


def test_list():
    print("\n[3] Testing POST /v3/memories/ (getAll) ...")
    url = f"{BASE_URL}/v3/memories/"
    payload = {
        "filters": {
            "user_id": "test_user",
            "app_id": "my_project"
        }
    }
    res = requests.post(url, json=payload, headers=HEADERS)
    print(f"Status: {res.status_code}")
    print(f"Response: {res.text}")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert "results" in data, "Expected 'results' in list response"
    assert "count" in data, "Expected 'count' in list response"
    print("✓ list (getAll) passed!")


def test_search():
    print("\n[4] Testing POST /v3/memories/search/ ...")
    url = f"{BASE_URL}/v3/memories/search/"
    payload = {
        "query": "What languages do I prefer?",
        "filters": {
            "user_id": "test_user",
            "app_id": "my_project"
        },
        "threshold": 0.3
    }
    res = requests.post(url, json=payload, headers=HEADERS)
    print(f"Status: {res.status_code}")
    print(f"Response: {res.text}")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert "results" in data, "Expected 'results' in search response"
    print("✓ search passed!")


if __name__ == "__main__":
    print(f"Starting SDK Compatibility Test against {BASE_URL}...")
    try:
        test_ping()
        test_add()
        test_list()
        test_search()
        print("\nAll Compatibility Tests Passed Successfully! 🎉")
    except Exception as e:
        print(f"\n❌ Test Failed: {e}", file=sys.stderr)
        sys.exit(1)
