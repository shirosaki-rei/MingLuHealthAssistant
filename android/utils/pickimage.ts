// 选一张图片。账号头像和上传报告都走这儿。

import type { ApiFailure } from '@/api/http'

/**
 * 拿到临时文件路径；用户取消时 resolve('')，调用方直接 return 就行。
 *
 * `albumMode: 'system'` 与 `sourceType: ['album']` 都是必须的，少写一个就选不中图
 * （`docs/agent/vapor-mode.md` §9d）：
 * - `sourceType` 默认 `['album','camera']`，会先弹 uni 自己的动作面板，而那个面板的选项行
 *   收不到点击——点了「从相册选择」面板会关、相册永远不打开，success/fail 都不回来。
 *   所以只给一端，面板这一步整个绕掉。
 * - `albumMode` 默认 `'custom'`，是 uni 自己在 vapor 渲染树里画的相册，和 `<picker>` 滚轮
 *   同一个毛病，格子点不动。写 `'system'` 走系统相册（独立 activity，不走 vapor 渲染）。
 *
 * `albumMode` 只在选相册时传：它和 `sourceType: ['camera']` 一起传，实测相机根本起不来
 * （不报错，success/fail 都不回调）。
 *
 * `sizeType` 只要压缩图：头像和报告都用不到原图，上传能小一个数量级。
 */
export function pickImage (source: 'album' | 'camera' = 'album'): Promise<string> {
	return new Promise<string>((resolve, reject) => {
		const options: any = {
			count: 1,
			sizeType: ['compressed'],
			sourceType: [source],
			success: (res: any) => {
				const paths = res.tempFilePaths as string[]
				// 没选中也当取消：别为这个弹错误
				resolve(paths === undefined || paths.length === 0 ? '' : paths[0])
			},
			fail: (e: any) => {
				const msg = String(e === undefined || e.errMsg === undefined ? '' : e.errMsg)
				if (msg.indexOf('cancel') >= 0) {
					resolve('')
					return
				}
				reject({
					code: 'NETWORK',
					message: '无法选择图片，请检查相册权限',
					request_id: ''
				} as ApiFailure)
			}
		}
		if (source === 'album') options.albumMode = 'system'
		uni.chooseImage(options)
	})
}
