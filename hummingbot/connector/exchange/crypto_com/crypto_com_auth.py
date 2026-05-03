import hashlib
import hmac
import time
from typing import Any, Dict, Optional

from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTRequest, WSRequest

_MAX_LEVEL = 3


def _params_to_str(obj: Any, level: int) -> str:
    """
    Serialize params for signing per official Crypto.com Exchange API v1 docs.
    Sort dict keys ascending, concatenate key+value (no delimiters).
    Recurse into nested dicts/lists up to MAX_LEVEL=3.
    None -> literal 'null'.
    """
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


def _sign(method: str, req_id: int, api_key: str, params: Optional[Dict], nonce: int, secret: str) -> str:
    """HMAC-SHA256 of: method + id + api_key + param_str + nonce"""
    param_str = _params_to_str(params, 0) if params else ""
    payload = f"{method}{req_id}{api_key}{param_str}{nonce}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


class CryptoComAuth(AuthBase):
    """
    Auth for Crypto.com Exchange API v1.
    Private REST calls are POST with a signed JSON body.
    WS sessions require a one-time public/auth message.
    """

    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key

    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:
        # Signing is done at the body-building stage via build_signed_body().
        return request

    async def ws_authenticate(self, request: WSRequest) -> WSRequest:
        return request

    def build_signed_body(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        req_id: Optional[int] = None,
        nonce: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build a complete signed request body for REST or WS."""
        if req_id is None:
            req_id = int(time.time() * 1000) % (2 ** 31)
        if nonce is None:
            nonce = int(time.time() * 1000)
        body: Dict[str, Any] = {
            "id": req_id,
            "method": method,
            "api_key": self.api_key,
            "params": params if params is not None else {},
            "nonce": nonce,
        }
        body["sig"] = _sign(method, req_id, self.api_key, params, nonce, self.secret_key)
        return body

    def ws_auth_payload(self) -> Dict[str, Any]:
        """One-time public/auth message for WS session authentication."""
        return self.build_signed_body("public/auth", params=None)
