interface Project {
  title: string
  description: string
  href?: string
  imgSrc?: string
}

const projectsData: Project[] = [
  {
    title: 'Screendesign Projekt',
    description: `UI & UX Design: Von der Konzeption bis zum Design in Figma`,
    imgSrc: '/static/images/3.1_projects.jpg',
    href: 'https://th-koeln.github.io/mi-bachelor-screendesign-projekte/sd-2020/chan-neubert-selim/',
  },
  {
    title: 'Webservice-Projekt',
    description: `Ein webbasierter Dienst für eine Online-Buchhandlung mit Fokus auf effiziente Datenverarbeitung und eine saubere Frontend-Implementierung.`,
    imgSrc: '/static/images/3.2_projects.jpg',
    href: 'https://github.com/cywind1/FDDW-products',
  },
]

export default projectsData
