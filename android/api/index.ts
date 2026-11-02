// 接口清单，与 contracts/openapi.yaml 一一对应。改接口先改 yaml，再重新生成 types/api.d.ts。
// 路径带参的一律走模板串，不在这里做业务判断。

import type { components } from '@/types/api'
import { BASE_URL, request, uploadAuthHeader, type ApiFailure } from './http'
import { errText } from '@/utils/apierr'

type User = components['schemas']['User']
type LoginResult = components['schemas']['LoginResult']
type Patient = components['schemas']['Patient']
type PatientCreate = components['schemas']['PatientCreate']
type PatientUpdate = components['schemas']['PatientUpdate']
type Persona = components['schemas']['Persona']
type PersonaCreate = components['schemas']['PersonaCreate']
type ChatMessage = components['schemas']['ChatMessage']
type ConversationOut = components['schemas']['ConversationOut']
type PreConsult = components['schemas']['PreConsult']
type MedicalRecord = components['schemas']['MedicalRecord']
type DepartmentMatch = components['schemas']['DepartmentMatch']
type Doctor = components['schemas']['Doctor']
type Order = components['schemas']['Order']
type Reminder = components['schemas']['Reminder']
type ReminderCreate = components['schemas']['ReminderCreate']
type PendingReminder = components['schemas']['PendingReminder']
type Report = components['schemas']['Report']
type TimelineEvent = components['schemas']['TimelineEvent']
type AgentMemory = components['schemas']['AgentMemory']
type Membership = components['schemas']['Membership']
type RedFlagRules = components['schemas']['RedFlagRules']

/** 契约里的分页响应都是这三件套 */
export interface Paged<T> {
	items: T[]
	total: number
	has_more: boolean
}

export interface Department {
	code: string
	name: string
	description: string
	consult_enabled: boolean
}

export interface MedicalActions {
	actions: string[]
	version: string
}

export interface ExportResult {
	url: string
	expires_at: string
}

export interface SmsResult {
	request_id: string
	expires_in: number
}

export interface UploadResult {
	report_id: string
	status: string
}

/** 预问诊一轮的答案：字符串或多选值 */
export type PreConsultAnswer = string | string[]

export interface PendingResult {
	items: PendingReminder[]
}

export interface PatientsResult {
	items: Patient[]
}

export interface PersonasResult {
	presets: Persona[]
	mine: Persona[]
}

