# android（uni-app x 蒸汽模式）

明路 App 端工程。蒸汽模式 = vapor：无 VDOM、直接映射原生渲染。**主端是 App**；小程序端也能编译
能跑（HBuilderX 运行到微信小程序模拟器），但只做过冒烟，未逐页回归。

## 目录

```
types/api.ts        由 ../contracts/openapi.yaml 生成，勿手改
api/http.ts         token 注入 + 错误归一（页面不直接调 uni.request）
api/chat.ts         SSE 流式对话（POST + enableChunked + onChunkReceived）
api/index.ts        28 个接口的清单，与 openapi.yaml 一一对应
stores/auth.ts      登录态（模块级 reactive 单例）
stores/session.ts   当前就诊人 / 角色 / 对话
utils/err.ts        错误对象格式化
utils/apierr.ts     把 ApiFailure 变成能展示的中文
utils/blank.ts      契约可选字段的判空（hasText/hasList/hasNum，字段可能是 null）
pages/              18 个页面，见 pages.json
App.uvue            全局设计语言（见下）
```

分层：`pages/` → `stores/` → `api/` → `utils/`。页面不直接调 `uni.request`，`utils` 不依赖 `stores`。

## 改外观只改一处

`App.uvue` 的 `<style>` 是**全 App 唯一的设计语言**：配色、圆角、卡片、按钮、标签、输入框、
底部标签栏全在里面，与设计稿
`.hbuilderx/uni-agent/design/minglu-miniprogram-ui/assets/prototype.css` 对齐。

页面的 `<style>` **只放这个页面特有的布局**（内边距、宽度、排列方式）。
要改全局长相就去 `App.uvue`，不要在页面里另起一套——那会出现「改了首页没改列表页」。
页面里故意覆盖全局的只有两处，都带注释说明原因：`chat` 的 `input`/`btn` 定宽、
`reminder` 的 `input` 下间距。

**底部标签栏是唯一一处「结构重复、样式不重复」**：`.tabbar`/`.tab`/`.tabCenter` 的 CSS 只在
`App.uvue` 一份，但那 20 行标签栏**结构**在 5 个一级页面（home / doctors / chat / health /
person）各写了一遍。改标签栏长相改 `App.uvue`；**改结构（加一项、换图标、换文案）要改 5 个页面**，
漏一个就会出现「某一页标签栏和别页不一样」。每页当前项不挂 `@tap`、图标文字用 `*On` 变体表示选中
——蒸汽模式动态 `:class` 不重算（`CLAUDE.md` 规则 5），所以选中态不能靠一个变量推。跳转统一
`uni.reLaunch`：标签页是平级的，`navigateTo` 会把页面栈越堆越高，返回键要按好几次。

配色是改版原型的「自然绿」主题，**亮暗两套都全**：主色 `--c-brand`、薄荷 `--c-mint`、
页面底 `--c-page`、正文 `--c-text`、警示 `--c-danger`。改配色只改 `App.uvue` 顶部的主题变量表
（亮暗各一套，靠 `@media (prefers-color-scheme)` 自动切，系统夜间模式变化时页面不重启即时刷新）。

**明亮/黑暗/跟随系统三选一已经做了**（`我的 → 外观`）。做成了原型 `.theme-options` 的按钮组版本：
三个等宽按钮无缝拼成一个两端圆角的胶囊，选中段用主色填充（`App.uvue` 的 `.segBar`/`.seg`/`.segOn`）。
选中态用 `v-if` 换一整个元素而不是动态 `:class`（规则 5），和找医生页科室胶囊 `.chip`/`.chipOn`
同一套写法；**段里不写对号**——对号会多占一行把选中段撑高，而填充色本身就是更强的选中信号。
胶囊圆角只写在 `.segBar` 上，靠 `overflow: hidden` 把子元素方角裁掉，省掉 `:last-child`。
用的是 `uni.setAppTheme({ theme })`，**只有 App 有**——小程序和 web 只能跟随宿主，所以那一节在别端
整段不渲染（`#ifdef APP`）。选择存 `uni.setStorageSync('app_theme')`，`App.uvue` 的 `onLaunch`
重新应用，杀进程重启不丢。真机验证过：系统暗色时选「明亮」App 当场切亮、填充段跟着走、重启后仍保持。

原型里另外两套配色（海盐蓝/暖阳杏）**没做**。蒸汽模式下运行时换配色没有干净方案：
CSS 变量定义在 `page` 上是静态的，`@media` 只有亮/暗两态，而动态切 `:class` 正是
`CLAUDE.md` 规则 5 禁止的（不重算）。`:style` 动态绑定**已验证会生效**（实测 `:style` 绑
`background-color` 能跟着数据逐项换色），所以卡住的只是「没有第三态可切」，不是绑定手段。

**改主题变量的值、或给某个 surface 配新文字色之前先跑对比度检查**（亮暗两套一起验）：

```bash
python contrast.py
```

首页焦点卡的描述文字曾经是 `rgba(255,255,255,.82)`，叠在 `#24695B` 上只有 4.96:1；
改版原型的渐变方案更低，`#d4e8df` 落在亮端 `#337d6c` 上只有 3.82:1——小字号确实费眼。
现在是纯白文字 + 更深的 `#1E574B` 卡底，真机取样实测 8.24:1（WCAG AAA）。
往亮底上放浅色字是这里最容易返的错。脚本现在直接从 `App.uvue` 读亮暗两套变量值，
改了配色再跑就会反映出来；亮色主题里有 6 项**改动之前就不达标**的组合（占位符灰、id 弱字等），
已在脚本的 `ACCEPTED` 表里注明原因，新引入的不达标会让脚本非零退出。

