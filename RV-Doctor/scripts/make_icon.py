"""生成 RV-Doctor 快捷方式图标（assets/rv-doctor.ico）。

设计：深蓝底 + 青色芯片轮廓 + 芯片内心电图脉冲线，
呼应项目"给 RISC-V 代码做体检"的定位与界面暗色霓虹配色。
画布 2048 超采样后缩到各尺寸，保证边缘抗锯齿。
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
S = 2048  # 主画布（8 倍于 256）

CYAN = (53, 214, 255, 255)        # 芯片描边
PIN = (38, 150, 185, 255)         # 引脚（略暗）
ECG = (160, 240, 255, 255)        # 心电线（亮青白）
PANEL = (20, 38, 66, 255)         # 芯片内面板
BG_TOP = (18, 29, 52)
BG_BOT = (9, 16, 30)

R = 460  # 圆角半径（约 22.5%，接近系统图标观感）

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# ---- 背景：垂直渐变 + 双色微光 ----
top = np.array(BG_TOP, dtype=float)
bot = np.array(BG_BOT, dtype=float)
t = np.linspace(0.0, 1.0, S)[:, None, None]
rgb = (top[None, None, :] * (1 - t) + bot[None, None, :] * t)

yy, xx = np.mgrid[0:S, 0:S].astype(float)

def glow(cx, cy, rad, color, strength):
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rad
    f = np.clip(1.0 - d, 0.0, 1.0) ** 2
    return np.array(color, dtype=float) * (strength * f)[:, :, None]

rgb = rgb + glow(1024, 1100, 980, (53, 214, 255), 0.10)
rgb = rgb + glow(1720, 320, 820, (124, 92, 255), 0.09)
rgb = np.clip(rgb, 0, 255)

bg = Image.fromarray(np.dstack([rgb, np.full((S, S, 1), 255.0)]).astype("uint8"), "RGBA")
img = Image.alpha_composite(img, bg)

d = ImageDraw.Draw(img)

# ---- 芯片：内面板 + 圆角描边 ----
chip = [384, 384, 1664, 1664]
d.rounded_rectangle(chip, radius=180, fill=PANEL)
d.rounded_rectangle(chip, radius=180, outline=CYAN, width=60)

# ---- 四边引脚：每侧 3 根，圆角矩形，与描边搭接 ----
pw, plen, over = 64, 120, 12
for off in (-460, 0, 460):
    c = 1024 + off
    d.rounded_rectangle([c - pw // 2, 384 - plen, c + pw // 2, 384 + over], radius=30, fill=PIN)
    d.rounded_rectangle([c - pw // 2, 1664 - over, c + pw // 2, 1664 + plen], radius=30, fill=PIN)
    d.rounded_rectangle([384 - plen, c - pw // 2, 384 + over, c + pw // 2], radius=30, fill=PIN)
    d.rounded_rectangle([1664 - over, c - pw // 2, 1664 + plen, c + pw // 2], radius=30, fill=PIN)

# ---- 心电图：P 波 → QRS 尖峰 → T 波 ----
pts = [
    (560, 1100), (760, 1100), (830, 960), (900, 1100), (1000, 1100),
    (1070, 640), (1160, 1345), (1215, 1100), (1330, 1100),
    (1395, 975), (1450, 1100), (1488, 1100),
]
d.line(pts, fill=ECG, width=52, joint="curve")
cap = 26
for x, y in (pts[0], pts[-1]):
    d.ellipse([x - cap, y - cap, x + cap, y + cap], fill=ECG)

# ---- 圆角裁切 + 缩放输出 ----
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S, S], radius=R, fill=255)
img.putalpha(mask)

ASSETS.mkdir(exist_ok=True)
ico_path = ASSETS / "rv-doctor.ico"
img.resize((256, 256), Image.LANCZOS).save(
    str(ico_path),
    sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
)
preview_dir = ROOT / "workdir" / "assets"
preview_dir.mkdir(parents=True, exist_ok=True)
img.resize((256, 256), Image.LANCZOS).save(str(preview_dir / "icon_preview.png"))
print("written:", ico_path)
