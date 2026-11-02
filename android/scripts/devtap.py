"""真机调试助手：截图找色块、点击、连拍监控。

为什么需要它：uni-app x 是自绘渲染，**不暴露 Android 无障碍层**，
`uiautomator dump` 拿不到任何 text 或 content-desc，所以点控件只能靠截图 + 像素颜色定位。
（2026-11-02 实测：肉眼估坐标偏了 58px，连点两次没反应。）

用法（解释器用 services/api/.venv，pillow 只装在那儿）：

    # 找色块团，打印每团中心坐标和像素数
    python devtap.py find 24695B [--tol 25] [--y0 2000] [--y1 2200] [--x0 600]

    # 找到面积最大的一团并点它
    python devtap.py tap 24695B [--tol 25] [--y0 2000] [--y1 2200]

    # 每秒统计某色像素数，打印增长曲线（验证流式渲染）
    python devtap.py watch F1F5F1 --secs 20

    # 只截图
    python devtap.py shot out.png
"""
from __future__ import annotations

import subprocess
import sys
import time
from collections import defaultdict

SHOT = "/sdcard/_devtap.png"
LOCAL = "_devtap.png"


def adb(*args: str) -> None:
    subprocess.run(["adb", *args], check=True, capture_output=True)


def shot(path: str = LOCAL) -> None:
    adb("shell", "screencap", "-p", SHOT)
    adb("pull", SHOT, path)


def blobs(im, rgb: tuple[int, int, int], tol: int, box: tuple[int, int, int, int]):
    """按 16px 网格聚类匹配像素，再把相邻网格并成一个团。

    返回 [(中心x, 中心y, 像素数)]，按面积降序。
    """
    from PIL import Image

    px = im.load()
    x0, y0, x1, y1 = box
    cells: dict[tuple[int, int], int] = defaultdict(int)
    for y in range(max(0, y0), min(im.size[1], y1)):
        for x in range(max(0, x0), min(im.size[0], x1)):
            r, g, b = px[x, y][:3]
            if abs(r - rgb[0]) <= tol and abs(g - rgb[1]) <= tol and abs(b - rgb[2]) <= tol:
                cells[(x // 16, y // 16)] += 1

    # 相邻网格（4 邻域）并成一个团：一个大按钮会被网格拆成几十格，不并起来没法点
    seen: set[tuple[int, int]] = set()
    out = []
    for start in cells:
        if start in seen:
            continue
        stack, group = [start], []
        seen.add(start)
        while stack:
            cur = stack.pop()
            group.append(cur)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (cur[0] + dx, cur[1] + dy)
                if nxt in cells and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        if len(group) * 256 < 2048:  # 滤掉边缘抗锯齿的零星格
            continue
        xs = [c[0] * 16 + 8 for c in group]
        ys = [c[1] * 16 + 8 for c in group]
        out.append((sum(xs) // len(xs), sum(ys) // len(ys), sum(cells[c] for c in group) * 256))
    out.sort(key=lambda t: -t[2])
    return out


def pick(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """--x0/--y0/--x1/--y1 → box，默认全屏。"""
    a = sys.argv
    g = lambda f, d: (int(a[a.index(f) + 1]) if f in a else d)  # noqa: E731
    return (g("--x0", 0), g("--y0", 0), g("--x1", 10**6), g("--y1", 10**6))


def main() -> None:
    from PIL import Image

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "shot":
        shot(sys.argv[2] if len(sys.argv) > 2 else LOCAL)
        print(f"已截图 → {sys.argv[2] if len(sys.argv) > 2 else LOCAL}")
        return
    if cmd not in ("find", "tap", "watch"):
        print(__doc__)
        return

    rgb = tuple(int(sys.argv[2][i : i + 2], 16) for i in (0, 2, 4))
    tol = 25
    if "--tol" in sys.argv:
        tol = int(sys.argv[sys.argv.index("--tol") + 1])
    box = pick(box=(0, 0, 10**6, 10**6))

    if cmd == "watch":
        secs = int(sys.argv[sys.argv.index("--secs") + 1]) if "--secs" in sys.argv else 15
        prev = None
        for i in range(1, secs + 1):
            shot()
            im = Image.open(LOCAL).convert("RGB")
            n = sum(b[2] for b in blobs(im, rgb, tol, box))
            mark = "" if prev is None else ("  ← 增长" if n > prev + 4000 else "")
            print(f"第 {i:2d}s  像素 ≈ {n:7d}{mark}")
            prev = n
            time.sleep(1)
        return

    shot()
    im = Image.open(LOCAL).convert("RGB")
    found = blobs(im, rgb, tol, box)
    if not found:
        print("没找到该颜色的团——换个色值或放宽 --tol")
        return
    for i, (x, y, n) in enumerate(found[:6]):
        print(f"  团{i}: ({x}, {y})  像素 ≈ {n}")
    if cmd == "tap":
        x, y, _ = found[0]
        adb("shell", "input", "tap", str(x), str(y))
        print(f"已点面积最大的团 ({x}, {y})")


if __name__ == "__main__":
    main()
