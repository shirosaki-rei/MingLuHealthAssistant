# uni-app x 蒸汽模式硬约束

> **什么时候读**：改 `android/` 下任何 `.uvue`/`.ts` 之前；或遇到「点了没反应」「样式不更新」「按钮不显示」。**什么时候删**：DCloud 修了某条行为，或项目迁出蒸汽模式——每条都标了过期条件，审计时逐条核对。

分四类：**编译期报错的**、**静默失效的**、**调试方法**、**跨端差异**。第二类最贵——不报错，就是不工作。第四类只在两端都跑的时候才咬人。

---

## 一、编译期

### 1. 类型文件必须是 `.ts`，不能是 `.d.ts`

uts 管线能把 `@/types/api` 解析到 `.d.ts`，但会把它当 JavaScript 解析，直接 `Expected '{', got 'interface'` 编译失败。
做法：`npx openapi-typescript@7 ../contracts/openapi.yaml -o types/api.ts`

- **来源**：openapi-typescript 默认输出 `.d.ts`，按 `contracts/README.md` 原样执行就连不上。
- **适用**：每次重新生成契约类型时。**过期**：uts 管线支持 `.d.ts` 解析。

### 2. `display` 只支持 `flex` / `none`；`align-items` 没有 `baseline`

`block` 和 `inline-block` 都是编译错误。view/text 默认就是 flex 纵向排列，想让 text 独占一行
**把 `display: block` 删掉**。`align-items` 只认 `center`/`flex-start`/`flex-end`/`stretch`，
写 `baseline` 会打 ERROR 但编译仍显示成功——大数字配小单位想对齐就用 `flex-end`。

- **来源**：官方 `css/display.html`；本项目曾一次报 87 处。
- **适用**：写任何样式时。**过期**：DCloud 补充支持 block。

### 3. 不支持关系选择器（`.a .b`、`.a > b`）

官方 `css/common/selector.md` 明写：关系类选择器（后代/子代/兄弟/分组）在 App 平台 VDOM
模式曾被支持，**蒸汽模式暂不支持**。而且是**静默丢弃**——不报编译错误，规则就是不生效
（CLI 会打一条 `Selector ... is not supported` 的 ERROR，但编译仍显示成功，最容易漏看）。

- **来源**：`.codeRow .input { width: 400rpx }` 整个失效，导致「获取验证码按钮不显示」；
  后来写 `.chipRow > view { margin }` 又中一次。
- **适用**：写样式时。只用单一 class 或复合 class（`.btn.ghost` 这种没空格的可以）。
- **过期**：DCloud 支持关系选择器。**自查**见文末清单。

### 4. `scroll-view` 的 `scroll-x` / `scroll-y` 已废弃

用 `direction="vertical"`（默认值）。**过期**：DCloud 恢复支持。

---

## 二、运行期：不报错，就是不工作

### 5. 模板事件处理器里不能写赋值表达式

`@tap="answer = o"`、`@click="showForm = false"` 会被**静默忽略**，点了什么都没发生，必须写成函数调用。

- **来源**：预问诊单选、角色中心取消按钮、就诊人关系/性别选择器全都中过。
- **适用**：写任何事件绑定时。**过期**：uvue 模板编译器支持赋值表达式。**自查**见文末清单。

### 6. 动态 `:class` 不重算

`:class="x === y ? 'a aOn' : 'a'"` 首次渲染是对的，但状态变了样式不更新——正是官方文档「App平台是后设生效」那条约束的含义。

- **来源**：真机实测：点击有触发、状态有更新、`<text>` 有渲染，唯独边框不移动。
- **适用**：任何「选中态 / 高亮态」。**过期**：DCloud 修复动态 class 的响应式。**自查**见文末清单。
- **做法**：选中态一律用 `v-if` 渲染标记元素或直接换一整个元素，参考 `pages/patients/patients.uvue`。

### 7. `ref` 对象不要改内部属性

`form.value.xxx = y` 不触发更新，要整体替换：`form.value = { ...form.value, xxx: y }`。

- **来源**：PoC 的 `uni-appX/utils/stream.ts` 早有同样注释，但当时没看懂，正式工程又踩一次。
- **适用**：任何 `ref({...})` 对象状态的修改。**过期**：蒸汽模式支持深层响应式。**自查**见文末清单。

### 8. 原生 `input` 默认 `width: 100%`

