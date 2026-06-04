import { useState, useCallback } from 'react'
import FileUpload from './components/FileUpload'
import PageList from './components/PageList'
import TextEditor from './components/TextEditor'
import AIPanel from './components/AIPanel'
import ExportPanel from './components/ExportPanel'
import Toast from './components/Toast'
import { extractDoc, saveEdits, translateDoc, exportDoc } from './api'
import './App.css'

const LANG_LABELS = { ja: '日本語', en: 'English', zh: '中文' }

export default function App() {
  const [file, setFile] = useState(null)
  const [pages, setPages] = useState([])
  const [currentPage, setCurrentPage] = useState(0)
  const [sourceLang, setSourceLang] = useState('ja')
  const [translations, setTranslations] = useState({})
  const [loading, setLoading] = useState(false)
  const [toast, setToast] = useState(null)

  const showToast = (msg, type = 'info') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  // ---- 上传 ----
  const handleUpload = useCallback(async (fileInfo) => {
    setFile(fileInfo)
    setPages([])
    setTranslations({})
    setLoading(true)
    try {
      const { data } = await extractDoc(fileInfo.file_id)
      setPages(data.pages || [])
      setCurrentPage(0)
      showToast(`Extracted ${data.pages?.length || 0} pages`, 'success')
    } catch (e) {
      showToast('Extraction failed: ' + (e.response?.data?.detail || e.message), 'error')
    } finally {
      setLoading(false)
    }
  }, [])

  // ---- 编辑 ----
  const handleEdit = useCallback(async (pageIndex, newText) => {
    const updated = pages.map((p, i) => i === pageIndex ? { ...p, text: newText } : p)
    setPages(updated)
  }, [pages])

  const handleSave = useCallback(async () => {
    if (!file || !pages.length) return
    try {
      const pageEdits = pages.map(p => ({ page: p.page, text: p.text }))
      await saveEdits(file.file_id, pageEdits, sourceLang)
      showToast('Saved', 'success')
    } catch {
      showToast('Save failed', 'error')
    }
  }, [file, pages, sourceLang])

  // ---- AI ----
  const handleTranslate = useCallback(async (targetLangs, glossary) => {
    if (!file) return
    setLoading(true)
    try {
      const { data } = await translateDoc(file.file_id, sourceLang, targetLangs, glossary)
      setTranslations(data.final_pages || data.translations || {})
      showToast(
        `Translated to ${targetLangs.map(l => LANG_LABELS[l] || l).join(', ')}` +
        (data.stats ? ` | ${data.stats.en_terms_normalized || 0} terms aligned` : ''),
        'success'
      )
    } catch (e) {
      showToast('Translation failed: ' + (e.response?.data?.detail || e.message), 'error')
    } finally {
      setLoading(false)
    }
  }, [file, sourceLang])

  // ---- 导出 ----
  const handleExport = useCallback(async (targetLang, mode) => {
    if (!file) return
    try {
      const { data } = await exportDoc(file.file_id, targetLang, mode)
      const url = URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url
      a.download = `tridoc_${targetLang}_${mode}.${file.file_type}`
      a.click()
      URL.revokeObjectURL(url)
      showToast(`Exported: ${LANG_LABELS[targetLang] || targetLang} (${mode})`, 'success')
    } catch (e) {
      showToast('Export failed: ' + (e.response?.data?.detail || e.message), 'error')
    }
  }, [file])

  return (
    <div className="app">
      {toast && <Toast message={toast.msg} type={toast.type} />}

      {/* Header */}
      <header className="header">
        <h1 className="logo">Tri<span>Doc</span></h1>
        <span className="tagline">日↔英↔中 文档翻译与润色</span>
        <div className="header-right">
          <FileUpload onUpload={handleUpload} disabled={loading} />
        </div>
      </header>

      {/* Toolbar */}
      <div className="toolbar">
        {file && (
          <>
            <span className="file-info">
              <strong>{file.file_name}</strong>
              <span className={`badge ${file.file_type}`}>{file.file_type.toUpperCase()}</span>
            </span>
            <label>
              Source:
              <select value={sourceLang} onChange={e => setSourceLang(e.target.value)}>
                <option value="ja">日本語</option>
                <option value="en">English</option>
                <option value="zh">中文</option>
              </select>
            </label>
            <ExportPanel
              file={file}
              translations={translations}
              onExport={handleExport}
            />
            <AIPanel
              sourceLang={sourceLang}
              onTranslate={handleTranslate}
              loading={loading}
            />
          </>
        )}
      </div>

      {/* Main */}
      <div className="main">
        {!file ? (
          <div className="placeholder">
            <div className="placeholder-icon">📄</div>
            <h2>TriDoc — 日↔英↔中 文档翻译与润色</h2>
            <p>上传 PDF / PPTX / Word 文件开始</p>
            <p className="sub">DeepSeek 驱动 · 术语一致性 · 对照导出</p>
          </div>
        ) : loading ? (
          <div className="placeholder">
            <div className="spinner" />
            <p>Processing...</p>
          </div>
        ) : (
          <>
            <PageList
              pages={pages}
              currentPage={currentPage}
              onSelect={setCurrentPage}
              translations={translations}
            />
            <TextEditor
              pages={pages}
              currentPage={currentPage}
              onEdit={handleEdit}
              onSave={handleSave}
            />
            <div className="translations-preview">
              <h4>Translations</h4>
              {Object.keys(translations).length === 0 && (
                <p className="muted">Translate to see results</p>
              )}
              {Object.entries(translations).map(([lang, transPages]) => {
                const currentText = transPages[currentPage]?.text || ''
                return (
                  <div key={lang} className="trans-block">
                    <span className={`badge ${lang}`}>{LANG_LABELS[lang] || lang}</span>
                    <p>{currentText.slice(0, 200)}{currentText.length > 200 ? '...' : ''}</p>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
