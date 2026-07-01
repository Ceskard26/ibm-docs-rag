"""Autenticación con IBM Cloud App ID (login con IBMid).

Diseño:
- Config por variables de entorno. Si no está configurado, AUTH_ENABLED=False y la
  app funciona anónima (como antes) — así el demo no se rompe sin App ID.
- El frontend hace el login OIDC contra App ID (que federa a IBMid) y envía el token
  en `Authorization: Bearer <token>`. Aquí solo VALIDAMOS ese token (firma vía JWKS
  de tu instancia + expiración), sin manejar secretos del cliente.
"""

import os
import base64

import jwt
import requests
from fastapi import Header, HTTPException

# URL del servidor OAuth de tu instancia App ID, p.ej.:
#   https://us-south.appid.cloud.ibm.com/oauth/v4/<TENANT_ID>
APPID_OAUTH_SERVER_URL = os.getenv("APPID_OAUTH_SERVER_URL", "").rstrip("/")
APPID_CLIENT_ID = os.getenv("APPID_CLIENT_ID", "")
# Secret para el intercambio server-side del código (App ID exige autenticar el cliente).
APPID_CLIENT_SECRET = os.getenv("APPID_CLIENT_SECRET", "")
# Si es true, los endpoints protegidos exigen login; si false, permiten anónimo.
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "false").lower() in ("1", "true", "yes")

# Auth activa solo si hay configuración de App ID.
AUTH_ENABLED = bool(APPID_OAUTH_SERVER_URL and APPID_CLIENT_ID)

ANONYMOUS = {"sub": "anonymous", "email": None, "name": None, "authenticated": False}

_jwks_client = None


def _jwks():
    """Cliente JWKS (cachea las llaves públicas de tu instancia App ID)."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(f"{APPID_OAUTH_SERVER_URL}/publickeys")
    return _jwks_client


def verify_token(token: str) -> dict:
    """Valida firma (JWKS de tu instancia) + expiración. Devuelve los claims."""
    signing_key = _jwks().get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        # La firma con la JWKS de TU tenant ya garantiza el origen; App ID usa
        # `aud` distinto entre access/id token, así que no lo forzamos.
        options={"verify_aud": False},
    )
    iss = claims.get("iss", "")
    if "appid" not in iss and (not APPID_OAUTH_SERVER_URL or APPID_OAUTH_SERVER_URL not in iss):
        raise ValueError("issuer inesperado")
    return claims


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Intercambia el código por tokens (server-to-server, autenticando el cliente).

    Aquí SÍ mandamos el secret (Basic auth), que es lo que App ID exige y lo que el
    navegador no podía hacer (por eso daba 401). El secret nunca sale del servidor.
    """
    basic = base64.b64encode(f"{APPID_CLIENT_ID}:{APPID_CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        f"{APPID_OAUTH_SERVER_URL}/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()  # {access_token, id_token, ...}


def user_from_claims(claims: dict) -> dict:
    return {
        "sub": claims.get("sub"),
        "email": claims.get("email"),
        "name": claims.get("name") or claims.get("given_name") or claims.get("email"),
        "authenticated": True,
    }


def get_current_user(authorization: str = Header(default=None)) -> dict:
    """Dependencia de FastAPI: devuelve el usuario actual (o anónimo)."""
    if not AUTH_ENABLED:
        return ANONYMOUS
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        if AUTH_REQUIRED:
            raise HTTPException(status_code=401, detail="Falta el token de autenticación")
        return ANONYMOUS
    try:
        claims = verify_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {e}")
    return user_from_claims(claims)


def public_config() -> dict:
    """Config no-secreta que el frontend necesita para iniciar el login OIDC."""
    return {
        "enabled": AUTH_ENABLED,
        "required": AUTH_REQUIRED,
        "oauthServerUrl": APPID_OAUTH_SERVER_URL,
        "clientId": APPID_CLIENT_ID,
    }