官方 `component/input.md` 明示。和按钮同行时会把按钮挤出容器，表现是「按钮不显示」。给 input
写死宽度，别用 `flex: 1`（basis 是 auto，会吃到默认 100%）：input `width: 400rpx; flex-shrink: 0;`
+ 按钮 `width: 230rpx; flex-shrink: 0;`

- **来源**：用户报「安卓不显示获取验证码按钮，浏览器和小程序正常」。
- **适用**：input 与按钮同行的布局。**过期**：DCloud 改掉默认宽度。

### 9. `<view>` 点击用 `@tap`，可点元素优先 `<button>`

两者官方都支持（Android 3.9+），文档明确「推荐使用 tap 代替」。混用容易漏。
另外 `<text>` **不支持 `disabled`**，用 `<text class="btn" :disabled>` 每条都报 warn 且根本不灰。
**`<button>` 里不能嵌任何子元素**（`<text>`/`<view>` 都不行），只打一条 warn 且**不算编译失败**；
要「图标+文字」排一行的按钮，整行做成带背景的 `<view @tap>`（有背景才有尺寸，见下条）。

**只包一个 `<text>` 的 view 收不到点击**：`<text>` 带背景色时渲染成原生 TextView，点它不往父 view
冒泡，而父 view 没背景也没尺寸、触摸框是空的。表现是「样子都对就是点不了」，且不报错。所以胶囊、
选项这类小按钮一律用 `<button>` + `@click`，别用 view 包 text。

- **来源**：提醒页的重复胶囊、就诊人页的关系胶囊全是这个结构。底部标签栏和首页宫格能点，
  是因为那两处 view 有 `flex:1` / `width:25%`。改成 `<button>` 立刻好。
- **适用**：任何「view 包一个带背景的 text」当按钮用的地方。
- **过期**：DCloud 修掉 text 的事件冒泡（`@tap`/`@click` 混用这条是风格约定，永不删）。

### 9b. 「卡片套多层子 view + @tap」整卡点不动

卡片里有 `docTop`/`docTags`/`docBottom` 这类 flex 子 view 时，点卡片任何位置都没反应——子 view
各占各的触摸区，tap 到不了外层 `@tap`。而「图标+文字+大留白」的宫格却点得动（`home.uvue` 的
`.tile`、tabbar 的 `.tab`、`person.uvue` 的 `.rowBtn`）。小尺寸 `<button class="tag">` 也点不动。

- **来源**：找医生页 `.docCard` 点了十几次毫无反应，日志里连 `LastPageBackPress` 都没有；同一屏的 `.tile` 一碰就进。
- **适用**：任何「一张卡里套了多行结构化内容的整卡点击」。**过期**：DCloud 修掉 view 的事件冒泡。
- **做法**：别指望整卡可点。卡里放大触摸区的 `@tap` 元素（`.rowBtn` 那种一行一项），或把入口做成
  卡内某个明确动作——`.docCard` 最后是把「查看主页」改成 `<button class="docGo">`。
- **反过来说，能点的形状是**：外层 view 有明确尺寸（`width:25%` / `flex:1` / 写死宽）挂 `@tap`，
  里面**只包一个填满它的子 view**（`home.uvue` 的 `.tile > .tileBox`、`person.uvue` 外观的
  `.seg > .segIn`）。子 view 里再套几层都行，只要挂 `@tap` 的那层只有这一个孩子。
- **「填满」是字面意思，差一点都不行**：2026-10-29 排查「对话页右上角三个点点不开」时走了弯路。
  那个按钮（`.moreBtn` 64rpx 里包一个 28rpx 的 `.moreDots`）其实是**好的**——真人点得动，
  是 `adb shell input tap` 的坐标没校准，把「点偏了」误判成「代码点不动」，白改了三轮。
  **教训**：真机排查点击问题，先用 `uiautomator dump` 或截图量像素校准坐标，确认真的点在控件上，
  再怀疑代码；能让人手动点一下确认的，别自己反复adb戳。
  （附带确认：`.moreDots` 写死 28rpx 不填满父级，在真人正确点击下工作正常，不需要改。）

### 9c. `picker` 的选项滚轮点不中、也划不动

`<picker mode="selector">` 弹层能正常打开、选项都在，但**点选项行选不中，adb 划也划不动**，
只有取消/确定点得动。不报错，就是选不中。

- **来源**：做「主题外观」三选一时踩的。弹层正常弹出，点「明亮」没反应、滑也没反应，
  `@change` 始终不触发。
