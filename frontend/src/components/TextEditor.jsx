import { useState, useEffect, useRef, useCallback } from 'react'
import './TextEditor.css'

export default function TextEditor({ pages, currentPage, onEdit, onSave }) {
  const [text, setText] = useState('')
  const timerRef = useRef(null)

  useEffect(() => {
    if (pages && pages[currentPage]) {
      setText(pages[currentPage].text || '')
    } else {
      setText('')
    }
  }, [pages, currentPage])

  const debouncedEdit = useCallback((val) => {
    setText(val)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      onEdit(currentPage, val)
    }, 400)
  }, [currentPage, onEdit])

  const handleKeyDown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault()
      onSave()
    }
  }

  if (!pages || pages.length === 0) {
    return (
      <div className="editor-wrap">
        <div className="editor-empty">Select a page to edit</div>
      </div>
    )
  }

  const page = pages[currentPage]
  return (
    <div className="editor-wrap">
      <div className="editor-header">
        <span>Page {page?.page || currentPage + 1}</span>
        <button className="outline" onClick={onSave} title="Ctrl+S">Save</button>
      </div>
      <textarea
        value={text}
        onChange={e => debouncedEdit(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Edit text here..."
        spellCheck={false}
      />
    </div>
  )
}
