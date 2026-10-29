# uni-app x 官方文档本地库

> 用途：给写代码的 AI 提供**可查的、版本正确的** uni-app x 文档，避免它靠猜测写 uvue 语法。

## 一、已经拉好了

| 项 | 值 |
|---|---|
| 本地路径 | `F:/hongmeng/pj1/uniapp-x-src/docs` |
| 来源 | `dcloudio/uni-app` 仓库的 **`uni-app-x` 分支** |
| 镜像 | `https://gitcode.com/dcloud/uni-app.git`（GitHub 直连不通） |
| 规模 | 650 个 md，12 MB |
| 内容 | **完整正文**，不是占位符 |

### 分类

| 目录 | 文件数 | 内容 |
|---|---|---|
| `api/` | 166 | API 参数、返回值、兼容性 |
| `css/` | 119 | 每个 CSS 属性的支持情况与值限制 |
| `component/` | 111 | 组件属性与平台差异 |
| `tutorial/` | 36 | 教程、踩坑、排查 |
| `native/` | 36 | 原生 SDK、UTS 插件、原生能力 |
| `plugin/` | 25 | 插件机制 |
| `collocation/` | 16 | manifest.json / pages.json 配置 |
| `mp/` | 6 | 小程序平台差异 |
| `compiler/` | 5 | 编译器行为 |
| 顶层 | 18 | 项目结构、性能、发布说明等 |

## 二、怎么来的（可复现）

```bash
git clone --depth 1 --filter=blob:none --sparse \
  -b uni-app-x \
  https://gitcode.com/dcloud/uni-app.git uniapp-x-src

cd uniapp-x-src
git sparse-checkout set docs
```

只拉 docs 目录，5 秒完成。全是 md，没有框架源码。

**两个走过弯路、不要用的地址**：

| 地址 | 为什么不用 |
|---|---|
| `github.com/dcloudio/unidocs-zh` | 老 uni-app 文档，`docs/uni-app-x` 只有一个跳转说明 |
| `gitcode.com/dcloud/unidocs-uni-app-x-zh` | 外壳仓库，正文是 `<!-- CSSJSON.xxx -->` 占位符，靠 `docs-common` 二次同步才填充 |

## 三、怎么给 AI 用

**不要把这些文件全部塞进上下文**——650 个 md 塞不进去，塞了也没用（AI 会丢中间部分）。

### 方式 A：给 AI 一条检索指令（推荐）

在项目级规则里加一段，让它知道去哪查：

```markdown
## uni-app x 文档

官方文档本地库：`F:/hongmeng/pj1/uniapp-x-src/docs`

写 uni-app x 代码前，涉及以下内容**必须先查文档**，不要凭记忆：

- CSS 属性是否支持、值限制 → `docs/css/<属性名>.md`
- 组件属性与平台差异 → `docs/component/<组件名>.md`
- API 参数与返回值 → `docs/api/<api名>.md`
- 配置项（manifest.json / pages.json）→ `docs/collocation/`

查法：`grep -rn "关键词" docs/` 或直接读对应文件。

已知的坑（本文档库中均有明确记载）：
- `scroll-view` 的 `scroll-y` 已废弃，改用 `direction`
- `uploadFile` 有 `filePath` 与 `files` 两种传参，小程序只认 `filePath`
- `display` 只有 `flex` 与 `none` 两个值
```

### 方式 B：按需检索

```bash
cd F:/hongmeng/pj1/uniapp-x-src/docs

# 某个属性支持什么值
sed -n '/值限制/,/属性值/p' css/max-width.md

# 某个组件在蒸汽模式下支持吗
grep -n "Vapor" component/scroll-view.md

# 找某个 api 的参数
grep -n "filePath\|files" api/upload-file.md
```

### 方式 C：做上下文包

如果用的是不支持文件检索的工具，可以把高频文档合并成一个文件：

```bash
cd F:/hongmeng/pj1/uniapp-x-src/docs
cat css/display.md css/flex*.md component/scroll-view.md component/text.md \
    component/image.md api/request.md api/upload-file.md > ../pack-basic.md
```

代价是每次都要重新生成，且文件会变长。**优先用方式 A**。

## 四、今天踩过的坑，文档原文对照

| 踩的坑 | 文档怎么写 | 位置 |
|---|---|---|
| `scroll-y` 不生效 | 「已废弃，请改用 direction」，并标注 `Android(Vapor): x` | `component/scroll-view.md:60` |
| `display: block` 报错 | 「默认值为 flex（弹性布局）」，值限制只有 enum | `css/display.md` |
| `uploadFile` 传参 | `filePath` 与 `files` 各自列了兼容性表 | `api/upload-file.md:34-36` |
| `max-width: 78%` 报错 | 值限制为 `length` | `css/max-width.md` |

**这四条当时全是靠试错发现的，代价是三轮重新打包。** 有了本地库，这类问题应该在写代码前就被查到。

## 五、线上备用入口

文档站提供了一份给 AI 的索引：

```
https://doc.dcloud.net.cn/uni-app-x/llms.txt
```

61 KB，是全部页面的标题 + 链接列表。适合：

- 本地库没同步时应急
- 快速确认「某个功能有没有文档」
- 定位某个页面在哪个目录

`llms-full.txt`（完整内容版）不存在，返回 404。

## 六、更新

文档随 HBuilderX 版本更新，建议每次升级 HBuilderX 后拉一次：

```bash
cd F:/hongmeng/pj1/uniapp-x-src
git pull --depth 1 origin uni-app-x
```

如果 pull 报错（浅克隆有时会），直接重新 clone 更省事。

## 七、注意事项

1. **不要把这个目录提交进项目仓库**——12 MB 的文档不该进 git。加到 `.gitignore`。
2. **不要修改** docs 里的文件，它是只读参考。
3. 文档写的是「支持」，但**具体到你的 HBuilderX 版本**可能不同。文档里有兼容性表（如 `Android(Vapor): 5.21`），要看那一列，不要只看标题。
