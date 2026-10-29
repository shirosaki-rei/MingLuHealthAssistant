// 助手头像。
//
// 分两类，规则完全相反：
// - **预设角色**：四张打包图（static/avatars/*.jpg），由服务端按角色名固定下发，
//   用户没有任何入口改。这里只做「名字 → 图」的本地兜底，服务端没给 URL 时用它。
// - **自定义角色**：用户上传，跟人设同一个审核对象。审核通过后服务端才给 URL，
//   之前本地显示名字首字。
//
// 本地不再存「用户自己选的颜色/字」了——那套（2026-10-28 之前）随预设图上线一起删掉，
// 自定义角色的头像走上传审核，不落在设备存储里。

import type { components } from '@/types/api'

type Persona = components['schemas']['Persona']

export interface AvatarView {
	/** 有图就是图，没图就是空串 */
	url: string
	/** 图片加载失败或没有时的兜底字 */
	glyph: string
	/** 只有没有图时才用得上：按角色 id 散一把，不同角色自动不同色 */
	color: string
}

/** 预设角色名 → 打包图。和服务端 `persona.PRESET_AVATARS` 一一对应，
 *  两边改一边就会出现「服务端给了图、本地不认」。 */
const PRESET_FILES: Record<string, string> = {
	安安: '/static/avatars/anan.jpg',
	老陈: '/static/avatars/laochen.jpg',
	小简: '/static/avatars/xiaojian.jpg',
	小叶: '/static/avatars/xiaoye.jpg'
}

export function presetAvatarOf (name: string): string {
	return PRESET_FILES[name] ?? ''
}

/** 没有图时的底色。按 id 散一把，用户不动手也不撞色。 */
export function avatarColorOf (personaId: string): string {
	const PALETTE = ['#2F7D63', '#3C6FB0', '#7E5CC0', '#B26A33', '#C04A62', '#4A8A3C', '#2E7C8C', '#8A6538']
	let h = 0
	for (let i = 0; i < personaId.length; i++) h = (h * 31 + personaId.charCodeAt(i)) % 997
	return PALETTE[h % PALETTE.length]
}

/** 对话页和角色列表都靠它决定「显示图还是显示字」。
 *  优先级：服务端给的 URL（预设图或审核通过的上传图）> 名字首字 + 派生色。 */
export function avatarOf (p: Persona | null): AvatarView {
	if (p === null) return { url: '', glyph: '明', color: avatarColorOf('') }
	const name = p.name ?? ''
	const url = p.avatar_url ?? presetAvatarOf(name)
	return {
		url,
		glyph: name === '' ? '明' : name.slice(0, 1),
		color: avatarColorOf(p.id)
	}
}
