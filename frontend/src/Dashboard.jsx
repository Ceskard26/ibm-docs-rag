import { useEffect, useState } from 'react'
import { Tile, Button, InlineLoading, InlineNotification } from '@carbon/react'
import { Login } from '@carbon/icons-react'
import './Dashboard.css'

/**
 * Panel de feedback — USO INTERNO (César), no forma parte de la demo.
 *
 * Se accede SOLO por URL directa (`#dashboard`, ver App.jsx) — a propósito NO hay
 * ningún botón/enlace visible en el header (decisión del PO: "menos es más", ya
 * aplicada al retirar el switcher del árbol de gobernanza — ver docs/STATUS.md).
 *
 * Requiere sesión iniciada (mismo patrón que /conversations): si no hay authUser,
 * muestra un mensaje simple pidiendo iniciar sesión (reusa `login()` de auth.js).
 * Consume GET /feedback/stats (ver docs/GOVERNANCE.md) — solo lectura.
 */
function Dashboard({ apiBase, authEnabled, authUser, authHeader, login, t }) {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!authUser) return
    setLoading(true)
    setError(null)
    fetch(`${apiBase}/feedback/stats`, { headers: { ...authHeader() } })
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status))
        return res.json()
      })
      .then(setStats)
      .catch(() => setError(t.dashboardError))
      .finally(() => setLoading(false))
  }, [authUser, apiBase]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="dashboard-page">
      <div className="dashboard-header">
        <h1>{t.dashboardTitle}</h1>
        <p className="dashboard-subtitle">{t.dashboardSubtitle}</p>
      </div>

      {!authUser ? (
        <Tile className="dashboard-tile">
          <p>{t.dashboardLoginRequired}</p>
          {authEnabled && (
            <Button kind="primary" size="sm" renderIcon={Login} onClick={login}>
              {t.signIn}
            </Button>
          )}
        </Tile>
      ) : loading ? (
        <InlineLoading description={t.dashboardLoading} />
      ) : error ? (
        <InlineNotification kind="error" title={t.errorTitle} subtitle={error} lowContrast hideCloseButton />
      ) : stats ? (
        <>
          <div className="dashboard-cards">
            <Tile className="dashboard-card">
              <p className="dashboard-card-label">{t.dashboardTotal}</p>
              <p className="dashboard-card-value">{stats.total_up + stats.total_down}</p>
            </Tile>
            <Tile className="dashboard-card">
              <p className="dashboard-card-label">{t.dashboardTotalUp}</p>
              <p className="dashboard-card-value dashboard-card-value--up">{stats.total_up}</p>
            </Tile>
            <Tile className="dashboard-card">
              <p className="dashboard-card-label">{t.dashboardTotalDown}</p>
              <p className="dashboard-card-value dashboard-card-value--down">{stats.total_down}</p>
            </Tile>
          </div>

          <Tile className="dashboard-tile">
            <h3>{t.dashboardByLanguage}</h3>
            {stats.by_language.length === 0 ? (
              <p className="dashboard-empty">{t.dashboardNoNegative}</p>
            ) : (
              <table className="dashboard-table">
                <thead>
                  <tr>
                    <th>{t.langPrefix}</th>
                    <th>{t.dashboardTotalUp}</th>
                    <th>{t.dashboardTotalDown}</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.by_language.map((row) => (
                    <tr key={row.language}>
                      <td>{row.language}</td>
                      <td>{row.up}</td>
                      <td>{row.down}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Tile>

          <Tile className="dashboard-tile">
            <h3>{t.dashboardRecentNegative}</h3>
            {stats.recent_negative.length === 0 ? (
              <p className="dashboard-empty">{t.dashboardNoNegative}</p>
            ) : (
              <table className="dashboard-table dashboard-table--wide">
                <thead>
                  <tr>
                    <th>{t.dashboardDate}</th>
                    <th>{t.dashboardQuestion}</th>
                    <th>{t.answer}</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.recent_negative.map((row, i) => (
                    <tr key={i}>
                      <td className="dashboard-date-cell">{new Date(row.created_at).toLocaleString()}</td>
                      <td>{row.question}</td>
                      <td>{row.answer}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Tile>
        </>
      ) : null}

      {/* Navegación normal (no SPA) a propósito: vuelve a la ruta sin el hash
          #dashboard, forzando una recarga limpia de la vista de chat. */}
      <a
        className="dashboard-back"
        href={typeof window !== 'undefined' ? window.location.pathname + window.location.search : '/'}
      >
        {t.dashboardBackToChat}
      </a>
    </div>
  )
}

export default Dashboard