- **适用**：任何「几个固定选项选一个」的场景。**过期**：DCloud 修好 picker 的选项触摸。
- **做法**：固定选项用 `.opt` 行（`view + @tap + text 子元素`，选中态用 `v-if` 渲染一个
  `&#10003;`），参考 `pages/person/person.uvue` 的外观三选一、`pages/preconsult/preconsult.uvue`。
  `mode="date"` 这类连续值能不能用没验证过，用之前先真机点一下。

### 9d. `uni.chooseImage` 必须写 `albumMode: 'system'` + `sourceType: ['album']`

两个都得写，只写一个还是选不中：

- `sourceType` 默认 `['album','camera']`，会先弹 **`uni.showActionSheet` 动作面板**。实测那个面板的
  选项行**收不到点击**——点「从相册选择」面板会关、相册永远不打开，`success` 不回来、`fail` 也不回来，
  **界面毫无反应也不报错**（点面板外的区域能关掉它，所以极易误判成"我点歪了"）。
  只给 `['album']` 直接进相册，面板这一步整个绕掉。
- `albumMode` 默认 `'custom'`，是 uni 自己在 vapor 渲染树里画的相册，和 §9c 的 `<picker>`
  同一个毛病，格子点不动。写 `'system'` 走系统相册（独立 activity，不走 vapor 渲染），触摸正常。
- **`albumMode` 不能和 `sourceType: ['camera']` 一起传**：它是相册专属参数，一起传相机根本起不来
  （不报错，success/fail 都不回调）。选相机时整个省掉。

- **来源**：做头像上传时踩的，三个坑各踩一遍。写成 `system` + 只给一端 + 按来源给 albumMode 后一遍通过。
- **适用**：任何 `uni.chooseImage` / `uni.chooseVideo` / `uni.chooseMedia`；以及任何想用
  `uni.showActionSheet` 做「二选一」的地方——**它的选项行点不动，别用**。
- **过期**：DCloud 修掉自定义相册与动作面板的触摸。
- **做法**：选图片统一走 `android/utils/pickimage.ts`（头像上传和报告上传都走它）。
  真要「拍照 / 相册」二选一，在页面内用 `.sheetRow` 自己画底部弹层（`pages/account/account.uvue`
  的换头像弹层就是），那是 §9b 里点得动的形状。

### 10. 别用负 margin 把元素顶出容器

`margin-top: -52rpx` 想让底部弹钮「凸起」时，视觉画在位置 A，**原生触摸框还在位置 B**，点不动且不报错。

- **来源**：首页标签栏中间的「问诊」圆钮，`@tap` 对、编译过、样子也对，就是点不了；改成正常撑高容器后立刻好。
- **适用**：任何想让元素溢出父容器的布局。**过期**：DCloud 修掉负 margin 下的触摸框计算。
- **做法**：父容器正常变高，元素做大做实心底，一样醒目。

### 10b. 固定定位遮罩里的滚动区，`height` 写死不裁剪

底部弹层（`position: fixed` 的 mask + 底部 sheet）里放 `scroll-view`，**给 scroll-view 写死
`height: 620rpx` 不生效**——内容会画出框外，弹层底部被屏幕切掉一截，而且滚不动（不报错）。
`height` 写在 **sheet 自己身上** + scroll-view 用 `flex: 1; min-height: 0` 才对，
和 `account.uvue` 的 `.page{height:100%} > .scroll` 是同一套。

- **来源**：对话页的角色弹层。第一次给 `.sheetScroll` 写死 620rpx，「创建自定义角色」按钮被屏幕
  切掉半个；换成 sheet 定高 + flex:1 后正常滚动。
- **适用**：任何 fixed 遮罩里的可滚动内容。**过期**：DCloud 让 fixed 容器里的 scroll-view 认死高度。
- **做法**：`.sheet { height: 1200rpx; display: flex; flex-direction: column }` +
  `.sheetScroll { flex: 1; min-height: 0; width: 100%; align-self: stretch }`。
- **高度别用百分比**：fixed 遮罩里子元素的 `%` 高度解析得不对劲（实测写 76% 实际只有 55%
  左右，弹层底被屏幕切掉一截）；而且百分比一旦让弹层高过遮罩，flex 的交叉轴拉伸会失效，
  滚动区缩成按内容撑宽。**用 rpx 写死**，按 1080×2376 和 1080×1920 两种屏核一遍放得下。

### 10c. `@tap.stop` 在蒸汽模式是可用的，别不敢用

