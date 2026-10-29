// SSE 流式对话。契约见 contracts/openapi.yaml 的 POST /v1/chat/stream
//
// 关键约定（不要自己发明）：
// - 事件名只有 delta / redflag / done
// - 一个 delta 至少是一个完整句子，服务端按句缓冲后才推，客户端收到即渲染，不需要拼句
// - 命中红旗：先 redflag 再 done，且不再生成正文
// - 每轮 done 都带 conversation_id / message_id，首轮必须拿它续下一轮，否则会开新会话

import type { components } from '@/types/api'
import { BASE_URL, type ApiFailure } from './http'
import { auth } from '@/stores/auth'
import { formatError } from '@/utils/err'

export type ChatRequestBody = components['schemas']['ChatRequest']

export interface RedFlagEvent {
	level: string
	message: string
	action: string
}

export interface ChatDoneEvent {
	request_id: string
	conversation_id: string
	message_id: string
	usage: { in: number; out: number }
}

export interface ChatStreamHandlers {
	onDelta: (text: string) => void
	onRedFlag?: (e: RedFlagEvent) => void
	onDone?: (e: ChatDoneEvent) => void
	onError?: (e: ApiFailure) => void
}

export interface ChatStreamTask {
	abort: () => void
}

/** 小程序与 App 端可能没有 TextDecoder，自己解 UTF-8 */
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

export function streamChat (body: ChatRequestBody, h: ChatStreamHandlers): ChatStreamTask {
	let aborted = false
	let buffer = ''
	let sawDone = false

	const emit = (text: string) => {
		buffer += text
		// 帧之间以空行分隔，最后一段可能不完整，留在 buffer 里等下一个 chunk
		const frames = buffer.split('\n\n')
		buffer = frames.pop() ?? ''
		for (const frame of frames) {
			let event = 'message'
			const dataLines: string[] = []
			for (const line of frame.split('\n')) {
				if (line.startsWith('event:')) event = line.slice(6).trim()
				else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
			}
			if (dataLines.length === 0) continue
			let payload: any = null
			try {
				payload = JSON.parse(dataLines.join('\n'))
			} catch {
				continue
			}
			if (aborted) continue
			if (event === 'delta' && typeof payload.delta === 'string') {
				h.onDelta(payload.delta)
			} else if (event === 'redflag') {
				h.onRedFlag?.(payload as RedFlagEvent)
			} else if (event === 'done') {
				sawDone = true
				h.onDone?.(payload as ChatDoneEvent)
			}
		}
	}

	const header: Record<string, string> = {
		'Content-Type': 'application/json',
		'Accept': 'text/event-stream'
	}
	if (auth.token !== '') header['Authorization'] = 'Bearer ' + auth.token

	const task: any = uni.request({
		url: BASE_URL + '/v1/chat/stream',
		method: 'POST',
		header,
		data: JSON.stringify(body),
		enableChunked: true,
		responseType: 'text',
		timeout: 120000,
		success: () => {
			if (aborted) return
			// 流正常结束却没等到 done：连接被中断，不能当成正常收尾
			if (!sawDone) {
				h.onError?.({ code: 'UPSTREAM_UNAVAILABLE', message: '连接中断，未收到完成事件', request_id: '' })
			}
		},
		fail: (e: any) => {
			if (!aborted) h.onError?.({ code: 'NETWORK', message: formatError(e), request_id: '' })
		}
	} as any)

	if (typeof task.onChunkReceived !== 'function') {
		h.onError?.({ code: 'UPSTREAM_UNAVAILABLE', message: '当前端不支持 chunked 流式', request_id: '' })
		return { abort: () => { aborted = true } }
	}

	task.onChunkReceived((res: any) => {
		if (aborted) return
		const data = res?.data
		if (typeof data === 'string') {
			emit(data)
		} else if (data instanceof ArrayBuffer) {
			emit(utf8Decode(data))
		} else if (data !== null && data !== undefined && data.byteLength > 0) {
			emit(utf8Decode(data as ArrayBuffer))
		}
	})

	return {
		abort: () => {
			aborted = true
			try {
				task.abort?.()
			} catch {
				// 请求已结束时 abort 会抛错，属正常情况
			}
		}
	}
}
