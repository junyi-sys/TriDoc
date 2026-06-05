/**
 * Initiate a browser file download from a Blob.
 * Returns { ok: true } on success, or { ok: false, error: string } on failure.
 */
export function triggerDownload(blob, filename) {
  if (!blob || blob.size === 0) {
    return { ok: false, error: 'Downloaded file is empty — the server may have returned an error' }
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
  return { ok: true }
}

/**
 * Read a Blob error response from axios and extract the error message.
 * Works with responseType: 'blob' where error bodies come back as Blob.
 */
export async function readBlobError(error) {
  const data = error?.response?.data
  if (data instanceof Blob && data.size > 0) {
    try {
      const text = await data.text()
      const parsed = JSON.parse(text)
      return parsed.detail || parsed.message || text
    } catch {
      return 'Unknown server error'
    }
  }
  return error?.response?.data?.detail || error?.message || 'Request failed'
}
