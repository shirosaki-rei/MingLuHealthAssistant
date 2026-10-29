"""统一错误体与 request_id。

契约约定：错误响应恒为 { code, message, request_id }，code 是业务码不是 HTTP 状态；
成功响应的 request_id 走 X-Request-Id 响应头。
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("minglu")

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class Code:
    """与 contracts/openapi.yaml 的 ErrorCode 枚举一一对应，改契约就要改这里。"""

    UNAUTHORIZED = "UNAUTHORIZED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    SMS_RATE_LIMITED = "SMS_RATE_LIMITED"
    SMS_CODE_INVALID = "SMS_CODE_INVALID"
    SMS_CODE_EXPIRED = "SMS_CODE_EXPIRED"
    PERSONA_QUOTA_EXCEEDED = "PERSONA_QUOTA_EXCEEDED"
    PERSONA_FORBIDDEN_WORD = "PERSONA_FORBIDDEN_WORD"
    PATIENT_LIMIT_EXCEEDED = "PATIENT_LIMIT_EXCEEDED"
    CHAT_DAILY_LIMIT_EXCEEDED = "CHAT_DAILY_LIMIT_EXCEEDED"
    REMINDER_LIMIT_EXCEEDED = "REMINDER_LIMIT_EXCEEDED"
    REPORT_MONTHLY_LIMIT_EXCEEDED = "REPORT_MONTHLY_LIMIT_EXCEEDED"
    PRECONSULT_NOT_ACTIVE = "PRECONSULT_NOT_ACTIVE"
    PRECONSULT_ALREADY_DONE = "PRECONSULT_ALREADY_DONE"
    ORDER_NOT_PAYABLE = "ORDER_NOT_PAYABLE"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    DOCTOR_UNAVAILABLE = "DOCTOR_UNAVAILABLE"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    UPLOAD_BAD_TYPE = "UPLOAD_BAD_TYPE"
    REDFLAG_TRIGGERED = "REDFLAG_TRIGGERED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"


class ApiError(Exception):
    """业务错误。http 默认 400；同一 code 在不同场景可以是不同 http 状态。"""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details


def not_found(what: str = "资源") -> ApiError:
    return ApiError(Code.NOT_FOUND, f"{what}不存在或无权访问", http_status=404)


def unauthorized(message: str = "请先登录") -> ApiError:
    return ApiError(Code.UNAUTHORIZED, message, http_status=401)


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id_var.get(),
    }
    if details:
        payload["details"] = details
    return payload


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            _body(exc.code, exc.message, exc.details),
            status_code=exc.http_status,
            headers={"X-Request-Id": request_id_var.get()},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # details 里的 input 可能是文件对象/大对象，只留字段路径与原因
        fields = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "reason": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            _body(Code.VALIDATION_ERROR, "请求参数不正确", {"fields": fields}),
            status_code=422,
            headers={"X-Request-Id": request_id_var.get()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = Code.UNAUTHORIZED if exc.status_code == 401 else Code.NOT_FOUND if exc.status_code == 404 else Code.VALIDATION_ERROR
        return JSONResponse(
            _body(code, str(exc.detail)),
            status_code=exc.status_code,
            headers={"X-Request-Id": request_id_var.get()},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error rid=%s path=%s", request_id_var.get(), request.url.path)
        return JSONResponse(
            _body("INTERNAL_ERROR", "服务开小差了，请稍后再试"),
            status_code=500,
            headers={"X-Request-Id": request_id_var.get()},
        )


def ai_log_row(**kwargs: Any) -> str:
    """仅用于调试时把一行 ai_log 打成 JSON。"""
    return json.dumps(kwargs, ensure_ascii=False, default=str)
