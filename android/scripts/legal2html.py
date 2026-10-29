"""把 docs/legal 的两份协议渲染成 App 内嵌网页，输出到 android/static/legal/。

用法（在 android/ 目录下）：
    python scripts/legal2html.py

为什么是「md → html → web-view」而不是把条款抄成 uvue 页面：
条款有二百多行、带表格，抄一遍就等于有了两份真相源，改一处漏一处。
web-view 的 src 支持 /static 下的本地路径（官方文档「src路径支持说明」），
所以编译时 html 会一起打进包，不依赖网络、不需要业务域名白名单。

两份文档里有三类内容不是给用户看的，渲染时按下面规则处理：
  1. 文首 `> ⚠️ ...` 的内部状态说明引用块 —— 整块剔除；
  2. `## 附：` 开头的内部附录（上线前必办清单）—— 整节剔除；
  3. 正文里 `> 【未实现】` 的落地说明 —— **保留**，渲染成黄框。
     第 3 类现在不能删：它们标注的条款（列加密、注销、导出）本身还没实现，
     删了注释就变成宣传自己没做到的保护。上线前逐项落地后，
     把 md 里这些块手工删掉再跑一次本脚本即可。

改完 md 必须重跑本脚本，否则 App 里显示的还是旧文本。
生成物不入库review，和 types/api.ts 一个性质：不许手改 html。
"""
import html
import os
import re
import sys

# 相对 android/ 目录运行
LEGAL_DIR = '../docs/legal'
OUT_DIR = 'F:/hongmeng/pj1/android/static/legal'

DOCS = [
    ('用户协议_v1.0.md', 'user-agreement.html', '用户协议'),
    ('隐私政策_v1.0.md', 'privacy-policy.html', '隐私政策'),
]

