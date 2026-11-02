# 明路

AI 私人健康助手 + 医生健康咨询撮合。**非诊疗产品，不做诊断结论。**

一端代码出三端：安卓 App（主端）、微信小程序、H5。后端 FastAPI 模块化单体，
前后端以 `contracts/openapi.yaml` 为唯一接口真相源。

---

## 仓库里有三个工程，别动错

| 目录 | 是什么 | 什么时候动 |
|---|---|---|
| `android/` | **正式客户端**，uni-app x 蒸汽模式，19 个页面，契约 40 个 operation 全有入口 | 日常前端开发都在这 |
| `poc/` | PoC 验证工程 + 零依赖桩服务 | 不前沿了，别在里面写正式代码 |
| `services/api/` | FastAPI 后端，13 模块 | 后端开发 |

---

## 快速开始

### 后端（默认 sqlite，不需要 docker）

```bash
cd services/api
pip install -r requirements.txt
python -m pytest                  # 36 项，含 12 条付费墙契约测试
PUBLIC_BASE_URL=http://<电脑局域网IP>:8000 python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 交互文档 http://127.0.0.1:8000/docs
```

首次启动自动建表并写种子数据（科室映射、演示医生、红旗规则、知识词条、预设角色）。
dev 下 `SMS_ECHO=1` 会把验证码打进服务端日志。

### 真机跑 App

1. `android/api/http.ts` 顶部 `BASE_URL` 填电脑局域网 IP（`127.0.0.1` 在手机上指向手机自己）
2. `PUBLIC_BASE_URL` 同上——**它决定 `avatar_url` / 报告图片 URL 拼成什么**，不填默认
   `http://127.0.0.1:8000`，真机上 `<image>` 加载失败（不报错，就是一个空白圆圈）
3. 设备序列号用 `cli.exe devices list` 查：

```bash
D:/Dev/HBuilderX/cli.exe launch app-android \
  --project android \
  --deviceId <序列号> --playground standard
```

`--project` 传 HBuilderX 里的项目名 `android`，**别传绝对路径**。
本项目没有 UTS 原生插件，标准基座就够。

> ⚠️ **不要用 `timeout` 包这条命令**——调试连接一断应用就"冻"住，极易误判成代码 bug。

### 或者容器化全套（PostgreSQL）

```bash
cp .env.example .env   # 填 JWT_SECRET、POSTGRES_PASSWORD 等
docker compose up --build
```

迁移走独立的 `migrate` 服务（`alembic upgrade head`），api 容器自己不跑 DDL。

---

## 三份真相源

| 文件 | 管什么 |
|---|---|
| `docs/明路_PRD_v3.1.md` | 需求 |
| `docs/明路_架构设计_v1.0.md` | 架构 |
| `contracts/openapi.yaml` | 接口（当前 1.4.0，35 path / 40 operation） |

**改接口必须先改 yaml**，再重新生成两边类型（前端 `android/types/api.ts` 是生成产物，
不手改）。完整五步见 `docs/agent/contract-workflow.md`。

## 测试与 CI

```bash
cd services/api && python -m pytest     # safety / contract / journey / architecture 四组
```

`.github/workflows/ci.yml` 跑三件事：`openapi.yaml` 能解析 + redocly lint、
`requirements.txt` 真的能装上、后端测试。

---

## 三条红线

1. **就医路径不可付费墙**：action 白名单在 `services/api/app/modules/entitlement.py`，
   服务端永远放行，不读会员状态。启动自检遍历所有 `/v1/*` 路由，漏声明 action 直接起不来。
2. **红旗不依赖模型**：`safety.py` 是纯规则，不 import `platform.llm`，模型全挂时照样生效。
3. **模型不出境**：`config.ALLOWED_LLM_BASE_URLS` 是硬编码白名单，运行期注入境外地址会被拒并降级。

蒸汽模式还有一批前端硬约束（模板里禁赋值表达式、选中态用 `v-if`、`ref` 整体替换、
不支持后代选择器……），动手改 `android/` 前先读 `docs/agent/vapor-mode.md`。

---

## 当前状态

技术侧约 85%，剩下的主要不是代码，是合规落地、内容和资质。
未接 `minglu-alarm` 闹钟插件（提醒页只做 CRUD）；小程序端只做过冒烟未逐页回归；
`BASE_URL` 是写死的局域网 IP，上线前必须改。逐项见
[`docs/上线前缺口清单.md`](docs/上线前缺口清单.md)。

---
