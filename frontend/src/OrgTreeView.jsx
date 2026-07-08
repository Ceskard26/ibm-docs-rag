import { useState } from 'react'
import { InlineNotification, Tag } from '@carbon/react'
import ORG_TREE from './orgTree'
import './OrgTreeView.css'

/**
 * Componente de nodo individual + sus descendientes (recursivo).
 * @param {object} node  - Nodo del árbol (id, name, role, email, area, children)
 * @param {string} userEmail - Email del usuario logueado (para resaltar)
 * @param {object} t     - Diccionario de traducciones
 * @param {boolean} isRoot - True si es el nodo raíz (no muestra conector superior)
 */
function OrgNode({ node, userEmail, t, isRoot = false }) {
  const hasChildren = node.children && node.children.length > 0
  const [expanded, setExpanded] = useState(true)
  const isMe = userEmail && node.email.toLowerCase() === userEmail.toLowerCase()

  function toggle() {
    if (hasChildren) setExpanded((v) => !v)
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      toggle()
    }
  }

  return (
    <div className="org-branch">
      {/* Conector vertical desde el padre */}
      {!isRoot && <div className="org-connector-top" />}

      {/* Tarjeta del nodo */}
      <div
        className={`org-card${hasChildren ? ' has-children' : ''}${isMe ? ' is-me' : ''}`}
        role={hasChildren ? 'button' : undefined}
        tabIndex={hasChildren ? 0 : undefined}
        aria-expanded={hasChildren ? expanded : undefined}
        onClick={toggle}
        onKeyDown={onKeyDown}
      >
        <p className="org-card-name" title={node.name}>{node.name}</p>
        <p className="org-card-role">{node.role}</p>
        <div className="org-card-area-row">
          <Tag type="blue" size="sm">{node.area}</Tag>
          {isMe && <span className="org-card-me-badge">{t.orgYou}</span>}
        </div>
        {hasChildren && (
          <span className="org-card-toggle" aria-hidden="true">
            {expanded ? '▲' : '▼'}
          </span>
        )}
      </div>

      {/* Sub-árbol de hijos (colapsable) */}
      {hasChildren && expanded && (
        <div className="org-children">
          {/* Conector vertical hacia los hijos */}
          <div className="org-connector-bottom" />
          <div className={`org-siblings${node.children.length === 1 ? ' single-child' : ''}`}>
            {node.children.map((child) => (
              <OrgNode
                key={child.id}
                node={child}
                userEmail={userEmail}
                t={t}
                isRoot={false}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * Vista principal del árbol de gobernanza.
 * @param {object|null} authUser - Usuario logueado {name, email, token} | null
 * @param {object} t - Diccionario de traducciones (contiene las claves org*)
 */
export default function OrgTreeView({ authUser, t }) {
  return (
    <div className="org-tree-root">
      <InlineNotification
        className="org-tree-banner"
        kind="info"
        title={t.orgDemoTitle}
        subtitle={t.orgDemoSubtitle}
        lowContrast
        hideCloseButton
      />

      <div className="org-level">
        <OrgNode
          node={ORG_TREE}
          userEmail={authUser?.email ?? null}
          t={t}
          isRoot
        />
      </div>
    </div>
  )
}
