import hashlib
import hmac
import time
from typing import Any, Dict

from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTRequest, WSRequest


class CryptocomAuth(AuthBase):
    """
    Handles authentication for Crypto.com Exchange API v1.

    Signature algorithm (from docs):
      1. Sort params keys ascending, concatenate as key+value
      2. payload = method + id + api_key + param_string + nonce
      3. sig = HMAC-SHA256(payload, secret_key).hexdigest()
    """

    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key.strip()
        self.secret_key = secret_key.strip()

    @staticmethod
    def _params_to_str(obj: Any, level: int = 0, max_level: int = 3) -> str:
        """Recursively serialize params dict to string for signing (Crypto.com spec)."""
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
                        result += CryptocomAuth._params_to_str(item, level + 1, max_level)
                elif isinstance(val, dict):
                    result += CryptocomAuth._params_to_str(val, level + 1, max_level)
                else:
                    result += str(val)
        elif isinstance(obj, list):
            for item in obj:
                result += CryptocomAuth._params_to_str(item, level + 1, max_level)
        else:
            result += str(obj)
        return result

    def sign_request(self, request_body: Dict[str, Any]) -> Dict[str, Any]:
        """Add api_key, nonce, and sig to a request body dict."""
        nonce = int(time.time() * 1000)
        request_body["api_key"] = self.api_key
        request_body["nonce"] = nonce

        method = request_body.get("method", "")
        req_id = request_body.get("id", 0)
        params = request_body.get("params", {})
        param_str = self._params_to_str(params) if params else ""

        payload = f"{method}{req_id}{self.api_key}{param_str}{nonce}"
        request_body["sig"] = hmac.new(
            self.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return request_body

    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:
        """Sign a REST request by injecting auth fields into the JSON body."""
        import json
        body = json.loads(request.data) if request.data else {}
        body = self.sign_request(body)
        request.data = json.dumps(body)
        if request.headers is None:
            request.headers = {}
        request.headers["Content-Type"] = "application/json"
        return request

    async def ws_authenticate(self, request: WSRequest) -> WSRequest:
        """WebSocket auth is done via public/auth message, not per-request signing."""
        return request

    def get_ws_auth_payload(self) -> Dict[str, Any]:
        """Build the public/auth WebSocket message."""
        nonce = int(time.time() * 1000)
        req_id = 1
        method = "public/auth"
        payload = f"{method}{req_id}{self.api_key}{nonce}"
        sig = hmac.new(
            self.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "id": req_id,
            "method": method,
            "api_key": self.api_key,
            "sig": sig,
            "nonce": nonce,
        }