## 跑起来

1. 后端：`cd services/api && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. `api/http.ts` 顶部 `BASE_URL` 填电脑局域网 IP（`127.0.0.1` 在手机上指向手机自己）
3. 真机一条命令（设备序列号用 `cli.exe devices list` 查）：

```bash
D:/Dev/HBuilderX/cli.exe launch app-android \
  --project android \
  --deviceId <序列号> --playground standard
```

本项目没有 UTS 原生插件，**标准基座就够**；接了 `minglu-alarm` 闹钟插件后才必须
`--playground custom`。

⚠️ `--project` 用 HBuilderX 里的项目名 `android`，**别传绝对路径**。传路径匹配不上已导入的
项目，会报「项目类型为Web，暂不支持」——项目没坏，只是没认出来。项目名用
`cli.exe project list` 查。

> ⚠️ **不要用 `timeout` 包这条命令**——调试连接一断应用就"冻"住，极易误判成代码 bug。

## 改接口

见 `../docs/agent/contract-workflow.md`（契约驱动五步）。简版：改 yaml → 重新生成
`types/api.ts` → 改 `api/index.ts` 对应行。

## 蒸汽模式的硬约束

**不在这里重复了**，统一收在 `../docs/agent/vapor-mode.md`（含编译期/运行期/调试三类和自查清单）。
在 `android/` 里干活前读它，尤其是这三条最贵的：

- 模板事件处理器里**不能写赋值表达式**（`@tap="x = y"` 静默失效）
- 选中态用 `v-if` 渲染标记，**不用动态 `:class`**（不重算）
- `ref` 对象**整体替换**，不改内部属性

## 平台相关的几件事

- 页面 `<script setup lang="ts">` 里的 `onLoad` / `onPageShow` / `onUnload` 直接可用，不需要 import
- 蒸汽模式下页面 script 编译为 **js**（不是 kotlin），uts 文件走 uts2js，所以 `.ts` 逻辑层随便写
- 只支持 `<script setup>` + 组合式 API，没有选项式 API
- `uni.request` 的 `data` 在 app-android 上只收 `UTSJSONObject` / `string` → 统一 `JSON.stringify`
- 流式响应走 `enableChunked: true` + `task.onChunkReceived`，回调里 `data` 是 `ArrayBuffer`（没有 TextDecoder，自己解 UTF-8）
- `uni.getStorageSync` 取不到时返回**空字符串**，不是 `null`
- 条件编译用 `#ifdef APP`，**不要用 `#ifndef H5` 代替**（会把小程序也圈进去，本项目已踩过）
- 图标字体的 `@font-face` **两端不能通用，必须条件编译**（在 `App.uvue`）：微信小程序的 wxss
  不加载包内本地字体文件（只认 https 和 base64），App 蒸汽模式反过来不能吃 data URI 字体
  （直接崩）。所以 `#ifdef MP` 用 base64、`#ifdef APP || H5` 引 `/static/font/*.ttf`。
  base64 那串由 `scripts/build-iconfont.py` 重新生成，别手改。详见 `../docs/agent/vapor-mode.md` §14
- 服务端已按句缓冲，客户端收到 `delta` 即渲染，不需要自己拼句
- **`uni.uploadFile` 要自己带 token**：它不吃 `uni.request` 那套全局 header，漏了就是 401
  （服务端只回裸 401，前端看不出是没带头）。用 `api/http.ts` 导出的 `uploadAuthHeader()`，
  报告上传和头像上传都走它
- **小程序端不提供「健康」和「提醒」**：点了弹「去 App」不跳转。拦截写在 `utils/apponly.ts`，
  由 5 个一级页面的 `go` / `switchTab` 统一调用——入口有六七处（标签栏、首页宫格、「我的」列表、
  健康页里的用药提醒），逐个套 `#ifdef` 必漏。App/H5 上这个函数恒返回 false
- `manifest.json` 的 `mp-weixin.setting.lazyCodeLoading` 是 `requiredComponents`（组件按需注入），
  需要基础库 2.11.1+
- **`.ts` 文件里能用 `// #ifdef`**（官方 `docs/compiler/platform.md` 的支持文件清单里列了 `.ts`），
  `utils/apponly.ts` 就靠它把小程序专属代码从 App 包里剔掉
- **选图片走 `utils/pickimage.ts`**：`uni.chooseImage` 的 `sourceType` 必须只给 `['album']`、
  `albumMode` 必须 `'system'`。默认值会先弹 uni 自己的动作面板，实测点了「从相册选择」面板会关、
  相册永远不打开，`success`/`fail` 都不回来。详见 `../docs/agent/vapor-mode.md` §9d
- **角色相关功能在对话页里，没有独立的角色页**：切角色、创建自定义角色全在
  `pages/chat/chat.uvue` 的弹层里。头像分两类：预设四张是打包图 `static/avatars/*.jpg`，
  由服务端按角色名固定下发（`modules/persona.py:PRESET_AVATARS`），用户没有入口改；
  自定义角色走上传，跟人设同一个审核对象，通过后才给 URL。本地 `utils/personaAvatar.ts`
  只做「服务端没给 URL 时按名字兜底」，不再存用户自选颜色/字（那套 2026-10-28 删掉了）。
  跟着对话走比埋在「我的」里好找——要改回去就新建一个页面把弹层内容搬过去
