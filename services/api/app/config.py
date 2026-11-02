"""配置。全部走环境变量，不引入 pydantic-settings。

架构约定：模型/OCR/ASR 只允许境内 base_url，白名单在下面硬编码。

`.env` 由本模块负责灌进环境变量（见 `_load_dotenv`）：直跑 uvicorn 不读 `.env`，
只有 docker-compose 读，不补这一步就会出现"改了 .env 没反应"。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# 服务根目录 = services/api（本文件在 app/config.py，parents[1] 即 services/api）
SERVICE_ROOT = Path(__file__).resolve().parents[1]
# 仓库根 = .env 所在处（services/api/app/config.py 往上数四级）
# 容器里 app/ 被拷到 /srv/app，只有三级可上溯，此时降级为 SERVICE_ROOT：
# 容器内配置全由 compose 注入，不依赖读 .env（`_load_dotenv` 找不到文件会直接返回）。
_here = Path(__file__).resolve()
PROJECT_ROOT = _here.parents[3] if len(_here.parents) > 3 else SERVICE_ROOT


def _load_dotenv() -> None:
    """把项目根的 `.env` 灌进 `os.environ`，**不覆盖已设置的变量**。

    已存在的优先，这样 `LLM_BASE_URL=xxx python -m uvicorn ...` 临时覆盖仍然有效，
    也不用引入 python-dotenv 依赖（就几行，解析规则：忽略空行与 # 注释，去引号）。
    """
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 空值一律跳过：.env 从 .env.example 复制来时满眼 `JWT_SECRET=` 这种空占位，
        # 当成"已设置"灌进去会把 config 里的默认值覆盖成空串（踩过：JWT 直接抛
        # "HMAC key must not be empty"）。想让某个项为空就别写这一行。
        if key and value and key not in os.environ:
            os.environ[key] = value

# 境内模型服务商白名单（架构 §8.3：模型不出境是硬约束）。
# 加新厂商必须走评审，不允许运行时从环境变量注入任意 URL。
# 境内模型服务商的 OpenAI 兼容 base_url（**完整路径前缀，不是裸域名**）。
# 代码按 `base_url + "/chat/completions"` 拼端点，所以这里必须填到厂商文档里
# base_url 的那一级：智谱是 /api/paas/v4、方舟是 /api/v3、百炼是 /compatible-mode/v1、
# moonshot 与百川是 /v1，只有 deepseek 是裸域名。填裸域名会 404。
# 数据不出境是硬约束：运行时从环境变量注入名单外的 URL 会被直接拒绝。
ALLOWED_LLM_BASE_URLS = frozenset(
    {
        "https://ark.cn-beijing.volces.com/api/v3",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://api.baichuan-ai.com/v1",
        "https://api.deepseek.com",
        "https://api.moonshot.cn/v1",
        "https://open.bigmodel.cn/api/paas/v4",
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
        # 思考强度，只对"始终思考"的模型有意义（glm-5.x / deepseek-r1）。
        # 空 = 不传这个参数（非思考模型传了可能被拒，所以默认空）。
        # 实测 glm-5.3-flash：默认档 12s、输出保守像客服；low 档 reasoning 归零、
        # 延迟相近但人设和共情都在——健康陪伴场景用 low，深度推理任务才需要 high/max。
        self.llm_reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "")

        self.storage_dir = Path(os.getenv("STORAGE_DIR", SERVICE_ROOT / "var" / "storage"))
        self.upload_max_bytes = int(os.getenv("UPLOAD_MAX_BYTES", str(5 * 1024 * 1024)))
        self.export_ttl_days = int(os.getenv("EXPORT_TTL_DAYS", "7"))
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

        # 运营后台（/admin）的独立账号。不复用 C 端登录体系。
        # dev 下留个默认值省事，prod 必须在自检里换掉（见 _guard_production）。
        self.admin_username = os.getenv("ADMIN_USERNAME", "admin")
        self.admin_password = os.getenv("ADMIN_PASSWORD", "dev-only-admin-password")

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
        if self.admin_password == "dev-only-admin-password":
            problems.append("ADMIN_PASSWORD 还是默认值，运营后台会被人裸登")
        if self.payment_mock:
            problems.append("PAYMENT_MOCK 还开着，生产必须关闭")
        if self.sms_echo:
            problems.append("SMS_ECHO 还开着，生产必须关闭")
        if problems:
            raise RuntimeError(
                f"MINGLU_ENV={self.env} 启动自检失败：\n  - " + "\n  - ".join(problems)
            )


# 导入即加载：settings 是 lru_cache 的单例，第一次 get_settings() 之前必须灌完 .env
_load_dotenv()


@lru_cache
def get_settings() -> Settings:
    return Settings()
