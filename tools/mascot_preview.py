"""
ClaudeMeter 吉祥物預覽工具 (mascot_preview.py)

讀取韌體 main.cpp「角色設計區」裡的角色格子與手臂參數，用和韌體相同的算法
畫出彈跳 + 擺手動畫，存成 GIF。改角色時先用這支看效果，滿意了再燒錄。

用法（需要 Pillow：pip install pillow）：
    python tools/mascot_preview.py                 # 產生 mascot_preview.gif
    python tools/mascot_preview.py --zoom 6        # 放大倍率（預設 4）
    python tools/mascot_preview.py --png           # 另外輸出一張靜態圖 mascot_preview.png

GIF 裡的灰線是下方卡片的上緣 (y=46)，角色動起來不能碰到它。
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "esp32_firmware" / "src" / "main.cpp"

HEADER_H = 50   # 預覽畫面高度（多畫一點，才看得到卡片上緣）
CARD_TOP = 46   # 韌體裡第一張卡片的 y 座標
ANIM_STEP = 0.15  # 韌體 loop() 每幀 animAngle 增加量


def rgb565_to_rgb(v: int) -> tuple[int, int, int]:
    r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def load_config(src: Path) -> dict:
    text = src.read_text(encoding="utf-8")
    body_block = re.search(r"MASCOT_BODY\[\]\s*=\s*\{(.*?)\};", text, re.S)
    if not body_block:
        raise SystemExit(f"在 {src} 裡找不到 MASCOT_BODY")
    body = re.findall(r'"([^"]*)"', body_block.group(1))
    widths = {len(r) for r in body}
    if len(widths) != 1:
        raise SystemExit(f"MASCOT_BODY 每一列的字元數必須相同，目前有：{sorted(widths)}")

    defines = dict(re.findall(r"^#define\s+(\w+)\s+([^\s/]+)", text, re.M))

    def num(name: str) -> float:
        return float(defines[name].rstrip("fFuUlL"))

    cfg = {
        "body": body,
        "scale": int(num("MASCOT_SCALE")),
        "hover": num("MASCOT_HOVER_PX"),
        "waves": int(num("MASCOT_WAVES_PER_BOUNCE")),
        "cy": int(num("HEADER_CY")),
        "elbow_x": num("ARM_ELBOW_X"),
        "elbow_y": num("ARM_ELBOW_Y"),
        "arm_len": int(num("ARM_LENGTH")),
        "arm_thick": int(num("ARM_THICK")),
        "base_deg": num("ARM_BASE_DEG"),
        "swing_deg": num("ARM_SWING_DEG"),
        "orange": rgb565_to_rgb(int(defines["COLOR_CLAUDE_ORANGE"], 16)),
    }
    if (len(body[0]), len(body)) != (int(num("MASCOT_BODY_W")), int(num("MASCOT_BODY_H"))):
        print(
            f"[WARN] MASCOT_BODY 實際是 {len(body[0])}x{len(body)}，"
            f"但 MASCOT_BODY_W/H 寫的是 {defines['MASCOT_BODY_W']}x{defines['MASCOT_BODY_H']}，"
            "韌體會畫錯，請修正這兩個數字"
        )
    return cfg


def cell_color(ch: str, cfg: dict):
    return {"#": cfg["orange"], "w": (255, 255, 255), "k": (0, 0, 0)}.get(ch)


def draw_arm(d: ImageDraw.ImageDraw, cfg: dict, body_x: int, body_y: int, deg: float, mirror: bool):
    s = cfg["scale"]
    rad = math.radians(deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    reach = cfg["arm_len"] + cfg["arm_thick"]
    ex, ey = cfg["elbow_x"], cfg["elbow_y"]
    w = len(cfg["body"][0])
    for gy in range(math.floor(ey) - reach, math.ceil(ey) + reach + 1):
        for gx in range(math.floor(ex) - reach, math.ceil(ex) + reach + 1):
            dx, dy = gx + 0.5 - ex, gy + 0.5 - ey
            u = dx * cos_a + dy * sin_a
            v = -dx * sin_a + dy * cos_a
            if -1.0 <= u < cfg["arm_len"] and abs(v) <= cfg["arm_thick"] / 2:
                cx = (w - 1 - gx) if mirror else gx
                x0, y0 = body_x + cx * s, body_y + gy * s
                d.rectangle([x0, y0, x0 + s - 1, y0 + s - 1], fill=cfg["orange"])


def render_frame(cfg: dict, anim_angle: float) -> Image.Image:
    s = cfg["scale"]
    body = cfg["body"]
    w, h = len(body[0]), len(body)
    total_w = (w + 2 * cfg["arm_len"]) * s
    canvas_w = total_w + 24
    img = Image.new("RGB", (canvas_w, HEADER_H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.line([(0, CARD_TOP), (canvas_w, CARD_TOP)], fill=(70, 70, 70))

    x = (canvas_w - total_w) // 2
    offset_y = math.sin(anim_angle) * cfg["hover"]
    body_x = x + cfg["arm_len"] * s
    body_y = cfg["cy"] - (h * s) // 2 + round(offset_y)

    arm_deg = cfg["base_deg"] - math.sin(anim_angle * cfg["waves"]) * cfg["swing_deg"]
    draw_arm(d, cfg, body_x, body_y, arm_deg, False)
    draw_arm(d, cfg, body_x, body_y, arm_deg, True)

    for r, row in enumerate(body):
        for c, ch in enumerate(row):
            color = cell_color(ch, cfg)
            if color:
                x0, y0 = body_x + c * s, body_y + r * s
                d.rectangle([x0, y0, x0 + s - 1, y0 + s - 1], fill=color)
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description="預覽 ClaudeMeter 吉祥物動畫")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="韌體原始碼路徑")
    ap.add_argument("--out", type=Path, default=Path("mascot_preview.gif"))
    ap.add_argument("--zoom", type=int, default=4, help="預覽放大倍率")
    ap.add_argument("--fps", type=int, default=25, help="播放速度（實機大約 20~30 fps）")
    ap.add_argument("--png", action="store_true", help="另外輸出第一幀靜態圖")
    args = ap.parse_args()

    cfg = load_config(args.src)
    frames = []
    angle = 0.0
    while angle <= 2 * math.pi:
        f = render_frame(cfg, angle)
        frames.append(f.resize((f.width * args.zoom, f.height * args.zoom), Image.NEAREST))
        angle += ANIM_STEP

    frames[0].save(
        args.out, save_all=True, append_images=frames[1:],
        duration=round(1000 / args.fps), loop=0,
    )
    print(f"已輸出 {args.out}（{len(frames)} 幀）")
    if args.png:
        png = args.out.with_suffix(".png")
        frames[0].save(png)
        print(f"已輸出 {png}")


if __name__ == "__main__":
    main()
