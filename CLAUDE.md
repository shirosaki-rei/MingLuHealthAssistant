# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这是什么

明路 —— AI 健康助手 + 医生咨询撮合（**非诊疗，不做诊断结论**）。三端同源：安卓 App（uni-app x 蒸汽模式）、微信小程序、H5。

需求真相源 `docs/明路_PRD_v3.1.md`，架构真相源 `docs/明路_架构设计_v1.0.md`，接口真相源 `contracts/openapi.yaml`。

## 首次运行

```bash
# 后端（默认 sqlite，不需要 docker）。用项目的 .venv：main.py 挂了运营后台（sqladmin），
# 系统 python 没这个依赖会 import 失败
cd services/api && .venv\Scripts\python.exe -m pytest && PUBLIC_BASE_URL=http://<电脑局域网IP>:8000 .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端真机
D:/Dev/HBuilderX/cli.exe launch app-android --project "F:/hongmeng/pj1/android" --deviceId <序列号> --playground standard

# 或容器化全套
cp .env.example .env && docker compose up --build
```

`android/api/http.ts` 顶部的 `BASE_URL` 要填电脑局域网 IP（`127.0.0.1` 在手机上指向手机自己）。
后端的 `PUBLIC_BASE_URL` 同理，且**它决定 `avatar_url` / 报告图片 URL 拼成什么**——不填默认
`http://127.0.0.1:8000`，真机上 `<image>` 会加载失败（不报错，就是一个空白圆圈）。

## 仓库里有四个工程，别动错

| 目录 | 是什么 | 什么时候动 |
|---|---|---|
| `android/` | **正式客户端**，蒸汽模式，20 页面，契约 42 operation 全有入口 | 日常前端开发都在这 |
| `uni-appX/` | 旧 PoC 工程。`utils/stream.ts`（三端流式适配层）和 `uni_modules/minglu-alarm`（精确闹钟 UTS 插件）**只在这里** | 移植流式/闹钟能力时才动 |
| `poc/` | PoC 验证工程 + 零依赖桩服务 | 不前沿了，别在里面写正式代码 |
| `services/api/` | FastAPI 后端，模块化单体，13 模块 | 后端开发 |

`uniapp-x-src/` 是 DCloud 官方源码参考（16MB，**自带 .git 的嵌套仓库**，已 ignore），只读查证用。

---

## 全局硬约束（不可违反）

1. **改接口必须先改 `contracts/openapi.yaml`**，再生成两边类型，不许跳过。`（源：前后端分两边开发，曾因类型理解不一致返工。用：任何接口变更。删：永不）`
2. **不手改 `android/types/api.ts`**，它是生成产物。`（源：下次生成就被覆盖。用：每次重新生成后。删：永不）`
3. **错误按 `ErrorCode` 枚举分支，不匹配 `message`**——文案会改。`（源：契约 README。用：任何错误处理。删：永不）`
4. **蒸汽模式：模板事件处理器里禁写赋值表达式**（`@tap="x = y"` 静默失效），必须函数调用。`（源：真机实测，5 处中招。用：写任何事件绑定。删：DCloud 支持后）`
5. **蒸汽模式：选中态用 `v-if` 渲染标记，不用动态 `:class`**（首次渲染对，状态变了不重算）。`（源：真机实测。用：任何选中/高亮态。删：DCloud 修复后）`
6. **蒸汽模式：`ref` 对象整体替换，不改内部属性**（`form.value = {...form.value, x: y}`）。`（源：PoC 早有同样注释又踩一次。用：任何 ref 对象状态修改。删：蒸汽模式支持深层响应式）`
7. **蒸汽模式：不支持后代选择器**（`.a .b` 静默丢弃），只用单一 class。`（源：曾致按钮不显示。用：写任何样式。删：DCloud 支持后）`
8. **蒸汽模式：`display` 只有 `flex`/`none`**；想让 text 独占一行就删掉 `display: block`。`（源：官方 css 文档。用：写任何样式。删：DCloud 补充支持）`
9. **蒸汽模式：契约类型必须生成成 `.ts` 不是 `.d.ts`**（否则 `Expected '{', got 'interface'`）。`（源：踩过。用：每次生成类型。删：uts 管线支持 .d.ts）`
10. **就医路径 action 服务端永远放行，不读会员状态**。可收费的只有消耗型动作。`（源：PRD §1.4 红线。用：任何接口、任何改 entitlement。删：永不）`
11. **模块间只能通过 service 层调用**，禁止跨模块直接查表；`safety` 不得依赖 `llm_gateway`。`（源：架构 §2.2，测试静态断言。用：加模块、加接口。删：永不）`
12. **非 dev 环境必须 `alembic upgrade head`**，`create_all()` 只在 dev 跑（禁止发布脚本跑 DDL）。`（源：migrations 曾全是空的。用：部署、改模型。删：永不）`
13. **别用 `timeout` 包 `cli.exe launch`**——调试连接一断应用就"冻"住（不响应点击和返回键，画面停最后一张），极易误判成代码 bug。`（源：误判了四轮。用：任何 CLI 真机调试。删：永不）`
14. **金额单位是分，时间是 ISO 8601 带时区**，ID 是 UUID string。`（源：契约文件头。用：任何字段设计。删：永不）`
15. **SSE 事件名只有 `delta`/`redflag`/`done`**，一个 `delta` 至少是完整句子，客户端不自己拼句。`（源：契约描述。用：改流式链路。删：永不）`

---

## 专题文档（按需加载）

| 文件 | 什么时候读 |
|---|---|
| `docs/agent/vapor-mode.md` | 改 `android/` 任何 `.uvue`/`.ts` 前；遇到「点了没反应」「样式不更新」「按钮不显示」。含改动后自查清单 |
| `docs/agent/contract-workflow.md` | 加/改/删接口；前后端字段对不上；重新生成类型。含 SSE 与白名单两处 OpenAPI 描述不了的地方 |
| `docs/agent/backend.md` | 跑测试、起服务、改模型、动迁移、用 docker。含 autogenerate 的两个坑 |
| `docs/agent/redlines.md` | 改付费墙/会员、加模块或接口、改对话链路或提醒调度。含三道保障与依赖规则 |
| `docs/上线前缺口清单.md` | 想知道还有什么没做、技术侧完成度。第二轮复核结论在文档第零节 |
| `android/README.md` | 只在 android 目录里干活时的速查（BASE_URL、目录结构） |

`.workbuddy/memory/` 是项目决策日志，含每轮「为什么这么决定」——改架构前值得翻。

---

## 已知未完成项

- `android/` 未接 `minglu-alarm` 闹钟插件，提醒页只做 CRUD 不注册本机闹钟
- 微信小程序端已能编译运行（2026-10-26 冒烟：登录/首页/找医生/记忆页渲染正常；2026-10-27 复核：同一个 `vapor: true` 工程可直接 `cli launch mp-weixin`，不需要另建非蒸汽工程），但未逐页回归。图标字体与按钮排版存在跨端差异，已在 `App.uvue` 用条件编译处理，见 `android/README.md`。「健康」和「提醒」两块在小程序端不提供，点了弹「去 App」（`utils/apponly.ts`）
- `BASE_URL` 是写死的局域网 IP，上线前必须改，建议加构建期检查（含私网 IP 不许出包）
- 知识库仅 5 条、医生是演示数据、合规材料（隐私政策/算法备案/软著）未办
