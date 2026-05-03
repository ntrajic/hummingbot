#!/usr/bin/env python3
"""
Standalone test for Crypto.com Exchange API v1 auth.

Step 1: Verify signature against the known official docs example (no API keys needed).
Step 2: Hit the real API with your keys to fetch balance.

Usage:
  python test_crypto_com_auth.py                        # signature test only
  python test_crypto_com_auth.py YOUR_API_KEY YOUR_SECRET  # + live balance check
"""
import hashlib
import hmac
import json
import sys
import urllib.request

# ── Copy of the signing logic (no hummingbot imports needed) ──────────────────

_MAX_LEVEL = 3

def _params_to_str(obj, level):
    if level >= _MAX_LEVEL:
        return str(obj)
    result = ""
    if isinstance(obj, dict):
        for key in sorted(obj):
            result += key
            val = obj[key]
            if val is None:
                result += "null"
            elif isinstance(val, list):
                for item in val:
                    result += _params_to_str(item, level + 1)
            elif isinstance(val, dict):
                result += _params_to_str(val, level + 1)
            else:
                result += str(val)
    elif isinstance(obj, list):
        for item in obj:
            result += _params_to_str(item, level + 1)
    else:
        result += str(obj)
    return result

def sign(method, req_id, api_key, params, nonce, secret):
    param_str = _params_to_str(params, 0) if params else ""
    payload = f"{method}{req_id}{api_key}{param_str}{nonce}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

def build_body(method, params, api_key, secret, req_id, nonce):
    body = {"id": req_id, "method": method, "api_key": api_key,
            "params": params if params is not None else {}, "nonce": nonce}
    body["sig"] = sign(method, req_id, api_key, params, nonce, secret)
    return body

# ── Step 1: Official docs known-answer test ───────────────────────────────────
# From https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html#digital-signature
# Python example uses: method=private/create-order-list, id=14, nonce=<live>
# But the simpler known test from the JS example:
#   method="private/get-order-detail", id=11, api_key="token",
#   params={"order_id": 53287421324}, nonce=1587846358253, secret="secretKey"
# Expected param_str: "order_id53287421324"
# Expected payload:   "private/get-order-detail11tokenorder_id532874213241587846358253"

KNOWN_METHOD  = "private/get-order-detail"
KNOWN_ID      = 11
KNOWN_KEY     = "token"
KNOWN_SECRET  = "secretKey"
KNOWN_PARAMS  = {"order_id": 53287421324}
KNOWN_NONCE   = 1587846358253

param_str = _params_to_str(KNOWN_PARAMS, 0)
payload   = f"{KNOWN_METHOD}{KNOWN_ID}{KNOWN_KEY}{param_str}{KNOWN_NONCE}"
sig       = sign(KNOWN_METHOD, KNOWN_ID, KNOWN_KEY, KNOWN_PARAMS, KNOWN_NONCE, KNOWN_SECRET)

print("=== Step 1: Signature self-test ===")
print(f"  param_str : {param_str!r}")
print(f"  payload   : {payload!r}")
print(f"  signature : {sig}")

# Verify param_str is exactly "order_id53287421324"
assert param_str == "order_id53287421324", f"FAIL param_str: {param_str!r}"
# Verify payload
assert payload == "private/get-order-detail11tokenorder_id532874213241587846358253", \
    f"FAIL payload: {payload!r}"
print("  ✅ param_str and payload are CORRECT\n")

# ── Step 2: Live balance check ────────────────────────────────────────────────
if len(sys.argv) < 3:
    print("=== Step 2: Skipped (no API keys provided) ===")
    print("  Run:  python test_crypto_com_auth.py YOUR_API_KEY YOUR_SECRET")
    sys.exit(0)

API_KEY = sys.argv[1]
SECRET  = sys.argv[2]
URL     = "https://api.crypto.com/exchange/v1/private/user-balance"

import time
body = build_body("private/user-balance", {}, API_KEY, SECRET,
                  req_id=int(time.time() * 1000) % (2**31),
                  nonce=int(time.time() * 1000))

print("=== Step 2: Live balance check ===")
print(f"  Request body: {json.dumps(body, indent=2)}")

req = urllib.request.Request(
    URL,
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    print(f"\n  Response code: {data.get('code')}")
    if data.get("code") == 0:
        accounts = data.get("result", {}).get("data", [])
        for acct in accounts:
            print(f"\n  Account instrument: {acct.get('instrument_name')}")
            print(f"  Total cash balance: {acct.get('total_cash_balance')}")
            print(f"  Available balance:  {acct.get('total_available_balance')}")
            for pos in acct.get("position_balances", []):
                print(f"    {pos['instrument_name']}: qty={pos.get('quantity')}  "
                      f"max_withdrawal={pos.get('max_withdrawal_balance')}")
        print("\n  ✅ Authentication SUCCESSFUL")
    else:
        print(f"  ❌ API error: {data.get('message', data)}")
        if data.get("code") == 40101:
            print("  → UNAUTHORIZED: API key or signature is wrong")
        elif data.get("code") == 40102:
            print("  → INVALID_NONCE: system clock is off by >60s")
except Exception as e:
    print(f"  ❌ Request failed: {e}")
