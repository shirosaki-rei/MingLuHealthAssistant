"""运营后台（内部 Web）。

PRD §1.2：运营后台是内部系统，不计入对外三端。这里只做三件事：
审核自定义角色、看数据、维护知识库/医生/科室/红旗规则。

安全边界（这不是能给 C 端用户看的界面）：
- 独立账号密码 `ADMIN_USERNAME` / `ADMIN_PASSWORD`，**不复用** C 端手机号登录体系；
- 非 dev 环境不许沿用默认密码，启动自检直接拒（和 `JWT_SECRET` 一个路子，见 config）；
- 挂在 `/admin` 下，不参与 `/v1/*` 的 action 自检，也不进 OpenAPI 契约；
- 默认全部只读，只有运营真要维护的几张表开写（见下面两组清单）。

用法：后端跑起来后开 `http://<主机>:8000/admin`。
"""
from __future__ import annotations

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend

from .. import models
from ..config import get_settings
from ..database import get_engine


class AdminAuth(AuthenticationBackend):
    """账号密码 + session cookie。secret_key 借用 jwt_secret（都是本进程的签名密钥）。"""

    async def login(self, request) -> bool:
        form = await request.form()
        settings = get_settings()
        ok = (
            form.get("username") == settings.admin_username
            and form.get("password") == settings.admin_password
        )
        if ok:
            request.session.update({"admin": True})
        return ok

    async def logout(self, request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request) -> bool:
        return bool(request.session.get("admin"))


# 只读的表：认证/计费/审计/日志类，改了会出乱子，运营只看。
READONLY = [
    models.User,
    models.SmsCode,
    models.ConsentLog,
    models.Membership,
    models.AiLog,
    models.NotifyLog,
    models.ShareExport,
    models.Message,
    models.KbCitation,
    models.Conversation,
    models.PreConsult,
    models.ConsultOrder,
    models.PatientProfile,
    models.AgentMemory,
    models.HealthRecord,
    models.ReportDoc,
    models.TimelineEvent,
    models.Reminder,
]

# 可写的表：运营维护的内容与规则。persona 是重点——自定义角色审核就靠改它的
# review_status（pending → approved / rejected），这是后台唯一卡业务的写操作。
EDITABLE = [
    models.Persona,
    models.Doctor,
    models.KbEntry,
    models.Department,
    models.DepartmentSymptom,
    models.RedFlagRule,
]

# 列表页默认展示的列太宽会看不出东西，给几张宽表收窄一下。
NAME_COLUMNS = {
    models.User: [models.User.phone_masked, models.User.status, models.User.created_at],
    models.Persona: [
        models.Persona.name,
        models.Persona.is_preset,
        models.Persona.review_status,
        models.Persona.created_at,
    ],
    models.Doctor: [
        models.Doctor.name,
        models.Doctor.title,
        models.Doctor.department_code,
        models.Doctor.online_status,
        models.Doctor.price_cents,
    ],
    models.KbEntry: [
        models.KbEntry.title,
        models.KbEntry.department_code,
        models.KbEntry.enabled,
        models.KbEntry.reviewed_at,
    ],
    models.ConsultOrder: [
        models.ConsultOrder.order_no,
        models.ConsultOrder.status,
        models.ConsultOrder.price_cents,
        models.ConsultOrder.paid_at,
    ],
    models.Message: [models.Message.role, models.Message.created_at],
}


def _make_view(model, *, readonly: bool):
    attrs: dict = {
        "can_create": not readonly,
        "can_edit": not readonly,
        "can_delete": not readonly,
    }
    columns = NAME_COLUMNS.get(model)
    if columns is not None:
        attrs["column_list"] = columns
    # model 必须作为**类关键字参数**传给 __init_subclass__（sqladmin 0.32 在那儿收），
    # 放进 class dict 里它收不到，实例化时才报 "no attribute pk_columns"。
    return type(f"{model.__name__}Admin", (ModelView,), attrs, model=model)


def mount_admin(app) -> Admin:
    """把后台挂到 app 上。create_app 里调用一次。"""
    settings = get_settings()
    admin = Admin(
        app,
        get_engine(),
        authentication_backend=AdminAuth(secret_key=settings.jwt_secret),
        title="明路运营后台",
        base_url="/admin",
    )
    for model in READONLY:
        admin.add_view(_make_view(model, readonly=True))
    for model in EDITABLE:
        admin.add_view(_make_view(model, readonly=False))
    return admin
