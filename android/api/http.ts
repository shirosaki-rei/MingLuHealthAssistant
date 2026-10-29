// 请求层：token 注入 + 错误归一。页面不直接调 uni.request（架构设计 §6.1）
//
// 契约要点（contracts/openapi.yaml 文件头）：
// - 成功响应是裸业务对象，request_id 走 X-Request-Id 响应头
// - 错误响应统一 { code, message, request_id }，按 code 分支，不按 HTTP 状态分支
// - app-android 的 uni.request data 只收 UTSJSONObject / string，所以统一 JSON.stringify

import { auth, clearAuth } from '@/stores/auth'
import type { components } from '@/types/api'
import { formatError } from '@/utils/err'

export type ErrorCode = components['schemas']['ErrorCode']

/** 服务端错误体。NETWORK 是客户端自己造的（uni.request fail，没拿到响应体） */
export interface ApiFailure {
	code: ErrorCode | 'NETWORK'
	message: string
	request_id: string
}

/**
 * 真机调试改这里：填电脑的 WLAN 局域网 IP（不是 WSL/虚拟网卡的地址）。
 *
 * 查法：cmd 里执行 ipconfig，看「无线局域网适配器 WLAN」下的 IPv4 地址。
 * 注意：
 * - localhost / 127.0.0.1 在手机上指向手机自己，连不上电脑
 * - 172.23.x.x 这类一般是 WSL 或 Hyper-V 的虚拟网卡，手机访问不到
 * - 换 Wi-Fi 或重连后 IP 会变，连不上时先来这行确认
 */
export const BASE_URL = 'http://10.43.253.140:8000'

interface RequestOptions {
	method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
	data?: any
	query?: Record<string, string | number | boolean | null | undefined>
}

function buildUrl (path: string, query?: RequestOptions['query']): string {
	if (query === undefined) return BASE_URL + path
	const parts: string[] = []
	for (const key in query) {
		const value = query[key]
		if (value === null || value === undefined || value === '') continue
		parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(value)))
	}
	return parts.length > 0 ? BASE_URL + path + '?' + parts.join('&') : BASE_URL + path
}

function authHeader (): Record<string, string> {
	const header: Record<string, string> = { 'Content-Type': 'application/json' }
	if (auth.token !== '') header['Authorization'] = 'Bearer ' + auth.token
	return header
}

/**
 * 文件上传（multipart）用的请求头。`uni.uploadFile` 不吃 `uni.request` 那套全局配置，
 * 必须逐个调用自己传——漏了就是 401，而服务端只回一个裸 401，前端看不出是没带头。
 */
export function uploadAuthHeader (): Record<string, string> {
	return auth.token === '' ? {} : { Authorization: 'Bearer ' + auth.token }
}

/**
 * 发一个请求，2xx 返回业务对象，其余 reject 成 ApiFailure。
 * dataType 用 text 再自己 parse：204 与 text/plain（导出摘要）都不是 JSON，
 * 交给框架自动 parse 会走 json 解析错误的 fail 分支。
 */
export function request<T> (path: string, options: RequestOptions = {}): Promise<T> {
	const method = options.method ?? 'GET'
	const hasBody = method === 'POST' || method === 'PATCH'

	return new Promise<T>((resolve, reject) => {
		uni.request({
			url: buildUrl(path, options.query),
			method,
			header: authHeader(),
			data: hasBody ? JSON.stringify(options.data ?? {}) : '',
			dataType: 'text',
			timeout: 30000,
			success: (res: any) => {
				const statusCode: number = res.statusCode
				if (statusCode === 401) clearAuth()

				if (statusCode >= 200 && statusCode < 300) {
					const raw = res.data
					if (typeof raw === 'string') {
						if (raw.length === 0) {
							resolve(null as T)
							return
						}
						try {
							resolve(JSON.parse(raw) as T)
						} catch {
							// 非 JSON 响应（如 GET /v1/exports/{token} 的 text/plain）原样返回
							resolve(raw as T)
						}
						return
					}
					resolve(raw as T)
					return
				}

				const body = res.data as any
				reject({
					code: body?.code ?? 'UPSTREAM_UNAVAILABLE',
					message: body?.message ?? ('HTTP ' + statusCode),
					request_id: body?.request_id ?? ''
				} as ApiFailure)
			},
			fail: (e: any) => {
				reject({ code: 'NETWORK', message: formatError(e), request_id: '' } as ApiFailure)
			}
		} as any)
	})
}
