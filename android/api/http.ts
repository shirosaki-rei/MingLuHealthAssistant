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

export interface ApiFailure {
	code: ErrorCode | 'NETWORK'
	message: string
	request_id: string
}

/**
 * 真机连哪个后端。
 *
 *   LOCAL = true   本地 USB 调试，配 `adb reverse tcp:8000 tcp:8000`
 *   LOCAL = false  线上服务器
 *
 * 电脑连手机热点时局域网是不通的（热点客户端隔离会挡掉电脑 IP），所以本地调试
 * 走 adb reverse + 127.0.0.1，别去改局域网 IP，那条路走不通。
 *
 * 上线前这里必须换成正式域名，并且私网 IP 不许打进 Release 包——现在靠这个
 * 布尔值人工保证，正式发版前应改成构建期注入 + 检查。
 */
const LOCAL = false

export const BASE_URL = LOCAL
	? 'http://127.0.0.1:8000'
	: 'http://43.143.225.197:8000'

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
				if (statusCode === 401) {
					clearAuth()
					// 必须跳登录页：只清 token 的话页面停在静默空态上——首页显示
					// 「明路用户」「还没有就诊人」「会员 —」，用户以为数据丢了。
					// token 失效不止过期一种：重新部署换了 JWT_SECRET，旧 token 全部作废，
					// 这时每个已登录用户都会撞上这个分支。
					uni.reLaunch({ url: '/pages/login/login' })
				}

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
