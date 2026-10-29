// 错误信息提取。契约规定 message 可直接展示，所以默认就用它；
// 网络错误（客户端自己造的 NETWORK）才有兜底文案。
import type { ApiFailure } from '@/api/http'

export function errText (e: any): string {
	if (e === null || e === undefined) return '未知错误'
	const code = e.code as string | undefined
	const message = e.message as string | undefined
	if (code === 'NETWORK') return '网络不通，检查接口地址或网络'
	if (message !== undefined && message !== '') return message
	return '出错了，请稍后重试'
}

/** 是否「未登录」——用于全局登出兜底 */
export function isUnauthorized (e: any): boolean {
	return (e as ApiFailure)?.code === 'UNAUTHORIZED'
}
