import { useState, useCallback, useRef } from 'react'
import FileUpload from './components/FileUpload'
import PageList from './components/PageList'
import TextEditor from './components/TextEditor'
import AIPanel from './components/AIPanel'
import ExportPanel from './components/ExportPanel'
import ConvertPanel from './components/ConvertPanel'
import Toast from './components/Toast'
import { extractDoc, saveEdits, translateDoc, exportDoc, getPipelineStatus } from './api'
import { triggerDownload, readBlobError } from './utils/download'
import './App.css'

const LANG_LABELS = { ja: '日本語', en: 'English', zh: '中文' }

export default function App() {
  const [file, setFile] = useState(null)
  const [pages, setPages] = useState([])
  const [currentPage, setCurrentPage] = useState(0)
  const [sourceLang, setSourceLang] = useState('ja')
  const [translations, setTranslations] = useState({})
  const [loading, setLoading] = useState(false)
  const [loadingStage, setLoadingStage] = useState('')
  const [loadingProgress, setLoadingProgress] = useState(0)
  const [toast, setToast] = useState(null)
  const pollingRef = useRef(null)

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
  const startPolling = useCallback((targetLangs) => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingRef.current = setInterval(async () => {
      try {
        const { data } = await getPipelineStatus(file.file_id)
        setLoadingStage(data.stage || '')
        setLoadingProgress(data.progress || 0)
        if (!data.running) {
          clearInterval(pollingRef.current)
          pollingRef.current = null
          setLoading(false)
          if (data.success !== false && !data.error) {
            // 重新提取以获取完整结果
            const extractData = await extractDoc(file.file_id)
            const result = extractData.data
            const trans = {}
            const langs = Object.keys(result.sidecar?.translations || {})
            for (const lang of langs) {
              trans[lang] = result.sidecar?.final?.[lang] || []
            }
            setTranslations(trans)
            showToast(
              `Translated to ${targetLangs.map(l => LANG_LABELS[l] || l).join(', ')}`,
              'success'
            )
          } else {
            showToast('Translation failed: ' + (data.error || 'unknown error'), 'error')
          }
        }
      } catch {
        // 继续轮询
      }
    }, 2000)
  }, [file])

  const handleTranslate = useCallback(async (targetLangs, glossary) => {
    if (!file) return
    setLoading(true)
    setLoadingStage('Starting...')
    try {
      await translateDoc(file.file_id, sourceLang, targetLangs, glossary)
      startPolling(targetLangs)
    } catch (e) {
      setLoading(false)
      showToast('Translation failed: ' + (e.response?.data?.detail || e.message), 'error')
    }
  }, [file, sourceLang, startPolling])

  // ---- 导出 ----
  const handleExport = useCallback(async (targetLang, mode) => {
    if (!file) return
    try {
      const { data } = await exportDoc(file.file_id, targetLang, mode)
      const result = triggerDownload(data, `tridoc_${targetLang}_${mode}.${file.file_type}`)
      if (result.ok) {
        showToast(`Exported: ${LANG_LABELS[targetLang] || targetLang} (${mode})`, 'success')
      } else {
        showToast(result.error, 'error')
      }
    } catch (e) {
      const msg = await readBlobError(e)
      showToast('Export failed: ' + msg, 'error')
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
            <ConvertPanel file={file} onToast={showToast} />
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
            <p>{loadingStage || 'Processing...'}</p>
            {loadingProgress > 0 && <p className="sub">{loadingProgress}%</p>}
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
                    <span className="trans-page-label">Page {currentPage + 1}</span>
                    <div className="trans-text">{currentText || <span className="muted">(empty)</span>}</div>
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
