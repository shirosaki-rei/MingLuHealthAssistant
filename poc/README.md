# 明路 PoC 工程

配套文档：`../docs/明路_PoC验证方案_v1.0.md`

## 你现在看到了 hello，这是正常的

HBuilderX 新建 uni-app x 项目后，默认就是 hello 模板，图标和名称也是 uni-app x 自带的（来自 `manifest.json` 的 `appname` 和图标配置）。

**这一步跑通本身就是一个有效结论**：HBuilderX + 手机 + 蒸汽模式的编译链路是通的。PoC 第 1 项（三端构建）的安卓端已经过了一半，剩下的是把我们的页面换进去。

---

## 这里有什么

```
poc/
├── server/stub.mjs              零依赖桩服务：SSE 流式 / 上传 / OCR
├── src/
│   ├── utils/stream.ts          三端流式适配层（PoC 核心交付物）
│   ├── pages/index/index.uvue   三端一致性验证页
│   ├── pages/stream/stream.uvue 流式对话验证页（含按句缓冲开关）
│   ├── pages-sub/camera/camera.uvue  拍照上传（故意放分包）
│   └── pages.json               页面注册 + 分包 + 预加载
├── uni_modules/minglu-alarm/    精确闹钟 UTS 骨架
└── native-reference/            Kotlin 参考实现
```

**注意：uni-app x 的页面后缀是 `.uvue`，不是 `.vue`。** 拷进去用 `.vue` 会识别不到。

---

## 第一步：看清你的工程结构

HBuilderX 里右键项目 → 「在资源管理器中打开」，看根目录：

| 你看到 | 结构类型 | 拷到哪 |
|---|---|---|
| 根目录直接有 `pages/` | 无 src（HBuilderX 默认） | 工程根目录 |
| 根目录有 `src/` | 有 src（CLI 模板） | `src/` 下 |

下面按「无 src」写，有 src 就把所有目标路径前面加 `src/`。

---

## 第二步：替换页面

1. **先备份工程原有的 `pages.json`**（改名为 `pages.json.bak`），等下要覆盖
2. 拷这三个文件，路径对应：

| 从 | 到 |
|---|---|
| `poc/src/pages/index/index.uvue` | `pages/index/index.uvue`（覆盖或新建） |
| `poc/src/pages/stream/stream.uvue` | `pages/stream/stream.uvue` |
| `poc/src/pages-sub/camera/camera.uvue` | `pages-sub/camera/camera.uvue` |
| `poc/src/utils/stream.ts` | `utils/stream.ts` |
| `poc/src/pages.json` | `pages.json`（覆盖） |

3. 覆盖 `pages.json` 后，模板自带的示例页面会从路由里移除（文件还在，不影响）
4. HBuilderX 里如果报"页面不存在"，检查 `pages.json` 里的路径是否与实际目录一致

---

## 第三步：改 IP 并自测网络

手机上 `localhost` 指向手机自己，必须用电脑的局域网 IP。

改这两个地方（三个文件顶部都有）：

- `utils/stream.ts` 里其实没有 BASE（它只接收 url）
- `pages/stream/stream.uvue` 顶部
- `pages-sub/camera/camera.uvue` 顶部

```ts
let BASE = 'http://localhost:8787'
// #ifndef H5
BASE = 'http://192.168.1.100:8787'   // ← 换成你电脑的 IP
// #endif
```

查 IP：命令行 `ipconfig`，找「IPv4 地址」。

**先做这一步再跑页面**：手机浏览器打开 `http://电脑IP:8787/health`。

- 看到 `{"ok":true,...}` → 网络通，可以往下
- 打不开 → 是网络或防火墙问题，不是代码问题。检查：同一 Wi-Fi、Windows 防火墙放行 8787、路由器没开 AP 隔离

启动桩服务：

```bash
cd poc
node server/stub.mjs
```

---

## 第四步：按五项逐条验

| # | 怎么验 | 记什么 |
|---|---|---|
| 1 | 打开首页，三端各看一遍 | 哪些组件/样式不一致，截图 |
| 2 | 进「流式对话」，点开始 | 传输方式（fetch / chunked / unsupported）、首字 ms、总耗时、帧数 |
| 3 | 「按句缓冲」开着再看一次 | 是否只在句末才出字 |
| 4 | 进分包页拍照 → 压缩 → 上传 → 识别 | 原图 KB、压缩后 KB、上传是否成功 |
| 5 | 微信开发者工具看主包体积 | 主包 KB，分包是否预加载 |

**第 2 项最关键**。如果安卓端显示 `unsupported`，说明 `uni.request` 不支持 `enableChunked`，后端要改 WebSocket 或轮询。这个结论要在 D4 拿到。

---

