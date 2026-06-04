import './PageList.css'

export default function PageList({ pages, currentPage, onSelect, translations }) {
  if (!pages.length) {
    return <div className="page-list empty">No pages</div>
  }

  const hasTranslation = (idx) => {
    return Object.values(translations).some(transPages => transPages[idx]?.text)
  }

  return (
    <div className="page-list">
      <h4>Pages</h4>
      <ul>
        {pages.map((p, i) => (
          <li
            key={i}
            className={`${i === currentPage ? 'active' : ''} ${hasTranslation(i) ? 'has-trans' : ''}`}
            onClick={() => onSelect(i)}
          >
            <span className="page-num">{p.page || i + 1}</span>
            <span className="page-preview">
              {(p.text || '').slice(0, 40)}{(p.text || '').length > 40 ? '...' : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
