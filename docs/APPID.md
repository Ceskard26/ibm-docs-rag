# Login con IBM Cloud App ID (IBMid) — qué provisionar

El backend ya está listo (validación de tokens App ID vía JWKS, config-driven). Falta
que provisiones la instancia y me pases 3 valores. Mientras no estén, la app corre
anónima (login desactivado) — no se rompe nada.

## 1. Provisiona App ID
IBM Cloud Catalog → **App ID** → región **Dallas (us-south)** → plan **Lite** (gratis) o Graduated.

## 2. Configura el proveedor de identidad (IdP)
En la instancia → **Identity Providers**:
- **Para producción (IBMid real de empleados):** configura **SAML 2.0** federando a
  **IBMid / w3id**. Esto es lo que permite "login con IBMid", pero requiere registrar
  la app en el SSO de IBM (puede necesitar aprobación interna).
- **Para el demo ya (mismo código):** activa **Cloud Directory** (creas usuarios de
  prueba) o un social (Google). Luego cambias el IdP a IBMid sin tocar el código.

## 3. Registra una aplicación
En la instancia → **Applications** → **Add application** → tipo **Single Page Application**.
Obtienes: `clientId`, `tenantId`, y el `discoveryEndpoint` / `oAuthServerUrl`.

## 4. Configura las Redirect URIs
En la app registrada → **redirect URLs**, agrega:
- Dev: `http://localhost:5173`
- Prod: la URL pública del frontend en Code Engine

## 5. Pásame estos 3 valores
Los pongo en `backend/.env` (y en el secret de Code Engine para prod):

```
APPID_OAUTH_SERVER_URL=https://us-south.appid.cloud.ibm.com/oauth/v4/<TENANT_ID>
APPID_CLIENT_ID=<clientId de la app registrada>
AUTH_REQUIRED=false   # true = obliga login; false = permite anónimo + login opcional
```

> El frontend recibe `oauthServerUrl` y `clientId` desde `GET /auth/config` (no van
> secretos al navegador). El flujo de login es OIDC Authorization Code + PKCE (cliente
> público, sin secreto).

## Qué haré yo cuando me des los valores
1. Frontend: botón **"Iniciar sesión con IBMid"** → redirige a App ID → vuelve con token.
2. El frontend manda `Authorization: Bearer <token>` en cada request.
3. Memoria persistente **por usuario**: las conversaciones se guardan en el servidor
   ligadas a tu `sub` (IBMid), no en localStorage → te siguen entre dispositivos.
4. Header con tu nombre/email y botón de cerrar sesión.
