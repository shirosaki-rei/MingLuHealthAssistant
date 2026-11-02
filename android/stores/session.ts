// 当前会话上下文：选中的就诊人 / 角色 / 对话。
// 只在内存里，冷启动后由 home 页按就诊人列表重选（不持久化，避免换账号串号）。

import { reactive } from 'vue'

export const session = reactive({
	patientId: '',
	personaId: '',
	/** 首轮不传，之后用每轮 done 返回的 conversation_id 续 */
	conversationId: '',
	/** conversationId 归属的就诊人。换就诊人时对话页靠它判断要不要丢掉旧会话
	 *  （不能只比 session.patientId：标签页之间是 reLaunch，页面整个重建，
	 *  页面级变量记不住上一次是谁）。 */
	conversationPatientId: ''
})

export function resetSession () {
	session.patientId = ''
	session.personaId = ''
	session.conversationId = ''
}
