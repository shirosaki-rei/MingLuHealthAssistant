"""FastAPI 应用装配。

启动自检：遍历所有路由，任何 /v1/* 路由若没有声明 action（`__minglu_action__`）
或 action 不在已知集合里，直接抛 RuntimeError —— 进程起不来。
这就是架构 §4.4 要求的「未声明直接 500」，把漏配挡在 CI 而不是线上。
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from .config import get_settings
from .database import Base, get_engine, get_sessionmaker
from .deps import known_action
from .errors import register_error_handlers, request_id_var
from .modules import (
    chat,
    consult,
    entitlement,
    health,
    identity,
    match,
    memory,
    persona,
    preconsult,
    profile,
    reminder,
)
from .seed import seed

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("minglu")


def create_app(*, run_seed: bool = True) -> FastAPI:
    app = FastAPI(
        title="明路 API",
        version="1.4.0",
        description="AI 健康助手 + 医生咨询撮合（非诊疗）。契约见 contracts/openapi.yaml",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 小程序与 H5 没有 Origin 限制需求；生产收窄到自己的域名
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        """request_id 贯通全链路，与 ai_log 对齐。"""
        token = request_id_var.set(request.headers.get("X-Request-Id") or uuid.uuid4().hex)
        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id_var.get()
            return response
        finally:
            request_id_var.reset(token)

    register_error_handlers(app)

    for module in (
        identity,
        profile,
        persona,
        chat,
        preconsult,
        match,
        consult,
        reminder,
        health,
        memory,
        entitlement,
    ):
        app.include_router(module.router)

    _assert_all_actions_declared(app)

    @app.on_event("startup")
    def _startup() -> None:
        # 架构 §7.3：禁止在发布脚本里跑 DDL。dev 用 create_all 省一步，
        # 非 dev 必须 `alembic upgrade head`（docker-compose 里由 migrate 服务先跑）。
        if get_settings().env == "dev":
            Base.metadata.create_all(get_engine())
        if run_seed:
            seed(get_sessionmaker()())

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # 上传的文件（报告图片、头像）按相对路径对外服务。
    # storage.public_url 一直在返回 /static/... 的 URL，但没有这条挂载，那些 URL 全是 404。
    _static = get_settings().storage_dir
    _static.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

    return app


def _walk_routes(routes) -> list:
    """摊平路由。

    FastAPI 0.159 的 include_router 不把子路由铺进 app.routes，而是留一个
    _IncludedRouter 包装器（带 original_router）。两种形态都要走，否则启动自检
    会一条路由都没检查到、假通过。
    """
    found: list = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            found.extend(_walk_routes(inner.routes))
        elif getattr(route, "path", None):
            found.append(route)
    return found


def _assert_all_actions_declared(app: FastAPI) -> None:
    """架构 §4.4：所有对外接口必须显式声明 action。漏配或拼错 → 启动失败。"""
    missing: list[str] = []
    unknown: list[str] = []
    for route in _walk_routes(app.routes):
        path = route.path
        if not path.startswith("/v1/"):
            continue
        actions = {
            getattr(dep.call, "__minglu_action__", None)
            for dep in getattr(getattr(route, "dependant", None), "dependencies", [])
            if hasattr(dep, "call")
        }
        actions.discard(None)
        method = sorted(route.methods)[0] if getattr(route, "methods", None) else "?"
        if not actions:
            missing.append(f"{method} {path}")
            continue
        for action in actions:
            if not known_action(action):
                unknown.append(f"{method} {path}: {action}")
    if missing or unknown:
        raise RuntimeError(
            "路由 action 自检失败。\n"
            f"  未声明 action：{missing}\n"
            f"  未知 action（拼错或未注册）：{unknown}\n"
            "修法：给路由加 Depends(require_action('xxx'))，"
            "就医路径动作加进 entitlement.MEDICAL_ACTIONS，其余用 register_checks 注册。"
        )


app = create_app()
