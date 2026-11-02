"""请求/响应模型。字段与 contracts/openapi.yaml 一一对应，改契约先改 yaml 再改这里。

两个硬约定在此落地：
- 时间一律 aware datetime，Pydantic v2 序列化为 2026-10-22T09:00:00+08:00
- 金额一律 int 分
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Relation = Literal["self", "parent", "child", "spouse", "other"]
Gender = Literal["male", "female", "unknown"]
RedFlagLevel = Literal["none", "attention", "urgent", "crisis"]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ 请求


class SmsRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")


class LoginRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    code: str = Field(min_length=4, max_length=6)


class MeUpdate(BaseModel):
    """只改昵称。手机号是登录凭据，不在这里改。"""

    nickname: str = Field(min_length=1, max_length=40)


class PatientCreate(BaseModel):
    name: str = Field(max_length=20)
    relation: Relation
    gender: Gender
    birth_date: date | None = None
    height_cm: int | None = None
    weight_kg: float | None = None
    chronic_conditions: list[str] | None = None
    allergy_notes: str | None = None
    guardian_consent: bool | None = Field(default=None, description="未成年人必填 true")


class PatientUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=20)
    height_cm: int | None = None
    weight_kg: float | None = None
    chronic_conditions: list[str] | None = None
    allergy_notes: str | None = None


class PersonaCreate(BaseModel):
    name: str = Field(max_length=12)
    species: str = Field(max_length=12)
    self_ref: str | None = Field(default=None, max_length=12)
    catchphrase: str | None = Field(default=None, max_length=40)
    worldview: str | None = Field(default=None, max_length=200)
    tones: list[str] | None = Field(default=None, max_length=3)


class ChatRequest(BaseModel):
    patient_id: str
    content: str = Field(max_length=2000)
    conversation_id: str | None = None
    persona_id: str | None = None


class PreConsultStart(BaseModel):
    patient_id: str
    chief_complaint: str = Field(max_length=500)


class PreConsultAnswer(BaseModel):
    question_id: str
    answer: str | list[str]


class PreConsultConfirm(BaseModel):
    chief_complaint: str | None = Field(default=None, max_length=500)
    present_illness: str | None = Field(default=None, max_length=2000)
    past_history: str | None = Field(default=None, max_length=1000)
    allergy_history: str | None = Field(default=None, max_length=1000)
    medication_history: str | None = Field(default=None, max_length=1000)


class MatchRequest(BaseModel):
    symptoms: list[str] = Field(min_length=1, max_length=10)
    patient_id: str | None = None


class OrderCreate(BaseModel):
    doctor_id: str
    patient_id: str
    preconsult_session_id: str | None = None


class OrderPay(BaseModel):
    channel: Literal["wechat", "alipay"] | None = "wechat"


class ReminderCreate(BaseModel):
    patient_id: str
    kind: Literal["medication", "measurement", "followup", "custom"]
    title: str = Field(max_length=40)
    content: str | None = Field(default=None, max_length=200)
    fire_at: datetime
    recurrence: str = "once"


class TimelineExport(BaseModel):
    patient_id: str
    start: datetime | None = None
    end: datetime | None = None


class MemoryUpdate(BaseModel):
    key: str | None = Field(default=None, max_length=100)
    value: str | None = Field(default=None, max_length=500)


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


# ------------------------------------------------------------------ 响应


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] | None = None


class UserOut(ApiModel):
    id: str
    phone_masked: str
    nickname: str | None = None
    avatar_url: str | None = None
    created_at: datetime


class LoginResult(ApiModel):
    token: str
    expires_in: int
    user: UserOut


class SmsResult(ApiModel):
    request_id: str
    expires_in: int


class PatientOut(ApiModel):
    id: str
    name: str
    relation: Relation
    gender: Gender
    birth_date: date | None = None
    height_cm: int | None = None
    weight_kg: float | None = None
    chronic_conditions: list[str] | None = None
    allergy_notes: str | None = None
    created_at: datetime


class PatientList(ApiModel):
    items: list[PatientOut]


class PersonaOut(ApiModel):
    id: str
    name: str
    species: str
    self_ref: str | None = None
    catchphrase: str | None = None
    worldview: str | None = None
    tones: list[str] | None = None
    is_preset: bool
    review_status: Literal["pending", "approved", "rejected"] | None = None
    # 审核通过才给 URL：没审过的用户图片不外发，别让它在对话里先露出来
    avatar_url: str | None = None
    created_at: datetime | None = None


class PersonaList(ApiModel):
    presets: list[PersonaOut]
    mine: list[PersonaOut]


class SourceRef(ApiModel):
    title: str
    publisher: str
    url: str | None = None
    updated_at: datetime


class ChatMessageOut(ApiModel):
    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    source_refs: list[SourceRef] | None = None
    redflag_level: RedFlagLevel = "none"
    created_at: datetime


class ChatHistory(ApiModel):
    items: list[ChatMessageOut]
    total: int
    has_more: bool


class ConversationOut(ApiModel):
    """会话列表行（第三期：历史对话 / 归档）。"""

    id: str
    patient_id: str
    patient_name: str
    persona_id: str | None = None
    persona_name: str
    last_message: str
    message_count: int
    created_at: datetime
    hidden: bool = False


class ConversationList(ApiModel):
    items: list[ConversationOut]
    total: int
    has_more: bool


class ConversationArchive(ApiModel):
    hidden: bool


class Question(ApiModel):
    id: str
    text: str
    type: Literal["single", "multiple", "text", "number"]
    options: list[str] | None = None
    required: bool = True


class PreConsultOut(ApiModel):
    id: str
    status: Literal["asking", "confirming", "done", "aborted_redflag"]
    round: int
    questions: list[Question] = Field(default_factory=list)
    answers: dict[str, Any] | None = None
    redflag_level: RedFlagLevel = "none"
    insufficient: bool = False


class MedicalRecordOut(ApiModel):
    session_id: str
    chief_complaint: str
    present_illness: str
    past_history: str | None = None
    allergy_history: str | None = None
    medication_history: str | None = None
    suggested_departments: list[str] | None = None
    ready_for_confirm: bool


class DepartmentMatch(ApiModel):
    code: str
    name: str
    confidence: float = Field(ge=0, le=1)
    reason: str
    consult_enabled: bool = False


class DepartmentMatchList(ApiModel):
    items: list[DepartmentMatch]


class DoctorOut(ApiModel):
    id: str
    name: str
    department_code: str
    department_name: str | None = None
    title: str
    hospital: str | None = None
    specialties: list[str] | None = None
    online_status: Literal["online", "busy", "offline"]
    price_cents: int
    response_minutes: int
    match_reason: str | None = None


class DoctorList(ApiModel):
    items: list[DoctorOut]
    total: int
    has_more: bool


class OrderOut(ApiModel):
    id: str
    status: Literal[
        "pending_pay", "paid", "doctor_replying", "completed", "refunded", "cancelled"
    ]
    amount_cents: int
    doctor_id: str | None = None
    patient_id: str | None = None
    pay_params: dict[str, Any] | None = None
    created_at: datetime
    paid_at: datetime | None = None


class ReminderOut(ApiModel):
    id: str
    patient_id: str
    kind: Literal["medication", "measurement", "followup", "custom"]
    title: str
    content: str | None = None
    fire_at: datetime
    recurrence: str = "once"
    status: Literal["pending", "fired", "cancelled"]
    created_at: datetime


class PendingReminder(ApiModel):
    id: str
    fire_at: datetime
    title: str
    content: str | None = None


class PendingReminderList(ApiModel):
    items: list[PendingReminder]


class ReportItem(ApiModel):
    name: str
    value: str
    unit: str
    ref_low: float | None = None
    ref_high: float | None = None
    flag: Literal["normal", "low", "high", "abnormal"] = "normal"


class ReportOut(ApiModel):
    id: str
    patient_id: str
    status: Literal["processing", "done", "failed"]
    image_url: str | None = None
    items: list[ReportItem] | None = None
    summary: str | None = None
    created_at: datetime


class ReportAccepted(ApiModel):
    report_id: str
    status: Literal["processing"]


class TimelineEventOut(ApiModel):
    id: str
    type: Literal["consult", "report", "preconsult", "measurement", "medication"]
    occurred_at: datetime
    title: str
    summary: str | None = None
    ref_id: str | None = None


class TimelineList(ApiModel):
    items: list[TimelineEventOut]
    total: int
    has_more: bool


class TimelineExportResult(ApiModel):
    url: str
    expires_at: datetime


class MembershipOut(ApiModel):
    status: Literal["none", "active", "expired"]
    plan: Literal["monthly", "yearly", "none"]
    expires_at: datetime
    entitlements: dict[str, Any]


class MedicalActions(ApiModel):
    actions: list[str]
    version: str


class RedFlagRuleOut(ApiModel):
    id: str
    keywords: list[str]
    level: Literal["attention", "urgent", "crisis"]
    action: Literal["show_card", "call_120", "encourage_emergency"]
    message: str | None = None


class RedFlagRules(ApiModel):
    version: str
    rules: list[RedFlagRuleOut]


class AgentMemoryOut(ApiModel):
    id: str
    patient_id: str
    type: Literal["fact", "pref", "event"]
    key: str
    value: str
    source_msg_id: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime


class MemoryList(ApiModel):
    items: list[AgentMemoryOut]
    total: int
    has_more: bool


class Page(BaseModel):
    """分页响应公共头。"""

    total: int
    has_more: bool
