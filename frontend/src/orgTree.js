/**
 * Árbol de gobernanza — datos MOCK para demo.
 *
 * Contrato del nodo (plantilla para la futura tabla en BD):
 *   { id, name, role, email, area, children: [Nodo] }
 *
 * Tabla sugerida en BD: org_nodes(id, name, role, email, area, parent_id)
 * Ver docs/org-tree.md para el esquema completo y el mapeo frontend↔BD.
 */

const ORG_TREE = {
  id: 'n1',
  name: 'Patricia Morales',
  role: 'General Manager, IBM Cloud LATAM',
  email: 'patricia.morales@ibm.com',
  area: 'IBM Cloud',
  children: [
    {
      id: 'n2',
      name: 'Andrés Fuentes',
      role: 'Director, Customer Success Management',
      email: 'andres.fuentes@ibm.com',
      area: 'CSM',
      children: [
        {
          id: 'n5',
          name: 'César Carrasco',
          role: 'Customer Success Manager',
          email: 'cesar.carrasco@ibm.com',
          area: 'IBM Cloud',
          children: [],
        },
        {
          id: 'n6',
          name: 'Valentina Torres',
          role: 'Customer Success Manager',
          email: 'valentina.torres@ibm.com',
          area: 'IBM Cloud',
          children: [],
        },
        {
          id: 'n7',
          name: 'Luis Herrera',
          role: 'Customer Success Manager',
          email: 'luis.herrera@ibm.com',
          area: 'watsonx',
          children: [],
        },
        {
          id: 'n8',
          name: 'Camila Rojas',
          role: 'CSM Tech Lead',
          email: 'camila.rojas@ibm.com',
          area: 'CSM',
          children: [],
        },
      ],
    },
    {
      id: 'n3',
      name: 'Roberto Salinas',
      role: 'Director, Client Engineering',
      email: 'roberto.salinas@ibm.com',
      area: 'Client Engineering',
      children: [
        {
          id: 'n9',
          name: 'Daniela Vega',
          role: 'Client Engineer',
          email: 'daniela.vega@ibm.com',
          area: 'Client Engineering',
          children: [],
        },
        {
          id: 'n10',
          name: 'Matías Contreras',
          role: 'Client Engineer',
          email: 'matias.contreras@ibm.com',
          area: 'Client Engineering',
          children: [],
        },
      ],
    },
    {
      id: 'n4',
      name: 'Sofía Mendoza',
      role: 'Director, Sales',
      email: 'sofia.mendoza@ibm.com',
      area: 'Sales',
      children: [
        {
          id: 'n11',
          name: 'Javier Castillo',
          role: 'Account Executive',
          email: 'javier.castillo@ibm.com',
          area: 'Sales',
          children: [],
        },
        {
          id: 'n12',
          name: 'Isabela Ferreira',
          role: 'Account Executive',
          email: 'isabela.ferreira@ibm.com',
          area: 'Sales',
          children: [],
        },
      ],
    },
  ],
}

export default ORG_TREE
