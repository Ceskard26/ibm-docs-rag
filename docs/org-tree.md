# Árbol de gobernanza — contrato de datos

## Nodo (esquema frontend)

```
{
  id:       string,   // identificador único del nodo
  name:     string,   // nombre completo de la persona
  role:     string,   // cargo/título
  email:    string,   // email corporativo (usado para destacar al usuario logueado)
  area:     string,   // área o BU (IBM Cloud, CSM, watsonx, Sales, Client Engineering…)
  children: [Nodo]    // sub-árbol (array vacío en nodos hoja)
}
```

## Tabla sugerida en BD

```sql
CREATE TABLE org_nodes (
  id        TEXT PRIMARY KEY,
  name      TEXT NOT NULL,
  role      TEXT NOT NULL,
  email     TEXT,
  area      TEXT,
  parent_id TEXT REFERENCES org_nodes(id) ON DELETE CASCADE
);
```

El campo `parent_id = NULL` identifica al nodo raíz (el GM de la organización).

## Mapeo BD → JSON frontend

El frontend espera el árbol ya ensamblado como objeto anidado (recursivo).
La query SQL sugerida es una CTE recursiva:

```sql
WITH RECURSIVE tree AS (
  SELECT * FROM org_nodes WHERE parent_id IS NULL
  UNION ALL
  SELECT n.* FROM org_nodes n JOIN tree t ON n.parent_id = t.id
)
SELECT * FROM tree;
```

El backend (endpoint sugerido `GET /org-tree`) ensambla las filas en el árbol
anidado antes de devolverlo al frontend.

## Estado actual

Hoy el frontend usa datos mock definidos en `frontend/src/orgTree.js`.
No hay endpoint real ni tabla en BD.
El componente `frontend/src/OrgTreeView.jsx` importa ese mock directamente;
cuando exista el endpoint, bastará reemplazar esa importación por una llamada
`fetch(`${API_BASE}/org-tree`)` y guardar el resultado en estado.

## Para conectar la data real (checklist)

1. Crear la tabla `org_nodes` en Postgres y poblarla (manual o via LDAP/directorio).
2. Exponer `GET /org-tree` en el backend (devuelve el árbol ensamblado, autenticado).
3. En `OrgTreeView.jsx`: reemplazar `import ORG_TREE from './orgTree'` por un
   `useEffect` que haga fetch al endpoint y guarde el árbol en `useState`.
4. Eliminar `frontend/src/orgTree.js` una vez la data real esté disponible.