底部弹层要「点外面关、点里面不关」时，mask 挂 `@tap`、sheet 挂 `@tap.stop="keepOpen"` 实测有效
（Android 真机验过：点角色行、点头像格子都不会顺手关掉弹层，点遮罩能关）。
官方兼容表 `stop` 是 Android 3.9+ / iOS 4.11+ / Web 4.0+ / 小程序全支持。

- **注意**：`@tap.stop` **不能省掉处理器写成无值形式**（官方示例一律带 `handleClick`），
  要个空函数占位（`function keepOpen () {}`）。
- **另一个坑**：`v-for` 和 `v-if` **不能写在同一个元素上**——Vue 3 里 v-if 优先于 v-for，
  条件里取不到循环变量。循环挂外层 view、v-if 挂内层元素（`person.uvue` 的主题三选一、
  `chat.uvue` 的头像格子都是这个写法）。

### 10d. 原生 `<button>` 按内容撑宽，不跟随父容器拉伸

`<button>` 在蒸汽模式下默认 `wrap_content`：父容器是通栏的，按钮照样缩成文字那么宽
（实测「提交审核」四个字只有 227px，不是通栏）。**`<view>` 会拉伸，原生 button 不会。**
要通栏按钮就自己写 `width: 100%`（`chat.uvue` 的 `.sheetBtn`）。

- **来源**：对话页角色弹层里的「创建自定义角色 / 填写更多 / 提交审核」三个按钮全是窄窄一条，
  而同一屏的 `<view class="pRow">` 卡片是通栏的——差别只在标签是不是 button。
- **适用**：任何想要通栏的主按钮。**自查**：写完截图量一下按钮左右边界，别只看「有没有」。
- **顺带**：页面里**别为了某个按钮去覆盖全局 `.btn`**（曾经的 `chat.uvue` 给 `.btn` 写死
  `width: 160rpx` 想让发送按钮变小，结果弹层里所有 `.btn` 都跟着变窄）。给特殊按钮单独起类。

---

## 三、调试：别把自己坑了

### 11. 绝对不要用 `timeout` 包 `cli.exe launch`

标准基座靠这条调试连接转发事件，连接一断应用就"冻"住——**不响应点击、不响应返回键，但画面停最后一张**。

- **来源**：曾因此连续四轮误判「点击全部失灵」。**适用**：任何 CLI 真机调试。
- **过期**：永不（工具行为）。**做法**：放后台跑，不设超时。

### 12. 量坐标用截图，别用 uiautomator；点之前先确认屏亮着

`uiautomator dump` 拿不到 uvue 的文本节点，截图 + PIL 量才靠谱。**软键盘弹起会把页面顶滚动**、
**设备息屏后 `input tap` 全部无声失败**（点下去没反应、不报错，极易误判成代码 bug）。
**来源**：多次因坐标错位和自动锁屏把「好的代码」判成坏的。**过期**：永不。

### 13. 别用 `uni.showToast` 当调试探针

调试基座里 toast 不稳定，用 `<text class="error">`（已知能渲染）更可靠。**过期**：toast 稳定后。

### 13b. CLI 重新编译后，被 `reLaunch` 刷新的页面才是新代码

`cli.exe launch` 推完新包，**后台残留的页面实例还是旧的渲染树**——点了没反应、样子也没变，
极易判成「新代码没用」。而 `reLaunch` / `navigateTo` 新建的页面是新代码。

- **来源**：改首页头像可点后连点十几次没反应，换条路（`我的` → 标签栏回首页）再点立刻好。
- **适用**：任何 CLI 真机调试后立刻验证的环节。**过期**：永不（工具行为）。
- **做法**：验证前先 `reLaunch` 到目标页（或整个应用重启），别在残留实例上试。

---

## 四、改动后的自查清单

```bash
cd android
grep -rnE '@(tap|click)="[^"]*=[^"]*"' pages/      # 应无输出：模板禁赋值
grep -rn ":class=\"[^\"]*?[^\"]*'" pages/          # 只许剩"建后不变"的（聊天气泡 role、报告 flag）
grep -rn "\.value\.[a-zA-Z_]* =" pages/            # 应无输出：ref 禁改内部属性
grep -rn "display: *block\|display: *inline-block" pages/   # 应无输出
grep -rnE "^\s*\.[a-zA-Z0-9_-]+\s*[ >+~]\s*[.a-zA-Z]" pages/ App.uvue   # 应无输出：禁关系选择器
# 应无输出：button 里禁嵌 text/view/image
grep -rnzoP '(?s)<button[^>]*>\s*<(text|view|image)\b' pages/ | tr '\0' '\n'
```

