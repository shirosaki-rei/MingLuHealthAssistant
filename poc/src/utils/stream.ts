// 流式对话适配层（PoC 核心交付物之一）
// 三端的流式 API 不一样，这一层就是用来抹平的。
// 结论会直接决定后端用 SSE 还是 WebSocket，所以先跑它。

export interface StreamHandlers {
  /** 收到一段增量文本 */
  onDelta: (text: string) => void
  /** 正常结束 */
  onDone?: () => void
  /** 出错或不支持 */
  onError?: (e: unknown) => void
}

export interface StreamTask {
  abort: () => void
  /** 实际使用的传输方式，用于 PoC 记录 */
  transport: 'fetch' | 'chunked' | 'unsupported'
}

/** UTF-8 解码：小程序与 App 端可能没有 TextDecoder */
function utf8Decode (buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf)
  let out = ''
  let i = 0
  while (i < bytes.length) {
    const b = bytes[i++]
    if (b < 0x80) {
      out += String.fromCharCode(b)
    } else if (b < 0xe0) {
      out += String.fromCharCode(((b & 0x1f) << 6) | (bytes[i++] & 0x3f))
    } else if (b < 0xf0) {
      out += String.fromCharCode(
        ((b & 0x0f) << 12) | ((bytes[i++] & 0x3f) << 6) | (bytes[i++] & 0x3f)
      )
    } else {
      const cp =
        ((b & 0x07) << 18) |
        ((bytes[i++] & 0x3f) << 12) |
        ((bytes[i++] & 0x3f) << 6) |
        (bytes[i++] & 0x3f)
      out += String.fromCodePoint(cp)
    }
  }
  return out
}

/** SSE 帧解析：把 text 里完整的 data 行取出来，返回 [已解析增量数组, 剩余缓冲] */
function parseSSE (buffer: string): { deltas: string[]; rest: string; done: boolean } {
  const deltas: string[] = []
  let done = false
  const frames = buffer.split('\n\n')
  const rest = frames.pop() ?? ''
  for (const frame of frames) {
    const line = frame.split('\n').find(l => l.startsWith('data:'))
    if (!line) continue
    const payload = line.slice(5).trim()
    if (payload === '[DONE]') { done = true; continue }
    try {
      const obj = JSON.parse(payload)
      if (obj && typeof obj.delta === 'string') deltas.push(obj.delta)
    } catch {
      // 非 JSON 帧忽略
    }
  }
  return { deltas, rest, done }
}

export function streamChat (url: string, h: StreamHandlers): StreamTask {
  let aborted = false
  let buffer = ''

  const emit = (text: string) => {
    buffer += text
    const { deltas, rest, done } = parseSSE(buffer)
    buffer = rest
    for (const d of deltas) {
      if (!aborted) h.onDelta(d)
    }
    if (done) h.onDone?.()
  }

  // ---------- H5：fetch + ReadableStream ----------
  // #ifdef H5
  // 说明：uvue 的 lib 里 AbortSignal 可能被声明为可空，这里统一用 any 过类型检查，
  // 不影响运行（H5 已实测通过）。
  const ctrl: any = new AbortController()
  const reqInit: any = { signal: ctrl.signal }
  // 用 any 接住 fetch 的返回值：uvue 的类型声明对 fetch 的返回与 signal 判定偏严格，
  // 这里只求通过编译期检查，运行行为与标准 fetch 一致（H5 已实测通过）
  const req: any = fetch(url, reqInit)
  req
    .then(async (res: any) => {
      if (!res.body) throw new Error('no body')
      const reader: any = res.body.getReader()
      const decoder: any = new TextDecoder()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        // uvue 的 TextDecoder.decode 只声明了 1 个参数，第二参数运行期是支持的
        emit(decoder.decode(value, { stream: true }))
      }
      h.onDone?.()
    })
    .catch((e) => {
      if (!aborted) h.onError?.(e)
    })
  return { abort: () => { aborted = true; ctrl.abort() }, transport: 'fetch' }
  // #endif

  // ---------- 小程序 / App：uni.request + enableChunked ----------
  // #ifndef H5
  const task: any = uni.request({
    url,
    method: 'GET',
    enableChunked: true,
    responseType: 'text',
    success: () => { if (!aborted) h.onDone?.() },
    fail: (e: any) => { if (!aborted) h.onError?.(e) }
  } as any)

  // 关键探测：不同端回调名与数据类型不一样
  if (typeof task.onChunkReceived === 'function') {
    task.onChunkReceived((res: any) => {
      const data = res?.data
      if (typeof data === 'string') {
        emit(data)
      } else if (data instanceof ArrayBuffer) {
        emit(utf8Decode(data))
      } else if (data && data.byteLength) {
        emit(utf8Decode(data as ArrayBuffer))
      }
    })
    return { abort: () => { aborted = true; task.abort?.() }, transport: 'chunked' }
  }

  // 没有 chunk 回调 = 该端不支持流式，这是 PoC 要抓的结论
  h.onError?.(new Error('onChunkReceived 不存在：当前端不支持 chunked 流式'))
  return { abort: () => { aborted = true }, transport: 'unsupported' }
  // #endif
}

/**
 * 按句缓冲：累积到句末标点才吐给 UI
 * 目的：保证用户永远看不到未经后置校验的半句话（见 PRD §14）
 */
export function createSentenceBuffer (flush: (s: string) => void) {
  const END = /[。！？；\n]/
  let pending = ''
  return {
    push (delta: string) {
      pending += delta
      let idx = -1
      for (let i = 0; i < pending.length; i++) {
        if (END.test(pending[i])) idx = i
      }
      if (idx >= 0) {
        const ready = pending.slice(0, idx + 1)
        pending = pending.slice(idx + 1)
        flush(ready)
      }
    },
    flushRest () {
      if (pending) { flush(pending); pending = '' }
    }
  }
}