## 第五步：精确闹钟（D6~D8）

`uni_modules/minglu-alarm` 是骨架，未验证过编译。两个卡点：

1. **Receiver 类怎么被 Intent 引用**：`setClassName` 需要 Receiver 已进 manifest，UTS 侧可能要反射
2. **Kotlin 源码放哪**：参考 `native-reference/AlarmReceivers.kt`

顺序：先让「1 分钟后响」跑通 → 再加开机恢复 → 最后测国产 ROM。

---

## 已知问题

| 问题 | 说明 |
|---|---|
| CLI 模板拉不到 | GitHub 不通，npm 无 `create-uni-app-x`。只能用 HBuilderX 建工程 |
| 页面必须是 `.uvue` | 用 `.vue` 识别不到；两个同时存在会冲突，要删掉旧的 |
| 安卓构建依赖 HBuilderX | CI 无法覆盖安卓出包 |
| 蒸汽模式是 2026 新品 | 跑不动就在 manifest 关掉，代码不改，只降性能 |
| 请求结束后 `abort()` 会抛错 | 若在 `reset()` 里先 abort 再清 UI，会因异常中断导致清空失效。顺序要先清 UI 再 stop，且 abort 包 try/catch |
| `uni.uploadFile` 参数 | **三端统一用 `filePath`**。微信小程序只认 `filePath`，传 `files` 会报 1001 `filePath should be String`；App 与 H5 实测 `filePath` 也都可用 |
| H5 不支持 compressImage | 相机页在 H5 只验到选图 |
| `uni.chooseImage` 覆盖度 | 若 uvue 不支持，改用 `uni.chooseMedia` |

## uvue 的 CSS 限制（实测踩到的）

uvue 用的是 UCSS 子集，不是完整 CSS。写样式前先看这张表，能省很多来回：

| 写法 | 报错 | 正确做法 |
|---|---|---|
| `display: block` | `property value block is not supported for display`（只支持 `flex` / `none`） | 直接删掉。view 默认就是 flex column |
| `max-width: 78%` | `property value 78% is not supported for max-width`（只支持 number / pixel） | 用 px，如 `max-width: 300px` |
| `<scroll-view scroll-y="true">` | `Property 'scroll-y' is not supported` | 改用 `direction="vertical"` |
| `border-width: 1` | 无单位值不接受 | 用简写 `border: 1px solid #xxx` |
| `box-shadow` | 待验证（首页第 5 项探针） | 语法用 `0px 4px 12px #999999`，不用 rgba |
| `background-image: linear-gradient` | 待验证 | 同上 |

首页第 5 项现在拆成了三个独立探针（圆角 / 阴影 / 渐变），哪个不生效一眼能看出来。

## 记录表（填完就是 PoC 结论）

| 项 | 结论 | 证据 |
|---|---|---|
| 三端构建 | **通过** | 安卓 / 小程序 / H5 同一份代码产出 |
| 流式（H5） | **通过** | 逐字渲染 + 按句缓冲正常 |
| 流式（安卓） | **通过** | `enableChunked` 可用，首字正常 |
| 流式（小程序） | **通过** | 同上，需关 `urlCheck` |
| 拍照上传 | **通过** | 压缩 778KB → 260.3KB，OCR 回显正常 |
| 精确闹钟 60 秒（含息屏） | **通过** | 息屏后准时触发，需加 ROM 白名单 |
| **开机恢复** | **通过** | 自定义基座 + `MingluBootReceiver`，重启后提醒恢复 |
| 精确闹钟 8 小时 | **未实测** | 环境限制（无法保持 8 小时观测）。风险低：`setExactAndAllowWhileIdle` 本身即为穿透 Doze 设计，60 秒含息屏已验证同一路径 |
| 分包体积 | 未测 | 不影响结论，开发期在开发者工具里看即可 |
| **总判定** | **通过** | 技术选型成立，进入开发 |

### 跑通这一套的关键配置（照做可复现）

| 问题 | 解法 |
|---|---|
| 安卓请求全失败 | 换真实局域网 IP + manifest 开 `usesCleartextTraffic` |
| 小程序 `url notin domain list` | 关 `project.private.config.json` 的 `urlCheck` |
| 页面样式报错 | uvue 用 UCSS 子集，见下方限制表 |
| 闹钟收到但通知不显示 | Android 13+ 运行时通知权限 |
| 息屏后闹钟不响 | ROM 白名单（vivo：后台高耗电 + 自启动） |
| 广播发出去没反应 | 动态注册只响应隐式广播，不能用显式 Intent |
| 自定义基座点桌面图标白屏 | 它要连 HBuilderX dev server，必须从 IDE 启动 |
| Receiver 不生效 | `AndroidManifest.xml` 必须放**项目根目录** |
