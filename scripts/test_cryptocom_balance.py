"""
Standalone test: authenticate to Crypto.com and fetch balance.
Run inside container: conda run -n hummingbot python3 /home/hummingbot/scripts/test_cryptocom_balance.py
"""
import asyncio
import hashlib
import hmac
import json
import time

import aiohttp
import yaml


def load_api_keys():
    """Load and decrypt API keys from the connector config."""
    import sys
    sys.path.insert(0, "/home/hummingbot")
    from hummingbot.client.config.security import Security
    from pathlib import Path

    # Try to decrypt using Security module
    try:
        Security.decrypt_all()
        from hummingbot.connector.exchange.cryptocom.cryptocom_utils import CryptocomConfigMap
        from hummingbot.client.config.config_helpers import ClientConfigAdapter
        import hummingbot.client.config.config_helpers as ch
        connector_config = ch.load_connector_config_map_from_file(
            Path("/home/hummingbot/conf/connectors/cryptocom.yml")
        )
        api_key = connector_config.cryptocom_api_key.get_secret_value()
        api_secret = connector_config.cryptocom_api_secret.get_secret_value()
        return api_key, api_secret
    except Exception as e:
        print(f"Could not decrypt via Security module: {e}")
        return None, None


def params_to_str(obj):
    result = ""
    if isinstance(obj, dict):
        for key in sorted(obj.keys()):
            result += key
            val = obj[key]
            if val is None:
                result += "null"
            elif isinstance(val, list):
                for item in val:
                    result += params_to_str(item)
            elif isinstance(val, dict):
                result += params_to_str(val)
            else:
                result += str(val)
    return result


def sign_request(body, api_key, secret_key):
    nonce = int(time.time() * 1000)
    body["api_key"] = api_key
    body["nonce"] = nonce
    method = body.get("method", "")
    req_id = body.get("id", 0)
    params = body.get("params", {})
    param_str = params_to_str(params) if params else ""
    payload = f"{method}{req_id}{api_key}{param_str}{nonce}"
    body["sig"] = hmac.new(
        secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return body


async def fetch_balance(api_key, secret_key):
    url = "https://api.crypto.com/exchange/v1/private/user-balance"
    body = {"id": 1, "method": "private/user-balance", "params": {}}
    body = sign_request(body, api_key, secret_key)
    print(f"\nPOST {url}")
    print(f"api_key prefix: {api_key[:8]}...")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers={"Content-Type": "application/json"}) as resp:
            text = await resp.text()
    result = json.loads(text)
    print(f"\nRaw response code: {result.get('code')}")
    if result.get("code") != 0:
        print(f"ERROR: {result.get('message', 'unknown')} (code {result.get('code')})")
        print(f"Full response: {json.dumps(result, indent=2)}")
        return
    data = result.get("result", {}).get("data", [])
    if not data:
        print("No balance data returned.")
        return
    print("\n=== BALANCES ===")
    for b in data[0].get("position_balances", []):
        qty = float(b.get("quantity", 0))
        if qty > 0:
            print(f"  {b['instrument_name']}: {qty} (available: {b.get('max_withdrawal_balance', '?')})")
    print("================\n")


async def main():
    api_key, secret_key = load_api_keys()
    if not api_key:
        print("Failed to load API keys.")
        return
    await fetch_balance(api_key, secret_key)


if __name__ == "__main__":
    asyncio.run(main())
