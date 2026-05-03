#!/usr/bin/env python3
"""
Diagnostic script to check Crypto.com credentials decryption and authentication.
"""
import sys
import json
import os
from pathlib import Path

sys.path.insert(0, "/workspaces/hummingbot")

def main():
    print("=" * 80)
    print("CRYPTO.COM CREDENTIALS DIAGNOSTIC")
    print("=" * 80)
    
    # Step 1: Check if config file exists
    config_file = Path("/workspaces/hummingbot/conf/connectors/cryptocom.yml")
    print(f"\n[1] Config file exists: {config_file.exists()}")
    
    if not config_file.exists():
        print("Config file not found!")
        return
    
    # Step 2: Read raw config
    with open(config_file, 'r') as f:
        config_lines = f.readlines()
    
    print(f"[2] Raw config lines: {len(config_lines)}")
    for i, line in enumerate(config_lines):
        if 'api_key' in line or 'api_secret' in line:
            key_type = 'api_key' if 'api_key' in line else 'api_secret'
            value = line.split(':', 1)[1].strip()
            print(f"    {key_type}: {value[:50]}...")
    
    # Step 3: Try to load and decrypt using Hummingbot's Security module
    print("\n[3] Attempting to decrypt credentials...")
    try:
        from hummingbot.client.config.security import Security
        from hummingbot.client.config.config_helpers import load_connector_config_map_from_file
        
        Security.decrypt_all()
        print("    ✓ Security.decrypt_all() succeeded")
        
        connector_config = load_connector_config_map_from_file(config_file)
        print("    ✓ Config loaded")
        
        api_key = connector_config.cryptocom_api_key.get_secret_value()
        api_secret = connector_config.cryptocom_api_secret.get_secret_value()
        
        print(f"    ✓ Decrypted api_key: {api_key[:10]}...{api_key[-10:]} (len={len(api_key)})")
        print(f"    ✓ Decrypted api_secret: {api_secret[:10]}...{api_secret[-10:]} (len={len(api_secret)})")
        
        return api_key, api_secret
        
    except Exception as e:
        print(f"    ✗ Decryption failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        
    # Step 4: Try manual decryption
    print("\n[4] Attempting manual hex decoding...")
    try:
        import yaml
        with open(config_file, 'r') as f:
            raw_config = yaml.safe_load(f)
        
        encrypted_key = raw_config.get('cryptocom_api_key', '')
        encrypted_secret = raw_config.get('cryptocom_api_secret', '')
        
        if encrypted_key.startswith('7b'):  # Looks like hex
            try:
                decoded_key = bytes.fromhex(encrypted_key).decode('utf-8')
                decoded_key_json = json.loads(decoded_key)
                print(f"    ✓ Hex-decoded api_key: {decoded_key[:100]}...")
                print(f"    ✓ JSON structure: {list(decoded_key_json.keys())}")
                
                # This is encrypted with AES - need to decrypt
                if 'crypto' in decoded_key_json:
                    print("    ⚠ Credentials are AES-128-CTR encrypted (need password to decrypt)")
                    print(f"    ⚠ Cipher: {decoded_key_json['crypto'].get('cipher')}")
                    print(f"    ⚠ KDF: {decoded_key_json['crypto'].get('kdf')}")
            except json.JSONDecodeError:
                print(f"    ✗ Not valid JSON after hex decode")
        else:
            print(f"    ✗ Credential doesn't look hex-encoded")
            
    except Exception as e:
        print(f"    ✗ Manual decode failed: {e}")

if __name__ == "__main__":
    main()
