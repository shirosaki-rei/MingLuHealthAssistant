"""全库表定义。

按架构 §2.2 的依赖规则：modules 之间不跨模块查表，只通过对方模块的 service 函数。
表集中在一个文件里只是为了少几个 import，运行时的隔离靠模块边界与 CI 的
import-linter，不靠文件拆分。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import JSON, Base, IsoDateTime, new_id, now

# ---------------------------------------------------------------- identity


class User(Base):
    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    phone: Mapped[str] = mapped_column(String(11), unique=True, index=True)
    phone_masked: Mapped[str] = mapped_column(String(16))
    nickname: Mapped[str | None] = mapped_column(String(40))
    # 头像存 storage 相对路径（avatars/xxx.jpg），URL 由 platform.storage.public_url 现算，
    # 换 PUBLIC_BASE_URL 不用回填数据库。没上传过是 NULL。
    avatar_path: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/banned/deleted
    guardian_consent_at: Mapped[datetime | None] = mapped_column(IsoDateTime)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


class SmsCode(Base):
    __tablename__ = "sms_code"

    phone: Mapped[str] = mapped_column(String(11), primary_key=True)
    code: Mapped[str] = mapped_column(String(6))
    expires_at: Mapped[datetime] = mapped_column(IsoDateTime)
    last_sent_at: Mapped[datetime] = mapped_column(IsoDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class ConsentLog(Base):
    """PIPL 举证用。"""

    __tablename__ = "consent_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(32))  # health_data/terms/privacy/guardian
    version: Mapped[str] = mapped_column(String(32))
    scene: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    agreed_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


# ---------------------------------------------------------------- profile


class PatientProfile(Base):
    __tablename__ = "patient_profile"
    __table_args__ = (Index("ix_patient_user", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(20))
    relation: Mapped[str] = mapped_column(String(16))  # self/parent/child/spouse/other
    gender: Mapped[str] = mapped_column(String(8))  # male/female/unknown
    birth_date: Mapped[str | None] = mapped_column(String(10))
    height_cm: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[float | None] = mapped_column()
    chronic_conditions: Mapped[list | None] = mapped_column(JSON)
    allergy_notes: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


# ---------------------------------------------------------------- persona


class Persona(Base):
    """只存结构化字段，不存自由 prompt（架构 §3.2）。"""

    __tablename__ = "persona"
    __table_args__ = (Index("ix_persona_owner", "owner_user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str | None] = mapped_column(String(36), index=True)  # None = 预设
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False)
    name: Mapped[str] = mapped_column(String(12))
    species: Mapped[str] = mapped_column(String(30))
    self_ref: Mapped[str | None] = mapped_column(String(16))
    catchphrase: Mapped[str | None] = mapped_column(String(40))
    worldview: Mapped[str | None] = mapped_column(Text)
    tones: Mapped[list | None] = mapped_column(JSON)
    # 自定义角色上传的头像，存 storage 相对路径（avatars/xxx.jpg）。预设角色是 NULL，
    # 头像由 seed 写死走 /static/avatars/*.jpg，用户改不了。
    avatar_path: Mapped[str | None] = mapped_column(String(255))
    focus: Mapped[list | None] = mapped_column(JSON)
    greeting: Mapped[str | None] = mapped_column(Text)
    specialty: Mapped[str | None] = mapped_column(String(32))
    proactive: Mapped[bool] = mapped_column(Boolean, default=True)
    review_status: Mapped[str] = mapped_column(String(16), default="approved")  # pending/approved/rejected
    review_reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="preset")  # preset/member/single_purchase
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


# ---------------------------------------------------------------- chat


class Conversation(Base):
    __tablename__ = "conversation"
    __table_args__ = (Index("ix_conv_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    patient_id: Mapped[str] = mapped_column(String(36), index=True)
    persona_id: Mapped[str | None] = mapped_column(String(36))
    scene: Mapped[str] = mapped_column(String(16), default="chat")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (Index("ix_msg_conv_created", "conversation_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversation.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user/assistant/system
    content: Mapped[str] = mapped_column(Text)
    source_refs: Mapped[list | None] = mapped_column(JSON)
    redflag_level: Mapped[str] = mapped_column(String(16), default="none")
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class KbCitation(Base):
    """引用快照：词条更新后仍可回溯当时依据。"""

    __tablename__ = "kb_citation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(String(36), index=True)
    entry_id: Mapped[str] = mapped_column(String(36), index=True)
    entry_version: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    publisher: Mapped[str] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


class AgentMemory(Base):
    """记忆绑定就诊人，不绑定角色（PRD §4.3）。"""

    __tablename__ = "agent_memory"
    __table_args__ = (Index("ix_memory_patient", "patient_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    patient_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(8))  # fact/pref/event
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)
    source_msg_id: Mapped[str | None] = mapped_column(String(36))
    confirmed_at: Mapped[datetime | None] = mapped_column(IsoDateTime)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


class AiLog(Base):
    """input/output 落库前掩码（PRD §12）。"""

    __tablename__ = "ai_log"
    __table_args__ = (Index("ix_ailog_rid", "request_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36))
    scene: Mapped[str] = mapped_column(String(32))
    conversation_id: Mapped[str | None] = mapped_column(String(36))
    input_masked: Mapped[str | None] = mapped_column(Text)
    output_masked: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64))
    retrieval_ids: Mapped[list | None] = mapped_column(JSON)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    injected_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[str | None] = mapped_column(String(120))
    redflag_rule_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


# ---------------------------------------------------------------- preconsult


class PreConsult(Base):
    __tablename__ = "pre_consult"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    patient_id: Mapped[str] = mapped_column(String(36), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36))
    chief_complaint: Mapped[str] = mapped_column(String(500))
    structured_json: Mapped[dict | None] = mapped_column(JSON)  # {question_id: answer} + 定稿字段
    round: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="asking")  # asking/confirming/done/aborted_redflag
    redflag_level: Mapped[str] = mapped_column(String(16), default="none")
    insufficient: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


# ---------------------------------------------------------------- match


class Department(Base):
    __tablename__ = "department"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    parent_code: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    consult_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class DepartmentSymptom(Base):
    """症状 → 科室 映射表。科室映射降级路径（架构 §14 #8）。"""

    __tablename__ = "department_symptom"
    __table_args__ = (UniqueConstraint("department_code", "symptom", name="uq_dept_symptom"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    department_code: Mapped[str] = mapped_column(String(32), index=True)
    symptom: Mapped[str] = mapped_column(String(40), index=True)
    weight: Mapped[float] = mapped_column(default=1.0)


class Doctor(Base):
    __tablename__ = "doctor"
    __table_args__ = (Index("ix_doctor_dept_online", "department_code", "online_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(40))
    department_code: Mapped[str] = mapped_column(String(32), index=True)
    sub_specialty: Mapped[list | None] = mapped_column(JSON)
    title: Mapped[str] = mapped_column(String(40))
    hospital: Mapped[str] = mapped_column(String(120))
    intro: Mapped[str | None] = mapped_column(Text)
    specialties: Mapped[list | None] = mapped_column(JSON)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    avg_reply_hours: Mapped[float] = mapped_column(default=24.0)
    rating: Mapped[float] = mapped_column(default=5.0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    online_status: Mapped[str] = mapped_column(String(16), default="offline")  # online/busy/offline
    settle_ratio: Mapped[float] = mapped_column(default=0.7)
    license_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


# ---------------------------------------------------------------- consult


class ConsultOrder(Base):
    """金额拆 price / platform_fee / doctor_income / tax_amount，结算可对账（架构 §3.2）。"""

    __tablename__ = "consult_order"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    patient_id: Mapped[str] = mapped_column(String(36), index=True)
    doctor_id: Mapped[str] = mapped_column(String(36), index=True)
    pre_consult_id: Mapped[str | None] = mapped_column(String(36))
    price_cents: Mapped[int] = mapped_column(Integer)
    platform_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    doctor_income_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    channel: Mapped[str | None] = mapped_column(String(16))
    pay_params: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="pending_pay")
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)
    paid_at: Mapped[datetime | None] = mapped_column(IsoDateTime)


# ---------------------------------------------------------------- reminder


class Reminder(Base):
    __tablename__ = "reminder"
    __table_args__ = (Index("ix_reminder_next_fire", "status", "fire_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    patient_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # medication/measurement/followup/custom
    title: Mapped[str] = mapped_column(String(40))
    content: Mapped[str | None] = mapped_column(Text)
    fire_at: Mapped[datetime] = mapped_column(IsoDateTime)
    recurrence: Mapped[str] = mapped_column(String(64), default="once")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/fired/cancelled
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


class NotifyLog(Base):
    """每次推送一条，用于频控与合规举证（架构 §3.2）。"""

    __tablename__ = "notify_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    category: Mapped[str] = mapped_column(String(16))  # proactive/reminder/system
    channel: Mapped[str | None] = mapped_column(String(16))
    template: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str | None] = mapped_column(String(16))
    sent_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


# ---------------------------------------------------------------- health


class HealthRecord(Base):
    __tablename__ = "health_record"
    __table_args__ = (Index("ix_health_patient_measured", "patient_id", "measured_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(32))  # blood_pressure/weight/...
    value: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str | None] = mapped_column(String(16))
    ref_low: Mapped[float | None] = mapped_column()
    ref_high: Mapped[float | None] = mapped_column()
    abnormal_flag: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(16))
    measured_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


class ReportDoc(Base):
    __tablename__ = "report_doc"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    image_path: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str] = mapped_column(Text)
    ocr_json: Mapped[dict | None] = mapped_column(JSON)
    items: Mapped[list | None] = mapped_column(JSON)
    abnormal_items: Mapped[list | None] = mapped_column(JSON)
    ai_comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing/done/failed
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


class TimelineEvent(Base):
    __tablename__ = "timeline_event"
    __table_args__ = (Index("ix_timeline_patient_occurred", "patient_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(16))  # consult/report/preconsult/measurement/medication
    ref_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)


class ShareExport(Base):
    """就诊摘要导出。token 不可枚举，7 天失效。"""

    __tablename__ = "share_export"

    token: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)
    expires_at: Mapped[datetime] = mapped_column(IsoDateTime)


# ---------------------------------------------------------------- knowledge


class KbEntry(Base):
    __tablename__ = "kb_entry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list | None] = mapped_column(JSON)
    department_code: Mapped[str | None] = mapped_column(String(32), index=True)
    category: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    seek_care_signs: Mapped[list | None] = mapped_column(JSON)
    source: Mapped[str | None] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str | None] = mapped_column(String(40))
    reviewer_license: Mapped[str | None] = mapped_column(String(40))
    reviewed_at: Mapped[datetime | None] = mapped_column(IsoDateTime)
    valid_until: Mapped[datetime | None] = mapped_column(IsoDateTime)
    version: Mapped[str] = mapped_column(String(16), default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


class RedFlagRule(Base):
    __tablename__ = "redflag_rule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    keywords: Mapped[list] = mapped_column(JSON)
    logic_expr: Mapped[str | None] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(16))  # attention/urgent/crisis
    action: Mapped[str] = mapped_column(String(32))  # show_card/call_120/encourage_emergency
    message: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str] = mapped_column(String(16), default="1")
    created_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now)


# ---------------------------------------------------------------- membership


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("user_id", name="uq_membership_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    plan: Mapped[str] = mapped_column(String(16), default="none")  # monthly/yearly/none
    status: Mapped[str] = mapped_column(String(16), default="none")  # none/active/expired
    start_at: Mapped[datetime | None] = mapped_column(IsoDateTime)
    end_at: Mapped[datetime | None] = mapped_column(IsoDateTime)
    channel: Mapped[str | None] = mapped_column(String(16))
    order_no: Mapped[str | None] = mapped_column(String(32))
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(IsoDateTime, default=now, onupdate=now)


__all__ = [name for name in dir() if name[0].isupper()]