# 剔除后不允许残留在产物里的字样。漏删一条就报错退出，别悄悄发给用户。
FORBIDDEN = ['内部状态说明', '本文档状态说明', '上线前必办', '定稿时删除本段']

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 28rpx 32rpx 64rpx;
  background: #FBFCF9; color: #1D3734;
  font: 30rpx/1.75 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  -webkit-text-size-adjust: 100%;
}
h1 { font-size: 44rpx; line-height: 1.4; margin: 0 0 8rpx; }
h2 {
  font-size: 34rpx; margin: 56rpx 0 16rpx; padding-left: 20rpx;
  border-left: 8rpx solid #24695B; line-height: 1.4;
}
h3 { font-size: 30rpx; margin: 40rpx 0 12rpx; color: #24695B; }
p { margin: 0 0 20rpx; }
strong { font-weight: 600; color: #12312C; }
code {
  font: 26rpx/1.5 ui-monospace, Menlo, Consolas, monospace;
  background: #F4F7F4; border-radius: 6rpx; padding: 2rpx 8rpx; color: #24695B;
}
hr { border: 0; border-top: 2rpx solid rgba(36,105,91,.14); margin: 48rpx 0; }
blockquote {
  margin: 0 0 24rpx; padding: 20rpx 24rpx;
  background: #F4F7F4; border-left: 8rpx solid #24695B; border-radius: 0 12rpx 12rpx 0;
}
blockquote p { margin: 0 0 12rpx; }
blockquote p:last-child { margin-bottom: 0; }
/* 未实现说明：黄框，和条款正文一眼可辨 */
blockquote.todo {
  background: #FDF6E7; border-left-color: #C9922B;
  color: #5C4A1E; font-size: 27rpx; line-height: 1.7;
}
blockquote.todo strong { color: #6B4E12; }
table { width: 100%; border-collapse: collapse; margin: 0 0 24rpx; font-size: 26rpx; }
th, td { border: 2rpx solid rgba(36,105,91,.16); padding: 12rpx 14rpx; text-align: left; vertical-align: top; }
th { background: #F4F7F4; font-weight: 600; white-space: nowrap; }
ol, ul { margin: 0 0 24rpx; padding-left: 40rpx; }
li { margin-bottom: 10rpx; }
.meta { font-size: 26rpx; color: #788985; line-height: 1.7; }
"""


def inline(text: str) -> str:
    """行内：先转义，再加粗、行内代码。顺序不能反，否则转义会把标签吃掉。"""
    out = html.escape(text, quote=False)
    out = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', out)
    out = re.sub(r'`([^`]+)`', r'<code>\1</code>', out)
    return out


def is_todo_quote(lines: list[str]) -> bool:
    return any('【未实现】' in ln or '【已实现】' in ln for ln in lines)


def render_table(rows: list[list[str]]) -> str:
    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip('|').split('|')]

    head, body = rows[0], rows[2:]  # 第二行是 |---|---|
    out = ['<table><thead><tr>']
    out += [f'<th>{inline(c)}</th>' for c in cells(head)]
    out.append('</tr></thead><tbody>')
    for line in body:
        out.append('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in cells(line)) + '</tr>')
    out.append('</tbody></table>')
    return ''.join(out)


def convert(md: str) -> str:
    lines = md.split('\n')
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 内部附录：从 `## 附：` 砍到文件尾（它本来就是最后一节）
        if stripped.startswith('## 附：'):
            break

        if stripped == '' or stripped == '---':
            if stripped == '---':
                out.append('<hr>')
            i += 1
            continue

        if stripped.startswith('#'):
            level = len(stripped) - len(stripped.lstrip('#'))
            text = inline(stripped.lstrip('#').strip())
            out.append(f'<h{level}>{text}</h{level}>')
            i += 1
            continue

        # 引用块：连续的行都算。⚠️ 行是内部状态说明的起点，从那行砍到块尾，
        # 但块首的版本/运营者行要留着（上线填完就是给用户看的元信息）。
        if stripped.startswith('>'):
            block: list[str] = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                text = lines[i].strip().lstrip('>').strip()
                if text.startswith('⚠️'):
                    while i < len(lines) and lines[i].strip().startswith('>'):
                        i += 1
                    break
                block.append(text)
                i += 1
            if not block:
                continue
            cls = ' class="todo"' if is_todo_quote(block) else ' class="meta"'
            paras = ''.join(f'<p>{inline(p)}</p>' for p in block if p != '')
            out.append(f'<blockquote{cls}>{paras}</blockquote>')
            continue

        # 表格：表头 + 分隔行 + 数据行
        if stripped.startswith('|'):
            block = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                block.append(lines[i])
                i += 1
            out.append(render_table(block))
            continue

        # 有序列表。`1. ` 才算；`1.1 xxx` 是条款段落，点号后没空格
        if re.match(r'^\d+\.\s', stripped):
            items = []
            while i < len(lines) and re.match(r'^\d+\.\s', lines[i].strip()):
                items.append(re.sub(r'^\d+\.\s*', '', lines[i].strip()))
                i += 1
            out.append('<ol>' + ''.join(f'<li>{inline(x)}</li>' for x in items) + '</ol>')
            continue

        if stripped.startswith('- '):
            items = []
            while i < len(lines) and lines[i].strip().startswith('- '):
                items.append(lines[i].strip()[2:])
                i += 1
            out.append('<ul>' + ''.join(f'<li>{inline(x)}</li>' for x in items) + '</ul>')
            continue

        # 普通段落：吃到空行为止
        para = []
        while i < len(lines) and lines[i].strip() != '' and not lines[i].strip().startswith(('#', '>', '|', '- ')) \
                and not re.match(r'^\d+\.\s', lines[i].strip()) and lines[i].strip() != '---':
            para.append(lines[i].strip())
            i += 1
        if para:
            # 文首 `> 版本：...` 那种已被引用分支收走，这里只剩纯段落。
            # 版本/运营者那几行原文档是引用块，会走 blockquote。
            out.append(f'<p>{inline(" ".join(para))}</p>')
        else:
            i += 1  # 兜底：别死循环
    return '\n'.join(out)


def page(title: str, body: str) -> str:
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        f'<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n'
        f'{body}\n</body>\n</html>\n'
    )


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    for src, dst, title in DOCS:
        path = os.path.join(LEGAL_DIR, src)
        with open(path, encoding='utf-8') as f:
            md = f.read()
        body = convert(md)
        for bad in FORBIDDEN:
            if bad in body:
                print(f'{src}: 产物里还留着内部内容「{bad}」，检查剔除规则', file=sys.stderr)
                return 1
        with open(os.path.join(OUT_DIR, dst), 'w', encoding='utf-8', newline='\n') as f:
            f.write(page(title, body))
        print(f'{src} -> static/legal/{dst}  ({len(body)} 字符正文)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
