// URL base del backend, compartida por la app y el módulo de auth.
// Prioridad: config inyectada en runtime (contenedor) > variable de build > localhost.
// Se quita cualquier barra final para evitar URLs con doble slash.
export const API_BASE = (
  (typeof window !== 'undefined' && window.__API_URL__) ||
  import.meta.env.VITE_API_URL ||
  'http://localhost:8000'
).replace(/\/+$/, '')
