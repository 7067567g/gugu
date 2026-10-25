/** M1 export surface: endpoint path/method and the base64 → download handoff. */
import { describe, expect, it, vi } from 'vitest'

import { bindApi, downloadCanvasExport, exportCanvas } from './api'

describe('canvas export', () => {
  it('posts to the project export endpoint', async () => {
    const calls: Array<{ opts?: Record<string, unknown>; path: string }> = []

    bindApi(
      async <T,>(
        path: string,
        opts?: {
          body?: unknown
          method?: string
          timeoutMs?: number
          upload?: { bytes: ArrayBuffer; contentType?: string; filename: string }
        }
      ): Promise<T> => {
        calls.push({ opts: opts as Record<string, unknown>, path })

        return { bytes: 12, data: 'eA==', filename: 'p1-r3-deadbeef.zip', revision: 3, sha256: 'x' } as T
      }
    )

    const result = await exportCanvas('p1')

    expect(result.filename).toBe('p1-r3-deadbeef.zip')
    expect(calls[0]?.path).toBe('/projects/p1/export')
    expect(calls[0]?.opts?.method).toBe('POST')
  })

  it('downloads the zip under the server filename and revokes the blob URL', () => {
    const createObjectURL = vi.fn(() => 'blob:mock-export')
    const revokeObjectURL = vi.fn()
    const click = vi.fn()
    const anchor = { click, download: '', href: '' }

    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    const createElement = vi.spyOn(document, 'createElement').mockReturnValue(anchor as unknown as HTMLElement)

    downloadCanvasExport({
      bytes: 3,
      data: btoa('zip'),
      filename: 'p1-r3-deadbeef.zip',
      revision: 3,
      sha256: 'x'
    })

    expect(createElement).toHaveBeenCalledWith('a')
    expect(anchor.download).toBe('p1-r3-deadbeef.zip')
    expect(anchor.href).toBe('blob:mock-export')
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-export')
    createElement.mockRestore()
  })
})
