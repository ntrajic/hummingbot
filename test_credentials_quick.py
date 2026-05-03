#!/usr/bin/env python3
"""
Quick test script to verify Crypto.com API credentials work.
Edit the API_KEY and SECRET variables with your actual credentials, then run:
python3 test_credentials_quick.py
"""
import hmac
import hashlib
import time
import json
import urllib.request
import urllib.error

# ========== EDIT THESE WITH YOUR ACTUAL CREDENTIALS ==========
API_KEY = "YOUR_CRYPTO_COM_API_KEY_HERE"
SECRET  = "YOUR_CRYPTO_COM_API_SECRET_HERE"
# ===========================================================

def test_credentials():
    print("=" * 70)
    print("CRYPTO.COM CREDENTIALS TEST")
    print("=" * 70)
    
    if API_KEY == "YOUR_CRYPTO_COM_API_KEY_HERE":
        print("\n❌ ERROR: You must edit the script and add your actual credentials!")
        print("   Edit lines 11-12 with your Crypto.com API key and secret.")
        return False
    
    print(f"\n[1] API Key (first 10 chars): {API_KEY[:10]}...")
    print(f"    Secret Key (first 10 chars): {SECRET[:10]}...")
    
    # Prepare request
    nonce = int(time.time() * 1000)
    body = {
        'id': 1,
        'method': 'private/user-balance',
        'params': {},
        'api_key': API_KEY,
        'nonce': nonce,
    }
    
    # Sign request
    payload = f"private/user-balance1{API_KEY}{nonce}"
    sig = hmac.new(
        SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    
    body['sig'] = sig
    
    print(f"\n[2] Request payload (first 100 chars): {payload[:100]}...")
    print(f"    Signature (first 20 chars): {sig[:20]}...")
    
    # Send request
    print(f"\n[3] Sending POST request to Crypto.com...")
    req = urllib.request.Request(
        'https://api.crypto.com/exchange/v1/private/user-balance',
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        resp = urllib.request.urlopen(req)
        result = resp.read().decode()
        result_json = json.loads(result)
        
        print(f"    ✓ SUCCESS! Response status: {resp.status}")
        print(f"    ✓ Response: {json.dumps(result_json, indent=2)[:500]}...")
        return True
        
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"    ✗ HTTP {e.code}: {error_body}")
        
        try:
            error_json = json.loads(error_body)
            if error_json.get('code') == 40101:
                print("\n    ⚠ ERROR 40101: Authentication Failure")
                print("    This means your API key or secret is INCORRECT or EXPIRED.")
                print("    Please verify your credentials in Crypto.com account settings.")
                return False
        except:
            pass
        
        return False

if __name__ == "__main__":
    success = test_credentials()
    print("\n" + "=" * 70)
    if success:
        print("✓ Your credentials work! You can now configure them in Hummingbot.")
    else:
        print("✗ Credentials verification failed. Fix your API key/secret and try again.")
    print("=" * 70)
