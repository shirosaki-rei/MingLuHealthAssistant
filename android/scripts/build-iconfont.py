"""把 Material Symbols Rounded 子集成 App 用的图标字体。

用法（在 android/ 目录下）：
    python scripts/build-iconfont.py probe  只看哪些图标名能在字体里找到
    python scripts/build-iconfont.py build  重新生成 static/font/minglu-icons.ttf

源字体不入库（5.4 MB），要用先拉一份：
    npm pack material-symbols && tar -xzf material-symbols-*.tgz
    mkdir -p static/font-src && cp package/material-symbols-rounded.woff2 static/font-src/

改完码位记得同步 android/utils/icons.ts 的表（build 会直接打印出来）。
build 还会把新 ttf 的 base64 写回 App.uvue 的 @font-face（小程序只认内联字体），别手改那一行。

为什么用字体而不是 SVG：App 平台不支持内联 svg 标签，<image src="*.svg"> 也不能用
css 换色——选中/未选中就得备两份文件。字体可以用 color 控制，一个文件全搞定，
也是官方文档给的跨端写法（unicode 直显）。
"""
import base64
import io
import os
import re
import sys

from fontTools.ttLib import TTFont
from fontTools import subset

SRC = '../static/font-src/material-symbols-rounded.woff2'
OUT_DIR = 'F:/hongmeng/pj1/android/static/font'

# 名字 -> 用在哪。子集只保留这些，别的一概丢掉。
ICONS = {
    # 底部导航
    'home': '导航·首页',
    'travel_explore': '导航·找医生',
    'forum': '导航·问助手',
    'favorite': '导航·健康',
    'person': '导航·我的',
    # 首页 / 健康页快捷入口
    'assignment': '预问诊',
    'stethoscope': '找医生',
    'description': '看报告/检查报告',
    'monitor_heart': '健康/指标',
    'cloud_upload': '上传报告',
    'medication': '用药提醒',
    # 我的
    'group': '就诊人档案',
    'timeline': '健康时间线',
    'notifications': '健康提醒',
    'auto_awesome': '角色与语气',
    'psychology': '助手记住的事',
    'lock': '隐私与授权',
    'help': '帮助与反馈',
    'logout': '退出登录',
    # 通用操作
    'send': '发送',
    'edit': '编辑',
    'delete': '删除',
    'chevron_right': '右箭头',
    'close': '关闭',
    'add': '新增',
    'check': '选中/确认',
    'arrow_back': '返回',
    'search': '搜索',
    'warning': '警示',
    'info': '说明',
    'payments': '支付',
    'schedule': '时间',
    'repeat': '重复',
    'photo_camera': '拍照',
    'local_hospital': '医院',
    'emergency': '急救',
    'settings': '设置',
    'event_available': '已完成',
    'pending': '待处理',
}


def codepoints():
    f = TTFont(SRC)
    cmap = f.getBestCmap()
    inv = {v: k for k, v in cmap.items()}
    found, missing = {}, []
    for name in ICONS:
        if name in inv:
            found[name] = inv[name]
        else:
            missing.append(name)
    return found, missing


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'probe'
    found, missing = codepoints()

    if cmd == 'probe':
        print('找到 %d 个，缺 %d 个' % (len(found), len(missing)))
        for name in sorted(found):
            print('  %-18s U+%04X  %s' % (name, found[name], ICONS[name]))
        if missing:
            print('\n字体里没有这些名字，得换：')
            for name in missing:
                print('  %-18s %s' % (name, ICONS[name]))
        sys.exit(0)

    if cmd != 'build':
        sys.exit('未知命令：' + cmd)

    if missing:
        print('还有 %d 个图标名没找到，先换掉再 build：%s' % (len(missing), ', '.join(missing)))
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, 'minglu-icons.ttf')
    opts = subset.Options()
    opts.flavor = None            # 输出 ttf，App 上比 woff2 稳
    opts.name_IDs = ['*']
    opts.notdef_outline = True
    opts.recalc_bounds = True
    opts.drop_tables += ['DSIG']
    font = subset.load_font(SRC, opts)
    subsetter = subset.Subsetter(options=opts)
    subsetter.populate(unicodes=sorted(found.values()))
    subsetter.subset(font)
    subset.save_font(font, out, opts)
    size = os.path.getsize(out)
    print('生成 %s（%.1f KB，%d 个字形）' % (out, size / 1024, len(found)))

    # base64 内联回 App.uvue 的 @font-face：微信小程序 wxss 的 @font-face 不加载包内
    # 本地字体文件，只有内联（或 https）能显示。ttf 本身留在 static/font 里当源。
    app = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'App.uvue')
    with io.open(app, encoding='utf-8') as f:
        css = f.read()
    b64 = base64.b64encode(io.open(out, 'rb').read()).decode('ascii')
    pat = re.compile(r"(src: url\('data:font/ttf;base64,)[^']*(('\))")
    if not pat.search(css):
        sys.exit('App.uvue 里没找到 base64 的 @font-face src，先按注释里的格式补一行再 build')
    with io.open(app, 'w', encoding='utf-8', newline='') as f:
        f.write(pat.sub(lambda m: m.group(1) + b64 + m.group(2), css, count=1))
    print('已把 base64（%d 字符）写回 App.uvue' % len(b64))

    # 顺手吐出 name -> 转义字面量 的映射，页面里直接粘
    print('\n页面里这样用：')
    print('  <text class="ic">&#x%X;</text>' % found['home'])
    rows = ['    %s: \'\\\\u%04x\',  // %s' % (n, found[n], ICONS[n]) for n in sorted(found)]
    print('\n'.join(rows))
