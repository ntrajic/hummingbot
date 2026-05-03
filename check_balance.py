#!/usr/bin/env python3
"""Diagnose Crypto.com balance response."""
import asyncio
import hashlib
import hmac
import json
import time
import aiohttp

API_KEY = "YOUR_API_KEY"
SECRET_KEY = "YOUR_SECRET_KEY"
URL = "https://api.crypto.com/exchange/v1/private/user-balance"


def sign(api_key, secret_key, method, params=None):
    nonce = int(time.time() * 1000)
    req_id = 1
    params = params or {}
    param_str = ""
    body = {
        "id": req_id,
        "method": method,
        "params": params,
        "api_key": api_key,
        "nonce": nonce,
    }
    payload = f"{method}{req_id}{api_key}{param_str}{nonce}"
    body["sig"] = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return body


async def main():
    body = sign(API_KEY, SECRET_KEY, "private/user-balance")
    async with aiohttp.ClientSession() as session:
        async with session.post(URL, json=body, headers={"Content-Type": "application/json"}) as resp:
            data = await resp.json()
            print(json.dumps(data, indent=2))

    # Show what the connector would parse
    accounts = data.get("result", {}).get("data", [])
    if accounts:
        print("\n--- Parsed balances ---")
        for b in accounts[0].get("position_balances", []):
            print(f"  {b['instrument_name']}: qty={b.get('quantity')} available={b.get('max_withdrawal_balance')}")
    else:
        print("\nNo accounts in response. Full result:", data.get("result"))

asyncio.run(main())
