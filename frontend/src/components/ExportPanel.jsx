import { useState } from 'react'
import './ExportPanel.css'

const LANG_LABELS = { ja: '日本語', en: 'English', zh: '中文' }

export default function ExportPanel({ file, translations, onExport }) {
  const [targetLang, setTargetLang] = useState('')
  const [mode, setMode] = useState('translated')
  const [exporting, setExporting] = useState(false)

  const availableLangs = Object.keys(translations || {})

  const handleExport = async () => {
    if (!targetLang) return
    setExporting(true)
    await onExport(targetLang, mode)
    setExporting(false)
  }

  if (!file) return null

  return (
    <div className="export-panel">
      <select value={targetLang} onChange={e => setTargetLang(e.target.value)}>
        <option value="">Export...</option>
        {availableLangs.map(l => (
          <option key={l} value={l}>{LANG_LABELS[l] || l}</option>
        ))}
      </select>
      <select value={mode} onChange={e => setMode(e.target.value)}>
        <option value="translated">Translation</option>
        <option value="bilingual">Bilingual</option>
      </select>
      <button className="success" onClick={handleExport} disabled={exporting || !targetLang}>
        {exporting ? '...' : 'Download'}
      </button>
    </div>
  )
}