⚠️ CLI 编译日志里的 `Selector ... is not supported`、`is not a standard property name`、
`Element '<text>' is not supported in '<button>'` 这类 ERROR/WARNING **都不会让编译失败**
（最后仍打印「编译成功」），但意味着规则被丢掉了。每次编译完务必搜一眼 `is not supported`。

---

## 五、跨端：同一份样式，两端表现不同

主端是 App，小程序端（`mp-weixin`）也能编译。下面几处**两端行为相反或只有一端支持**，必须按平台
分开写，别指望一份样式通用。都记在 `android/README.md` 的「平台相关的几件事」里。

### 14. `@font-face` 的 src：小程序要 base64，App 要包内文件

- **小程序**：wxss 的 `@font-face` **不加载包内本地字体文件**（只认 https 网络地址和 base64），
  表现是图标位置全空白、且不报错。
- **App 蒸汽模式**：反过来**不能吃 data URI 字体**。`src: url('data:font/ttf;base64,...')`
  一上去就崩——`java.util.ConcurrentModificationException`，字体异步加载完成时正在遍历样式表。

`App.uvue` 里必须 `#ifdef MP` 用 base64、`#ifdef APP || H5` 引 `/static/font/*.ttf`，两端分开写；
base64 那串由 `scripts/build-iconfont.py` 重新生成，别手改。

- **来源**：为了让小程序显示图标把 src 统一换成 base64，App 直接崩；改回条件编译后两端都正常。
- **适用**：任何自定义字体。**过期**：DCloud 让蒸汽模式支持 data URI 字体。
- **自查**：改完跑一次 App，确认首页宫格和 tabbar 图标还在——崩了就是条件编译没生效。

### 15. `button` 同时写 `height` 和 `padding`，App 上文字贴上沿

原生 button 自带垂直居中，但**一旦同时写 `height` 和 `padding`** 就失灵：文字贴着按钮上沿、
下方空一大块。不报错，就是位置不对，**小程序端没这个问题**。做法是高度用上下 padding 撑
（`padding: 13rpx 26rpx`），不写 `height`。

- **来源**：找医生页科室胶囊 `.chip`（`height: 60rpx` + `padding: 0 26rpx`），真机实测文字比
  盒子中心高 13px；同页不写 `height` 的 `.btn.small`（88rpx）只偏 2px。记忆页 `.mini` 同病。
- **适用**：任何带横向内边距的 `<button>`（胶囊、小按钮）。**过期**：DCloud 修掉 button 的 padding 布局。
- **效果**：实测偏差从 -13px 变成 +2px，胶囊总高只从 86px 变 90px，视觉无差。按钮里也别写
  `line-height`（见 §9 注释，会把文字压到偏下）。

### 16. 主题变量：必须定义在 `page` 下，且 `defaultAppTheme` 要设 auto

蒸汽模式支持 CSS 自定义变量和 `@media (prefers-color-scheme)`（App 5.25+、Web/小程序也支持），
但有三处和 Web 端不一样：

- **不支持 `:root`**，变量要定义在 `page` 选择器下（`App.uvue` 里现成就有 `page { }`）。
- **`manifest.json` 的 `app.defaultAppTheme` 必须是 `auto`**。不配默认是 `light`，此时 appTheme
  永远停在 light，`@media (prefers-color-scheme: dark)` 那套**永远不匹配**——不报错，就是暗色
  不生效（实测踩过，改 manifest 后才好）。
- 变量值不能再是 `var(--x)`；`padding`/`margin` 简写里别用 `var`（VDOM 模式明确不支持，蒸汽模式
  文档没写，保守起见躲开）。
- **`:style` 动态绑定是会生效的**（真机验证：`:style="{ 'background-color': d }"` 逐项换色，
  截图能看到颜色跟着数据变）。规则 5 禁的只是动态 `:class`，别把 `:style` 一起躲开。

- **来源**：给 `App.uvue` 上暗黑模式时逐个踩的。**适用**：任何主题/换色改动。
- **做法**：`App.uvue` 顶部一张亮暗变量表 + 页面一律 `var(--c-xxx)`，主题变化时页面不重启即时
  刷新（真机实测）。`python contrast.py` 直接从 `App.uvue` 读变量值，两套配色一起验 WCAG。
  小程序端还要在 `manifest.json` 的 `mp-weixin` 里开 `darkmode: true`，否则媒体查询同样不匹配。
