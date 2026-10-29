"""算 WCAG 对比度。用来验证亮/暗两套主题的文字可读性。

用法：python contrast.py
改 App.uvue 里主题变量的值、或给某个 surface 配了新文字色之后，跑一遍确认别掉到 4.5 以下。
两套主题都要过：脚本直接从 App.uvue 的 @media 块里读变量值，不在这里另抄一份，
所以改了 App.uvue 再跑就行，不会出现「检查过了但页面已经变了」。
"""
import io
import re
import sys

APP = 'App.uvue'

# (说明, 文字色变量, 底色变量)
CASES = [
    ('正文 / 页面底', '--c-text', '--c-page'),
    ('正文 / 卡片', '--c-text', '--c-card'),
    ('次级正文 / 卡片', '--c-text-2', '--c-card'),
    ('次要文字 / 卡片', '--c-muted', '--c-card'),
    ('弱字(占位符) / 卡片', '--c-faint', '--c-card'),
    ('弱字(占位符) / 软卡片', '--c-faint', '--c-card-soft'),
    ('主色字 / 卡片', '--c-brand', '--c-card'),
    ('主色字 / 页面底', '--c-brand', '--c-page'),
    ('按钮字 / 主色底', '--c-on-brand', '--c-brand'),
    ('正文 / 薄荷底', '--c-text', '--c-mint'),
    ('薄荷强调字 / 薄荷底', '--c-mint-text', '--c-mint'),
    ('主色字 / 薄荷底', '--c-brand', '--c-mint'),
    ('正文 / 警示底', '--c-text', '--c-danger-bg'),
    ('警示强调字 / 警示底', '--c-danger-text', '--c-danger-bg'),
    ('警示字 / 警示底', '--c-danger', '--c-danger-bg'),
    ('警示字 / 卡片', '--c-danger', '--c-card'),
    ('正文 / 等待底', '--c-text', '--c-warn-bg'),
    ('等待字 / 等待底', '--c-warn', '--c-warn-bg'),
    ('正文 / 软卡片', '--c-text', '--c-card-soft'),
    ('正文 / AI 气泡', '--c-text', '--c-ai-bubble'),
    ('成功字 / 卡片', '--c-ok', '--c-card'),
    ('次要文字 / 灰底', '--c-muted', '--c-gray-bg'),
]

# 亮色主题里**改动之前就不达标**的组合，不是本次暗黑引入的回归。
# 记在这里而不是把颜色改掉：改了就动了 app 原有长相，超出暗黑的范围。
# key = (主题, 说明)，value = 原因。新加的失败项不在这表里就会让脚本非零退出。
ACCEPTED = {
    ('light', '次要文字 / 卡片'): '原有 #788985 次要说明文字，改动前 3.67',
    ('light', '弱字(占位符) / 卡片'): '原有 #9AA7A2 输入框占位符，改动前 2.49',
    ('light', '弱字(占位符) / 软卡片'): '同上，占位符本就不按正文要求',
    ('light', '等待字 / 等待底'): '原有 #A9701F 等通知橙，改动前 3.66',
    ('light', '成功字 / 卡片'): '原有 #3D8975 成功绿，改动前 4.17',
    ('light', '次要文字 / 灰底'): '原有 #788985 on #EFF1EE，改动前 3.23',
}

# 不主题化的自包含深色块，两端同一套字面量，单独验
FIXED = [
    ('hero 白字 / 深绿底（两端同一套）', '#FFFFFF', '#1E574B'),
    ('问助手圆钮白字 / 主色底（两端同一套）', '#FFFFFF', '#24695B'),
]


def parse_themes():
    """从 App.uvue 的两个 @media 块里读出变量表。"""
    src = io.open(APP, encoding='utf-8').read()
    themes = {}
    for m in re.finditer(
            r'@media \(prefers-color-scheme: (\w+)\) \{\s*page \{(.*?)\n\t\t\}', src, re.S):
        name, body = m.group(1), m.group(2)
        vals = dict(re.findall(r'(--c-[\w-]+):\s*([^;]+);', body))
        themes[name] = {k: v.strip() for k, v in vals.items()}
    return themes


def lum(color):
    c = color.strip()
    if c.startswith('rgba'):
        # rgba 只当边框/投影用，不出现在文字对比里；真出现了就明确报错
        raise SystemExit('对比度检查不该收到 rgba：' + c)
    h = c.lstrip('#')
    if len(h) == 3:
        h = ''.join(ch * 2 for ch in h)
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [((x + 0.055) / 1.055) ** 2.4 if x > 0.03928 else x / 12.92 for x in ch]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(fg, bg):
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def tag(r):
    if r >= 7:
        return 'AAA'
    if r >= 4.5:
        return 'AA'
    if r >= 3:
        return 'AA仅大字'
    return '不通过'


def main():
    themes = parse_themes()
    for name in ('light', 'dark'):
        if name not in themes:
            sys.exit('App.uvue 里找不到 %s 主题的变量定义' % name)

    print('%-24s %16s %16s' % ('组合', '浅色', '暗色'))
    print('-' * 60)
    bad, accepted = [], []
    for label, fg, bg in CASES:
        cells = []
        for theme in ('light', 'dark'):
            t = themes[theme]
            for v in (fg, bg):
                if v not in t:
                    sys.exit('%s 主题里没有变量 %s' % (theme, v))
            r = ratio(t[fg], t[bg])
            cells.append((r, tag(r)))
            if r < 4.5 and (theme, label) not in ACCEPTED:
                bad.append('%s · %s %.2f:1' % (theme, label, r))
            elif r < 4.5:
                accepted.append('%s · %s %.2f:1 —— %s' % (theme, label, r, ACCEPTED[(theme, label)]))
        print('%-24s %7.2f %-8s %7.2f %-8s' % (label, cells[0][0], cells[0][1],
                                               cells[1][0], cells[1][1]))

    print('-' * 60)
    for label, fg, bg in FIXED:
        r = ratio(fg, bg)
        print('%-24s %7.2f %-8s' % (label, r, tag(r)))
        if r < 4.5:
            bad.append('%s %.2f:1' % (label, r))

    if accepted:
        print('已接受的既有不达标（亮色原有配色，非本次引入）：')
        for a in accepted:
            print('  ', a)
        print()
    if bad:
        print('不达 AA 的 %d 项：' % len(bad))
        for b in bad:
            print('  ', b)
        sys.exit(1)
    print('全部通过 WCAG AA')


if __name__ == '__main__':
    main()
