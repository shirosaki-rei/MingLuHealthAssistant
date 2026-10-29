// 登录态。模块级 reactive 单例，跨页面共享（App 只有一个逻辑层）。
// ponytail: 没用 Pinia，就 token + user 两个字段；等 store 之间开始互相调用再上 Pinia。

import { reactive } from 'vue'
import type { components } from '@/types/api'

type User = components['schemas']['User']

const TOKEN_KEY = 'minglu.token'
const USER_KEY = 'minglu.user'

export const auth = reactive({
	token: '',
	user: null as User | null
})

/** App 启动时调一次。getStorageSync 取不到时返回空字符串，不是 null。
 *  user 必须一起恢复：只恢复 token 的话，重启后 isLoggedIn() 是真但 auth.user 是 null，
 *  首页问候语、手机号掩码全都退化成兜底文案。 */
export function initAuth () {
	const saved = uni.getStorageSync(TOKEN_KEY)
	auth.token = typeof saved === 'string' ? saved : ''
	const savedUser = uni.getStorageSync(USER_KEY)
	if (typeof savedUser === 'string' && savedUser !== '') {
		try {
			auth.user = JSON.parse(savedUser) as User
		} catch (e) {
			// 存坏了就当没有，别让整个启动流程挂掉
			auth.user = null
		}
	}
}

export function saveAuth (token: string, user: User) {
	auth.token = token
	auth.user = user
	uni.setStorageSync(TOKEN_KEY, token)
	uni.setStorageSync(USER_KEY, JSON.stringify(user))
}

export function clearAuth () {
	auth.token = ''
	auth.user = null
	uni.removeStorageSync(TOKEN_KEY)
	uni.removeStorageSync(USER_KEY)
}

export function isLoggedIn (): boolean {
	return auth.token !== ''
}
