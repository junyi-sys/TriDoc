import { useState, useCallback, useRef, useEffect } from 'react'
import { startConversion, getConversionStatus, downloadConversion, getConversionFormats } from '../api'
import { triggerDownload, readBlobError } from '../utils/download'
import './ConvertPanel.css'

const FMT_LABELS = { pdf: 'PDF', docx: 'DOCX', pptx: 'PPTX', txt: 'TXT' }

export default function ConvertPanel({ file, onToast }) {
  const [targetFormat, setTargetFormat] = useState('')
  const [validTargets, setValidTargets] = useState([])
  const [converting, setConverting] = useState(false)
  const [status, setStatus] = useState(null)
  const pollingRef = useRef(null)

  useEffect(() => {
    if (!file) return
    getConversionFormats().then(({ data }) => {
      const src = file.file_type?.toLowerCase() || ''
      setValidTargets(data.by_source[src] || [])
    }).catch(() => {})
  }, [file])

  const startPolling = useCallback(() => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingRef.current = setInterval(async () => {
      try {
        const { data } = await getConversionStatus(file.file_id)
        setStatus(data)
        if (!data.running) {
          clearInterval(pollingRef.current)
          pollingRef.current = null
          setConverting(false)
          if (data.error) {
            onToast('Conversion failed: ' + data.error, 'error')
          } else {
            onToast('Conversion complete', 'success')
          }
        }
      } catch {
        // continue polling
      }
    }, 2000)
  }, [file, onToast])

  const handleConvert = useCallback(async () => {
    if (!targetFormat || !file) return
    setConverting(true)
    setStatus(null)
    try {
      const { data } = await startConversion(file.file_id, targetFormat)
      if (data.async) {
        startPolling()
        onToast('Conversion started...', 'info')
      } else {
        setConverting(false)
        setStatus({ running: false, stage: '完成', progress: 100, error: null })
        onToast('Conversion complete', 'success')
      }
    } catch (e) {
      setConverting(false)
      onToast('Conversion failed: ' + (e.response?.data?.detail || e.message), 'error')
    }
  }, [targetFormat, file, startPolling, onToast])

  const handleDownload = useCallback(async () => {
    try {
      const { data } = await downloadConversion(file.file_id, targetFormat)
      const ext = targetFormat || 'bin'
      const result = triggerDownload(data, `converted.${ext}`)
      if (!result.ok) {
        onToast(result.error, 'error')
      }
    } catch (e) {
      const msg = await readBlobError(e)
      onToast('Download failed: ' + msg, 'error')
    }
  }, [file, targetFormat, onToast])

  if (!file) return null

  return (
    <div className="convert-panel">
      <select
        value={targetFormat}
        onChange={e => setTargetFormat(e.target.value)}
        disabled={converting}
      >
        <option value="">Convert to...</option>
        {validTargets.map(fmt => (
          <option key={fmt} value={fmt}>{FMT_LABELS[fmt] || fmt}</option>
        ))}
      </select>
      <button
        className="btn btn-sm"
        onClick={handleConvert}
        disabled={converting || !targetFormat}
      >
        {converting ? 'Converting...' : 'Convert'}
      </button>
      {converting && status && (
        <span className="convert-progress">
          {status.stage} {status.progress > 0 ? `${status.progress}%` : ''}
        </span>
      )}
      {!converting && status && !status.running && !status.error && (
        <button className="btn btn-sm btn-dl" onClick={handleDownload}>
          Download
        </button>
      )}
    </div>
  )
}