export const api = {
	// ---------- auth ----------
	sendSms: (phone: string) => request<SmsResult>('/v1/auth/sms', { method: 'POST', data: { phone } }),
	login: (phone: string, code: string) =>
		request<LoginResult>('/v1/auth/login', { method: 'POST', data: { phone, code } }),
	updateMe: (nickname: string) =>
		request<User>('/v1/me', { method: 'PATCH', data: { nickname } }),
	/** 头像走 multipart，和 uploadReport 同一套写法。返回更新后的账号。 */
	uploadAvatar: (filePath: string) =>
		new Promise<User>((resolve, reject) => {
			uni.uploadFile({
				url: BASE_URL + '/v1/me/avatar',
				filePath,
				name: 'file',
				header: uploadAuthHeader(),
				success: (res: any) => {
					try {
						resolve(JSON.parse(res.data) as User)
					} catch (e) {
						reject({ code: 'UPSTREAM_UNAVAILABLE', message: String(res.data), request_id: '' } as ApiFailure)
					}
				},
				fail: (e: any) => {
					reject({ code: 'NETWORK', message: String(e?.errMsg ?? e), request_id: '' } as ApiFailure)
				}
			} as any)
		}),

	// ---------- patient ----------
	listPatients: () => request<PatientsResult>('/v1/patients'),
	getPatient: (patientId: string) => request<Patient>('/v1/patients/' + patientId),
	createPatient: (body: PatientCreate) => request<Patient>('/v1/patients', { method: 'POST', data: body }),
	updatePatient: (patientId: string, body: PatientUpdate) =>
		request<Patient>('/v1/patients/' + patientId, { method: 'PATCH', data: body }),
	deletePatient: (patientId: string) => request<null>('/v1/patients/' + patientId, { method: 'DELETE' }),

	// ---------- persona ----------
	listPersonas: () => request<PersonasResult>('/v1/personas'),
	createPersona: (body: PersonaCreate) => request<Persona>('/v1/personas', { method: 'POST', data: body }),
	/** 自定义角色头像。和人设同一个审核对象，所以传预设角色的 id 会 404。
	 *  注意 uploadFile 的 success 在 HTTP 4xx 时也会走（那不是网络失败），
	 *  所以必须自己看 statusCode——不然 404 会被当成上传成功。 */
	uploadPersonaAvatar: (personaId: string, filePath: string) =>
		new Promise<Persona>((resolve, reject) => {
			uni.uploadFile({
				url: BASE_URL + '/v1/personas/' + personaId + '/avatar',
				filePath,
				name: 'file',
				header: uploadAuthHeader(),
				success: (res: any) => {
					const fail = (raw: any) => {
						let parsed: any = raw
						if (typeof raw === 'string') {
							try { parsed = JSON.parse(raw) } catch (e) { parsed = null }
						}
						reject({
							code: res?.statusCode === 404 ? 'NOT_FOUND' : 'UPSTREAM_UNAVAILABLE',
							message: errText(parsed), request_id: ''
						} as ApiFailure)
					}
					if (res?.statusCode !== undefined && (res.statusCode < 200 || res.statusCode >= 300)) {
						fail(res.data)
						return
					}
					try {
						resolve(JSON.parse(res.data) as Persona)
					} catch (e) {
						fail(res.data)
					}
				},
				fail: (e: any) => {
					reject({ code: 'NETWORK', message: String(e?.errMsg ?? e), request_id: '' } as ApiFailure)
				}
			} as any)
		}),

	// ---------- chat ----------
	chatHistory: (query: { page?: number; page_size?: number; patient_id?: string; conversation_id?: string }) =>
		request<Paged<ChatMessage>>('/v1/chat/history', { query }),

	// ---------- conversations ----------
	/** 会话列表（第三期）。不传 patient_id 就是全部，客户端自行分组。 */
	listConversations: (query: { patient_id?: string; include_hidden?: boolean; page?: number; page_size?: number }) =>
		request<Paged<ConversationOut>>('/v1/conversations', { query }),

	/** 归档 / 恢复。hidden=true 后从主列表消失，消息不删。 */
	archiveConversation: (conversationId: string, hidden: boolean) =>
		request<ConversationOut>('/v1/conversations/' + conversationId, { method: 'PATCH', data: { hidden } }),

	// ---------- preconsult ----------
	preconsultStart: (patientId: string, chiefComplaint: string) =>
		request<PreConsult>('/v1/preconsult/start', {
			method: 'POST',
			data: { patient_id: patientId, chief_complaint: chiefComplaint }
		}),
	preconsultAnswer: (sessionId: string, questionId: string, answer: PreConsultAnswer) =>
		request<PreConsult>('/v1/preconsult/' + sessionId + '/answer', {
			method: 'POST',
			data: { question_id: questionId, answer }
		}),
	preconsultFinish: (sessionId: string) =>
		request<MedicalRecord>('/v1/preconsult/' + sessionId + '/finish', { method: 'POST' }),
	preconsultConfirm: (sessionId: string, body: Record<string, string> = {}) =>
		request<PreConsult>('/v1/preconsult/' + sessionId + '/confirm', { method: 'POST', data: body }),

	// ---------- match ----------
	matchDepartments: (symptoms: string[], patientId?: string) =>
		request<{ items: DepartmentMatch[] }>('/v1/match/departments', {
			method: 'POST',
			// JSON.stringify 会丢掉 undefined 字段，可选参数直接给 undefined
			data: { symptoms, patient_id: patientId }
		}),
	listDoctors: (departmentCode: string, query: { online_only?: boolean; page?: number; page_size?: number } = {}) =>
		request<Paged<Doctor>>('/v1/doctors', { query: { department_code: departmentCode, ...query } }),
	getDoctor: (doctorId: string) => request<Doctor>('/v1/doctors/' + doctorId),
	listDepartments: (consultOnly = false) =>
		request<{ items: Department[] }>('/v1/departments', { query: { consult_only: consultOnly } }),

	// ---------- order ----------
	createOrder: (doctorId: string, patientId: string, preconsultSessionId?: string) =>
		request<Order>('/v1/orders', {
			method: 'POST',
			data: { doctor_id: doctorId, patient_id: patientId, preconsult_session_id: preconsultSessionId }
		}),
	getOrder: (orderId: string) => request<Order>('/v1/orders/' + orderId),
	/** 支付结果以服务端状态为准，客户端轮询 getOrder 拿最终状态 */
	payOrder: (orderId: string, channel: 'wechat' | 'alipay' = 'wechat') =>
		request<Order>('/v1/orders/' + orderId + '/pay', { method: 'POST', data: { channel } }),

	// ---------- reminder ----------
	createReminder: (body: ReminderCreate) => request<Reminder>('/v1/reminders', { method: 'POST', data: body }),
	/** 启动时与每次进提醒页都调，用于全量重排本机闹钟 */
	pendingReminders: (withinHours = 24) =>
		request<PendingResult>('/v1/reminders/pending', { query: { within_hours: withinHours } }),
	deleteReminder: (reminderId: string) => request<null>('/v1/reminders/' + reminderId, { method: 'DELETE' }),

	// ---------- report ----------
	uploadReport: (filePath: string, patientId: string) =>
		new Promise<UploadResult>((resolve, reject) => {
			uni.uploadFile({
				url: BASE_URL + '/v1/reports',
				filePath,
				name: 'file',
				header: uploadAuthHeader(),
				formData: { patient_id: patientId },
				success: (res: any) => {
					try {
						resolve(JSON.parse(res.data) as UploadResult)
					} catch (e) {
						reject({ code: 'UPSTREAM_UNAVAILABLE', message: String(res.data), request_id: '' } as ApiFailure)
					}
				},
				fail: (e: any) => {
					reject({ code: 'NETWORK', message: String(e?.errMsg ?? e), request_id: '' } as ApiFailure)
				}
			} as any)
		}),
	getReport: (reportId: string) => request<Report>('/v1/reports/' + reportId),

	// ---------- timeline ----------
	timeline: (patientId: string, query: { page?: number; page_size?: number } = {}) =>
		request<Paged<TimelineEvent>>('/v1/timeline', { query: { patient_id: patientId, ...query } }),
	exportTimeline: (patientId: string, from?: string, to?: string) =>
		request<ExportResult>('/v1/timeline/export', {
			method: 'POST',
			data: { patient_id: patientId, from, to }
		}),
	/** 公开端点，token 鉴权；返回 text/plain，所以这里 T 是 string */
	getExport: (token: string) => request<string>('/v1/exports/' + token),
	deleteTimelineEvent: (eventId: string) => request<null>('/v1/timeline/' + eventId, { method: 'DELETE' }),

	// ---------- memory ----------
	listMemories: (patientId: string, query: { page?: number; page_size?: number } = {}) =>
		request<Paged<AgentMemory>>('/v1/memories', { query: { patient_id: patientId, ...query } }),
	updateMemory: (memoryId: string, body: { value?: string; key?: string }) =>
		request<AgentMemory>('/v1/memories/' + memoryId, { method: 'PATCH', data: body }),
	deleteMemory: (memoryId: string) => request<null>('/v1/memories/' + memoryId, { method: 'DELETE' }),

	// ---------- membership ----------
	getMembership: () => request<Membership>('/v1/membership'),

	// ---------- config ----------
	/** 就医路径白名单：客户端可缓存做本地预判，最终判定以服务端为准 */
	medicalActions: () => request<MedicalActions>('/v1/config/medical-actions'),
	redFlagRules: () => request<RedFlagRules>('/v1/redflag/rules')
}
