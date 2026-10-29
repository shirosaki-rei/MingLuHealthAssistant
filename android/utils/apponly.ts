/** 小程序端不提供「健康」和「提醒」两块：点了弹「去 App」，不跳转。
 *
 *  为什么挂在每个页面的 go / switchTab 上，而不是逐个入口套 #ifdef：
 *  入口有六七处（5 个标签页的底部标签栏、首页宫格、「我的」页列表、健康页里的
 *  用药提醒），漏一个就还能跳进去。两个导航函数是所有入口的必经之路，拦在这儿不会漏。
 *
 *  App / H5 上永远返回 false，等于不存在——不为小程序在正式端留分支代码。
 */
const APP_ONLY: Record<string, string> = {
	'/pages/health/health': '健康档案',
	'/pages/reminder/reminder': '用药与测量提醒'
}

export function appOnly (url: string): boolean {
	// #ifdef MP
	const label = APP_ONLY[url]
	if (label !== undefined) {
		uni.showModal({
			title: '去明路 App 使用',
			content: label + '只在 App 里提供。微信里可以先问助手、查知识、找医生。',
			showCancel: false,
			confirmText: '知道了'
		})
		return true
	}
	// #endif
	return false
}
