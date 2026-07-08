// Autenticación con IBM Cloud App ID (login IBMid/Cloud Directory).
//
// Flujo:
// 1. login(): redirige a la página de App ID (authorization).
// 2. App ID vuelve a esta app con ?code=...
// 3. initAuth() manda ese code a NUESTRO backend (/auth/exchange), que hace el
//    intercambio con el secret (server-to-server). El secret nunca toca el navegador.
// 4. El backend devuelve un token; lo guardamos y lo mandamos como Bearer.
import { API_BASE } from './api'

let config = { enabled: false }
const TOKEN_KEY = 'ika-token'
const USER_KEY = 'ika-user' // perfil (nombre/email), el access token no lo trae
const LOGIN_AT_KEY = 'ika-login-at' // timestamp (Date.now()) del último login exitoso

// La sesión restaurada de localStorage expira a las ~2h, aunque el access token
// del backend siga siendo válido. Evita que una sesión quede "colgada" sin nombre
// visible (perfil corrupto/ausente) o indefinidamente logueada.
const SESSION_MAX_AGE_MS = 2 * 60 * 60 * 1000

function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  localStorage.removeItem(LOGIN_AT_KEY)
}

export async function initAuth() {
  try {
    const res = await fetch(`${API_BASE}/auth/config`)
    config = await res.json()
  } catch {
    return { enabled: false, user: null }
  }
  if (!config.enabled) return { enabled: false, user: null }

  const params = new URLSearchParams(window.location.search)

  // ¿Volvemos del login con un código? -> lo canjeamos en el backend.
  if (params.get('code')) {
    const code = params.get('code')
    window.history.replaceState({}, document.title, window.location.pathname)
    try {
      const r = await fetch(`${API_BASE}/auth/exchange`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, redirect_uri: window.location.origin }),
      })
      if (!r.ok) throw new Error(`exchange ${r.status}: ${await r.text()}`)
      const data = await r.json()
      // Guardamos el perfil ANTES de devolver, para que una sesión recién
      // creada nunca quede sin `ika-user`/`ika-login-at` en localStorage.
      localStorage.setItem(TOKEN_KEY, data.token)
      localStorage.setItem(USER_KEY, JSON.stringify(data.user))
      localStorage.setItem(LOGIN_AT_KEY, String(Date.now()))
      return { enabled: true, user: { ...data.user, token: data.token } }
    } catch (e) {
      console.error('[App ID] Falló el intercambio del código:', e)
      return { enabled: true, user: null, error: String(e?.message || e) }
    }
  }

  // ¿Hay un token guardado y todavía válido? -> lo verificamos con /me y reusamos
  // el perfil guardado (nombre/email), que el access token no incluye.
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    const loginAt = Number(localStorage.getItem(LOGIN_AT_KEY))
    const sessionExpired = !loginAt || Date.now() - loginAt > SESSION_MAX_AGE_MS

    if (!sessionExpired) {
      try {
        const r = await fetch(`${API_BASE}/me`, { headers: { Authorization: `Bearer ${token}` } })
        if (r.ok) {
          const u = await r.json()
          if (u.authenticated) {
            let profile = {}
            try {
              profile = JSON.parse(localStorage.getItem(USER_KEY)) || {}
            } catch {
              /* ignore */
            }
            const merged = { ...u, ...profile, token }
            // Si el perfil restaurado no trae nombre (localStorage corrupto/vacío),
            // tratamos la sesión como inválida en vez de mostrar un header sin nombre.
            if (merged.name) {
              return { enabled: true, user: merged }
            }
          }
        }
      } catch {
        /* token vencido/ inválido */
      }
    }

    clearSession()
  }

  return { enabled: true, user: null }
}

export function login() {
  const p = new URLSearchParams({
    client_id: config.clientId,
    response_type: 'code',
    redirect_uri: window.location.origin,
    scope: 'openid profile email',
    state: Math.random().toString(36).slice(2),
  })
  window.location.href = `${config.oauthServerUrl}/authorization?${p.toString()}`
}

export function logout() {
  clearSession()
  window.location.reload()
}
