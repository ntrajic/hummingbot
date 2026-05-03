#!/usr/bin/env python3
"""
Diagnostic script to verify Crypto.com credentials decryption and authentication.
Run: python3 /workspaces/hummingbot/scripts/diagnostic_cryptocom_auth.py
"""

import sys
import os
import json
from pathlib import Path

# Add hummingbot to path
sys.path.insert(0, "/workspaces/hummingbot")

def test_security_module():
    """Test if Security module is working properly."""
    print("\n" + "="*60)
    print("TEST 1: Security Module Configuration")
    print("="*60)
    
    try:
        from hummingbot.client.config.security import Security
        print("✓ Security module imported successfully")
        
        # Check if security is initialized
        print(f"  - Security password set: {Security.password is not None}")
        print(f"  - Encryption key exists: {hasattr(Security, 'encrypted_secret_key')}")
        
        return True
    except Exception as e:
        print(f"✗ Error importing Security module: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_file_format():
    """Check the raw format of the config file."""
    print("\n" + "="*60)
    print("TEST 2: Config File Format")
    print("="*60)
    
    config_path = Path("/workspaces/hummingbot/conf/connectors/cryptocom.yml")
    
    if not config_path.exists():
        print(f"✗ Config file not found: {config_path}")
        return False
    
    print(f"✓ Config file exists: {config_path}")
    
    try:
        import yaml
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        api_key_raw = config.get("cryptocom_api_key", "")
        api_secret_raw = config.get("cryptocom_api_secret", "")
        
        print(f"  - API Key length: {len(api_key_raw)} chars")
        print(f"  - API Secret length: {len(api_secret_raw)} chars")
        
        # Check if they look encrypted (hex-encoded JSON)
        if api_key_raw.startswith("7b"):  # "7b" is "{" in hex
            print("  - API Key appears to be HEX-ENCODED (encrypted)")
            try:
                decoded = bytes.fromhex(api_key_raw).decode('utf-8')
                parsed = json.loads(decoded)
                print(f"    - Contains crypto structure: {list(parsed.keys())}")
                print(f"    - Cipher type: {parsed.get('crypto', {}).get('cipher', 'unknown')}")
            except Exception as e:
                print(f"    - Failed to decode: {e}")
        else:
            print("  - API Key looks like plaintext")
        
        return True
    except Exception as e:
        print(f"✗ Error reading config: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_loading():
    """Test if credentials can be loaded and decrypted."""
    print("\n" + "="*60)
    print("TEST 3: Load & Decrypt Credentials")
    print("="*60)
    
    try:
        from hummingbot.client.config.security import Security
        from pathlib import Path
        import yaml
        
        # First, try to decrypt all configs
        print("  - Attempting to decrypt all configs...")
        Security.decrypt_all()
        print("  ✓ Decrypt_all() completed")
        
        # Now try to load the connector config
        from hummingbot.client.config.config_helpers import load_connector_config_map_from_file
        
        config_path = Path("/workspaces/hummingbot/conf/connectors/cryptocom.yml")
        print(f"  - Loading config from: {config_path}")
        
        connector_config = load_connector_config_map_from_file(config_path)
        print("  ✓ Config map loaded")
        
        # Try to get the secret values
        try:
            api_key = connector_config.cryptocom_api_key.get_secret_value()
            api_secret = connector_config.cryptocom_api_secret.get_secret_value()
            
            print(f"  ✓ API Key decrypted (length: {len(api_key)})")
            print(f"    - First 8 chars: {api_key[:8]}")
            print(f"    - Last 8 chars: {api_key[-8:]}")
            
            print(f"  ✓ API Secret decrypted (length: {len(api_secret)})")
            print(f"    - First 8 chars: {api_secret[:8]}")
            print(f"    - Last 8 chars: {api_secret[-8:]}")
            
            # Check for common issues
            if len(api_key) < 10:
                print("  ⚠ WARNING: API Key seems too short!")
            if len(api_secret) < 20:
                print("  ⚠ WARNING: API Secret seems too short!")
            
            if api_key.startswith("7b2263727970746f"):  # Still hex
                print("  ✗ ERROR: Keys are still hex-encoded! Decryption may have failed")
                return False
            
            return True
        except Exception as e:
            print(f"  ✗ Error getting secret values: {e}")
            import traceback
            traceback.print_exc()
            return False
            
    except Exception as e:
        print(f"  ✗ Error in config loading test: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_authentication_object():
    """Test creating the authentication object."""
    print("\n" + "="*60)
    print("TEST 4: Create Authentication Object")
    print("="*60)
    
    try:
        from hummingbot.client.config.security import Security
        from hummingbot.client.config.config_helpers import load_connector_config_map_from_file
        from hummingbot.connector.exchange.cryptocom.cryptocom_auth import CryptocomAuth
        from pathlib import Path
        
        Security.decrypt_all()
        config_path = Path("/workspaces/hummingbot/conf/connectors/cryptocom.yml")
        connector_config = load_connector_config_map_from_file(config_path)
        
        api_key = connector_config.cryptocom_api_key.get_secret_value()
        api_secret = connector_config.cryptocom_api_secret.get_secret_value()
        
        print(f"  - Creating CryptocomAuth with keys...")
        auth = CryptocomAuth(api_key=api_key, secret_key=api_secret)
        
        print(f"  ✓ Auth object created")
        print(f"    - API Key in auth: {auth.api_key[:8]}...")
        print(f"    - Secret Key in auth: {auth.secret_key[:8]}...")
        
        # Test signing a request
        print(f"  - Testing signature generation...")
        test_request = {
            "id": 1,
            "method": "private/user-balance",
            "params": {}
        }
        
        signed = auth.sign_request(test_request.copy())
        
        print(f"  ✓ Request signed successfully")
        print(f"    - Signature (first 16 chars): {signed.get('sig', '')[:16]}...")
        print(f"    - Nonce: {signed.get('nonce')}")
        
        return True
    except Exception as e:
        print(f"  ✗ Error in auth test: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("\n" + "="*60)
    print("CRYPTO.COM AUTHENTICATION DIAGNOSTIC")
    print("="*60)
    
    results = {
        "Security Module": test_security_module(),
        "Config Format": test_config_file_format(),
        "Load & Decrypt": test_config_loading(),
        "Auth Object": test_authentication_object(),
    }
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status} - {test_name}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n✓ All diagnostics passed!")
        print("  - Your credentials appear to be properly configured")
        print("  - Issue likely: Crypto.com API key/secret are incorrect or revoked")
        print("  - Solution: Verify in Crypto.com dashboard and re-enter if needed")
    else:
        print("\n✗ Some diagnostics failed!")
        print("  - Check the errors above for details")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
