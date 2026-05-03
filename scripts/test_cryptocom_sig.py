"""
Direct signature test for Crypto.com API.
Decrypts keys from Hummingbot config and tests against live API.
Run: conda run -n hummingbot python3 /home/hummingbot/scripts/test_cryptocom_sig.py PASSWORD
"""
import asyncio
import hashlib
import hmac
import json
import sys
import time

import aiohttp


def decrypt_keys(password: str):
    import sys
    sys.path.insert(0, "/home/hummingbot")
    from hummingbot.client.config.config_crypt import ETHKeyFileSecretManger, validate_password
    from hummingbot.client.config.security import Security

    mgr = ETHKeyFileSecretManger(password)
    if not validate_password(mgr):
        print(f"Wrong password: {password!r}")
        return None, None

    Security.login(mgr)
    keys = Security.api_keys("cryptocom")
    if not keys:
        print("No keys found for 'cryptocom'")
        return None, None

    api_key = keys.get("cryptocom_api_key", "")
    api_secret = keys.get("cryptocom_api_secret", "")
    print(f"Decrypted api_key prefix: {api_key[:12]}...")
    print(f"Decrypted secret prefix:  {api_secret[:8]}...")
    return api_key, api_secret


def params_to_str(obj, level=0, max_level=3):
    if level >= max_level:
        return str(obj)
    result = ""
    if isinstance(obj, dict):
        for key in sorted(obj.keys()):
            result += key
            val = obj[key]
            if val is None:
                result += "null"
            elif isinstance(val, list):
                for item in val:
                    result += params_to_str(item, level + 1, max_level)
            elif isinstance(val, dict):
                result += params_to_str(val, level + 1, max_level)
            else:
                result += str(val)
    elif isinstance(obj, list):
        for item in obj:
            result += params_to_str(item, level + 1, max_level)
    else:
        result += str(obj)
    return result


def sign(body, api_key, secret_key):
    nonce = int(time.time() * 1000)
    body["api_key"] = api_key
    body["nonce"] = nonce
    method = body.get("method", "")
    req_id = body.get("id", 0)
    params = body.get("params", {})
    param_str = params_to_str(params) if params else ""
    payload = f"{method}{req_id}{api_key}{param_str}{nonce}"
    print(f"\nPayload to sign: {payload!r}")
    body["sig"] = hmac.new(
        secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    print(f"Signature: {body['sig'][:16]}...")
    return body


async def test_balance(api_key, secret_key):
    url = "https://api.crypto.com/exchange/v1/private/user-balance"
    body = {"id": 1, "method": "private/user-balance", "params": {}}
    body = sign(body, api_key, secret_key)
    print(f"\nPOSTing to {url}")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers={"Content-Type": "application/json"}) as resp:
            text = await resp.text()
    result = json.loads(text)
    print(f"\nResponse code: {result.get('code')}")
    if result.get("code") != 0:
        print(f"ERROR: {result}")
        return
    data = result.get("result", {}).get("data", [])
    print("\n=== BALANCES ===")
    for b in data[0].get("position_balances", []):
        qty = float(b.get("quantity", 0))
        if qty > 0:
            print(f"  {b['instrument_name']}: {qty}")
    print("================")


async def main():
    password = sys.argv[1] if len(sys.argv) > 1 else input("Enter Hummingbot password: ")
    api_key, secret_key = decrypt_keys(password)
    if not api_key:
        return
    await test_balance(api_key, secret_key)


if __name__ == "__main__":
    asyncio.run(main())
