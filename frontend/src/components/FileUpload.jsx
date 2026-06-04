import { useState, useRef } from 'react'
import { uploadFile } from '../api'
import './FileUpload.css'

export default function FileUpload({ onUpload, disabled }) {
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef(null)

  const handleDrag = (e) => {
    e.preventDefault()
    if (!disabled) setDragging(e.type === 'dragover')
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    if (disabled) return
    const file = e.dataTransfer.files[0]
    if (file) doUpload(file)
  }

  const handleSelect = (e) => {
    const file = e.target.files[0]
    if (file) doUpload(file)
  }

  const doUpload = async (file) => {
    const ext = file.name.split('.').pop().toLowerCase()
    if (!['pdf', 'pptx', 'docx'].includes(ext)) {
      setError('PDF / PPTX / DOCX only')
      return
    }
    setError('')
    setUploading(true)
    try {
      const { data } = await uploadFile(file)
      onUpload(data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="file-upload-wrap">
      <div
        className={`drop-zone ${dragging ? 'drag-over' : ''} ${disabled ? 'disabled' : ''}`}
        onDragOver={handleDrag}
        onDragLeave={handleDrag}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
      >
        {uploading ? (
          <span className="upload-text">Uploading...</span>
        ) : (
          <span className="upload-text">
            <span className="upload-icon">⬆</span> Drop file or <u>click</u>
          </span>
        )}
        <input ref={inputRef} type="file" accept=".pdf,.pptx,.docx" onChange={handleSelect} hidden />
      </div>
      {error && <span className="upload-error">{error}</span>}
    </div>
  )
}
