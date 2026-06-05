import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// Files
export const uploadFile = (file) => {
  const fd = new FormData()
  fd.append('file', file)
  return api.post('/upload', fd)
}

export const listFiles = () => api.get('/files')
export const deleteFile = (id) => api.delete(`/files/${id}`)

// Documents
export const extractDoc = (fileId) => api.get(`/extract/${fileId}`)
export const saveEdits = (fileId, pages, sourceLanguage) =>
  api.put(`/extract/${fileId}`, { pages, source_language: sourceLanguage })

// AI Pipeline
export const polishText = (fileId, sourceLang, glossary) =>
  api.post(`/ai/polish/${fileId}`, {
    source_language: sourceLang,
    target_languages: [sourceLang],
    glossary: glossary || {},
    skip_polish: false,
    skip_post_polish: true,
  })

export const translateDoc = (fileId, sourceLang, targetLangs, glossary) =>
  api.post(`/ai/translate/${fileId}`, {
    source_language: sourceLang,
    target_languages: targetLangs,
    glossary: glossary || {},
    skip_polish: false,
    skip_post_polish: false,
  })

export const alignTerms = (fileId, targetLang, glossary) =>
  api.post(`/ai/align/${fileId}`, {
    target_language: targetLang,
    glossary: glossary || {},
  })

// Export
export const exportDoc = (fileId, targetLang, mode = 'translated') =>
  api.get(`/export/${fileId}`, {
    params: { target_language: targetLang, mode },
    responseType: 'blob',
  })

// Pipeline status
export const getPipelineStatus = (fileId) => api.get(`/ai/status/${fileId}`)

// Conversion
export const startConversion = (fileId, targetFormat) =>
  api.post(`/convert/${fileId}`, { target_format: targetFormat })

export const getConversionStatus = (fileId) => api.get(`/convert/status/${fileId}`)

export const downloadConversion = (fileId, targetFormat) =>
  api.get(`/convert/download/${fileId}`, {
    params: { target_format: targetFormat },
    responseType: 'blob',
  })

export const getConversionFormats = () => api.get('/convert/formats')

// Health
export const healthCheck = () => api.get('/health')
