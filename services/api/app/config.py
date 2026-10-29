"""配置。全部走环境变量，不引入 pydantic-settings。

架构约定：模型/OCR/ASR 只允许境内 base_url，白名单在 platform/llm.py 硬编码。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# 服务根目录 = services/api（本文件在 app/config.py，parents[1] 即 services/api）
SERVICE_ROOT = Path(__file__).resolve().parents[1]

# 境内模型服务商白名单（架构 §8.3：模型不出境是硬约束）。
# 加新厂商必须走评审，不允许运行时从环境变量注入任意 URL。
ALLOWED_LLM_BASE_URLS = frozenset(
    {
        "https://ark.cn-beijing.volces.com",
        "https://dashscope.aliyuncs.com",
        "https://api.baichuan-ai.com",
        "https://api.deepseek.com",
        "https://api.moonshot.cn",
        "https://open.bigmodel.cn",
    }
)


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.env = os.getenv("MINGLU_ENV", "dev")
        self.debug = self.env == "dev"

        self.database_url = os.getenv(
            "DATABASE_URL", f"sqlite:///{(SERVICE_ROOT / 'minglu.db').as_posix()}"
        )
        self.jwt_secret = os.getenv("JWT_SECRET", "dev-only-insecure-secret")
        self.token_ttl = int(os.getenv("TOKEN_TTL", str(7 * 24 * 3600)))

        # 验证码回显：仅 dev。开了会把验证码写进服务端日志，生产必须关闭。
        self.sms_echo = _flag("SMS_ECHO", self.debug)
        # 沙箱支付：本接口直接置 paid，跑通下游。生产必须关闭。
        self.payment_mock = _flag("PAYMENT_MOCK", self.debug)

        self.llm_base_url = os.getenv("LLM_BASE_URL", "")
        self.llm_api_key = os.getenv("LLM_API_KEY", "")
        self.llm_model = os.getenv("LLM_MODEL", "")

        self.storage_dir = Path(os.getenv("STORAGE_DIR", SERVICE_ROOT / "var" / "storage"))
        self.upload_max_bytes = int(os.getenv("UPLOAD_MAX_BYTES", str(5 * 1024 * 1024)))
        self.export_ttl_days = int(os.getenv("EXPORT_TTL_DAYS", "7"))
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

        self._guard_production()

    def _guard_production(self) -> None:
        """非 dev 环境禁止沿用默认密钥。

        守卫放这里而不是 docker-compose 的 `${JWT_SECRET:?}`：那个写法会让
        `docker compose ps`、`down` 全都因为缺变量而失败， stack 就没法运维了。
        """
        # 只对 prod 收紧。test 环境 conftest 会开 SMS_ECHO 与 PAYMENT_MOCK，
        # 不该被这里挡住。
        if self.env != "prod":
            return
        problems = []
        if self.jwt_secret == "dev-only-insecure-secret":
            problems.append("JWT_SECRET 还是默认值，先设一个随机值")
        if self.payment_mock:
            problems.append("PAYMENT_MOCK 还开着，生产必须关闭")
        if self.sms_echo:
            problems.append("SMS_ECHO 还开着，生产必须关闭")
        if problems:
            raise RuntimeError(
                f"MINGLU_ENV={self.env} 启动自检失败：\n  - " + "\n  - ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
