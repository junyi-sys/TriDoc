import { useState } from 'react'
import './AIPanel.css'

const TARGET_LANGS = [
  { code: 'en', label: 'English' },
  { code: 'ja', label: '日本語' },
  { code: 'zh', label: '中文' },
]

export default function AIPanel({ sourceLang, onTranslate, loading }) {
  const [selectedLangs, setSelectedLangs] = useState(['en'])
  const [showGlossary, setShowGlossary] = useState(false)
  const [glossaryText, setGlossaryText] = useState('')

  const toggleLang = (code) => {
    setSelectedLangs(prev =>
      prev.includes(code) ? prev.filter(l => l !== code) : [...prev, code]
    )
  }

  const parseGlossary = () => {
    const map = {}
    glossaryText.split('\n').forEach(line => {
      const parts = line.split('\t').length === 2 ? line.split('\t') : line.split(',')
      if (parts.length === 2 && parts[0].trim() && parts[1].trim()) {
        map[parts[0].trim()] = parts[1].trim()
      }
    })
    return map
  }

  const handleTranslate = () => {
    const targets = selectedLangs.filter(l => l !== sourceLang)
    if (targets.length === 0) return
    onTranslate(targets, parseGlossary())
  }

  const availableTargets = TARGET_LANGS.filter(l => l.code !== sourceLang)

  return (
    <div className="ai-panel">
      <div className="ai-lang-chips">
        {availableTargets.map(l => (
          <button
            key={l.code}
            className={`chip ${selectedLangs.includes(l.code) ? 'active' : ''}`}
            onClick={() => toggleLang(l.code)}
          >
            {l.label}
          </button>
        ))}
      </div>

      <button
        className="success"
        onClick={handleTranslate}
        disabled={loading || selectedLangs.filter(l => l !== sourceLang).length === 0}
      >
        {loading ? 'Translating...' : 'Translate'}
      </button>

      <button className="outline small" onClick={() => setShowGlossary(!showGlossary)}>
        Glossary {showGlossary ? '▴' : '▾'}
      </button>

      {showGlossary && (
        <div className="glossary-editor">
          <p className="hint">CSV or TSV format: source, target</p>
          <textarea
            rows={4}
            value={glossaryText}
            onChange={e => setGlossaryText(e.target.value)}
            placeholder={"仕様書,Specification\n稟議,Approval Request"}
          />
        </div>
      )}
    </div>
  )
}
