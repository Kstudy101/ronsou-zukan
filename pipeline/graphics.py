"""장면 배경이 될 도해를 직접 그린다.

스톡 사진을 늘어놓는 대신 수치를 그림으로 만든다. 논쟁 소재는 「숫자가
팽팽하다」는 것 자체가 내용이므로, 대립 막대가 사진보다 훨씬 잘 전달한다.
매 영상에 오리지널 소재를 최소 한 컷 넣는다는 수익화 방어 원칙에도 맞는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.render import load_font  # noqa: E402

# A파 = 남색, B파 = 감색(枾). 두 색만으로 대립을 표현하고 나머지는 무채색.
A_COLOR = (86, 156, 232)
B_COLOR = (240, 146, 92)
INK = (247, 249, 252)
DIM = (150, 162, 180)
GROUND_TOP = (18, 24, 34)
GROUND_BOTTOM = (10, 13, 20)


def _ground(size: tuple[int, int]) -> Image.Image:
    """위아래로 아주 옅게 밝기가 변하는 바탕. 평면적으로 보이지 않게 한다."""
    width, height = size
    base = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        base.putpixel((0, y), tuple(
            round(GROUND_TOP[i] + (GROUND_BOTTOM[i] - GROUND_TOP[i]) * t)
            for i in range(3)
        ))
    return base.resize(size)


def _glow(size: tuple[int, int], box, color, alpha: int, blur: int) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=40, fill=(*color, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _fonts(design: dict):
    path = design["ja_font"]
    return {
        "huge": load_font(path, 200, "Black"),
        "big": load_font(path, 130, "Black"),
        "mid": load_font(path, 66, "Bold"),
        "small": load_font(path, 46, "Medium"),
        "tiny": load_font(path, 34, "Medium"),
    }


# --------------------------------------------------------------------------- #
def versus(design: dict, spec: dict) -> Image.Image:
    """대립 막대 — 실제 비율 그대로 나눈다. 팽팽할수록 가운데에 선이 선다."""
    W, H = design["width"], design["height"]
    canvas = _ground((W, H)).convert("RGBA")
    font = _fonts(design)

    a_value, b_value = float(spec["a_value"]), float(spec["b_value"])
    total = a_value + b_value or 1.0
    a_ratio = a_value / total

    bar_w, bar_h = int(W * 0.86), 132
    x0, y0 = (W - bar_w) // 2, int(H * 0.18)
    split = x0 + int(bar_w * a_ratio)

    canvas = Image.alpha_composite(
        canvas, _glow((W, H), [x0 - 30, y0 - 30, x0 + bar_w + 30, y0 + bar_h + 30],
                      (120, 150, 200), 60, 50))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([x0, y0, split, y0 + bar_h], radius=10, fill=A_COLOR)
    draw.rounded_rectangle([split, y0, x0 + bar_w, y0 + bar_h], radius=10, fill=B_COLOR)
    # 경계선 — 이 선이 가운데에 설수록 팽팽하다는 뜻이다.
    draw.rectangle([split - 3, y0 - 22, split + 3, y0 + bar_h + 22], fill=INK)

    draw.text((x0, y0 - 44), spec["a_label"], font=font["small"],
              fill=A_COLOR, anchor="ls")
    draw.text((x0 + bar_w, y0 - 44), spec["b_label"], font=font["small"],
              fill=B_COLOR, anchor="rs")

    # 백분율을 득표수처럼 보이게 하면 안 된다. 단위와 소수점을 명시한다.
    unit = spec.get("unit", "")
    digits = int(spec.get("decimals", 1 if unit == "%" else 0))
    fmt = lambda v: f"{v:,.{digits}f}{unit}"

    ratio_font = font["big"]
    draw.text((x0 + 14, y0 + bar_h + 30), fmt(a_value), font=ratio_font,
              fill=A_COLOR, anchor="lt")
    draw.text((x0 + bar_w - 14, y0 + bar_h + 30), fmt(b_value), font=ratio_font,
              fill=B_COLOR, anchor="rt")

    gap = abs(a_value - b_value)
    gap_unit = "ポイント" if unit == "%" else unit
    draw.text((W / 2, y0 + bar_h + 236), f"差 {gap:,.{digits}f}{gap_unit}",
              font=font["mid"], fill=INK, anchor="mt")
    return canvas.convert("RGB")


def number(design: dict, spec: dict) -> Image.Image:
    """숫자 한 개를 크게 세운다. 3초 안에 읽히는 것이 목적이다."""
    W, H = design["width"], design["height"]
    canvas = _ground((W, H)).convert("RGBA")
    font = _fonts(design)
    color = A_COLOR if spec.get("side") == "a" else B_COLOR

    cy = int(H * 0.27)
    canvas = Image.alpha_composite(
        canvas, _glow((W, H), [W * 0.12, cy - 190, W * 0.88, cy + 190],
                      color, 70, 70))
    draw = ImageDraw.Draw(canvas)

    draw.text((W / 2, cy - 190), spec.get("label", ""), font=font["mid"],
              fill=DIM, anchor="ms")

    value, unit = str(spec["value"]), spec.get("unit", "")
    value_font = font["huge"] if len(value) <= 5 else font["big"]
    value_w = draw.textlength(value, font=value_font)
    unit_w = draw.textlength(unit, font=font["mid"])
    left = (W - (value_w + 18 + unit_w)) / 2
    draw.text((left, cy), value, font=value_font, fill=color, anchor="lm")
    draw.text((left + value_w + 18, cy + 56), unit, font=font["mid"],
              fill=color, anchor="lm")
    return canvas.convert("RGB")


def split(design: dict, spec: dict) -> Image.Image:
    """세 갈래(우세 A / 우세 B / 동수)를 도도부현 수로 보여준다."""
    W, H = design["width"], design["height"]
    canvas = _ground((W, H)).convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    font = _fonts(design)

    groups = [
        (spec["a_label"], int(spec["a_value"]), A_COLOR),
        (spec["b_label"], int(spec["b_value"]), B_COLOR),
        (spec.get("tie_label", "同数"), int(spec.get("tie_value", 0)), DIM),
    ]
    # 도도부현 하나를 사각형 하나로 — 47개가 실제로 세어진다.
    cell, gap, cols = 62, 12, 9
    top = int(H * 0.14)
    for label, count, color in groups:
        draw.text((W * 0.09, top), f"{label}", font=font["small"],
                  fill=color, anchor="ls")
        draw.text((W * 0.91, top), f"{count}", font=font["mid"],
                  fill=color, anchor="rs")
        top += 26
        for index in range(count):
            cx = W * 0.09 + (index % cols) * (cell + gap)
            cy = top + (index // cols) * (cell + gap)
            draw.rounded_rectangle([cx, cy, cx + cell, cy + cell],
                                   radius=8, fill=color)
        rows = (count + cols - 1) // cols
        top += rows * (cell + gap) + 46
    return canvas.convert("RGB")


def cta(design: dict, spec: dict) -> Image.Image:
    """댓글 유도 — 양자택일을 화면에 세워 두면 손가락이 멈춘다."""
    W, H = design["width"], design["height"]
    canvas = _ground((W, H)).convert("RGBA")
    font = _fonts(design)

    box_w, box_h = int(W * 0.80), 190
    x0 = (W - box_w) // 2
    for index, (label, color) in enumerate(
            [(spec["a_label"], A_COLOR), (spec["b_label"], B_COLOR)]):
        y0 = int(H * 0.15) + index * (box_h + 34)
        canvas = Image.alpha_composite(
            canvas, _glow((W, H), [x0, y0, x0 + box_w, y0 + box_h], color, 55, 40))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=26,
                               outline=color, width=5)
        draw.text((W / 2, y0 + box_h / 2), label, font=font["mid"],
                  fill=color, anchor="mm")

    draw = ImageDraw.Draw(canvas)
    draw.text((W / 2, int(H * 0.15) + 2 * (box_h + 34) + 40), "コメントで",
              font=font["small"], fill=DIM, anchor="mt")
    return canvas.convert("RGB")


def trend(design: dict, spec: dict) -> Image.Image:
    """연도별 추이. 기준선을 넘는 순간이 보이게 그린다.

    「예전엔 반대였다」가 논지일 때는 값 하나보다 꺾은선이 훨씬 강하다.
    """
    W, H = design["width"], design["height"]
    canvas = _ground((W, H)).convert("RGBA")
    font = _fonts(design)

    points = spec["points"]                       # [{"label": "2022", "value": 38.9}, ...]
    values = [float(p["value"]) for p in points]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    pad = span * 0.35
    low, high = low - pad, high + pad

    left, right = int(W * 0.14), int(W * 0.86)
    top, bottom = int(H * 0.15), int(H * 0.36)
    step = (right - left) / max(len(points) - 1, 1)

    def xy(index: int, value: float) -> tuple[float, float]:
        return left + index * step, bottom - (value - low) / (high - low) * (bottom - top)

    draw = ImageDraw.Draw(canvas)

    # 기준선(예: 50%) — 넘었다는 사실 자체가 뉴스일 때 쓴다.
    if spec.get("baseline") is not None:
        base = float(spec["baseline"])
        by = bottom - (base - low) / (high - low) * (bottom - top)
        for x in range(left, right, 26):
            draw.line([(x, by), (x + 13, by)], fill=DIM, width=3)
        draw.text((right + 8, by), spec.get("baseline_label", ""), font=font["tiny"],
                  fill=DIM, anchor="lm")

    coords = [xy(i, v) for i, v in enumerate(values)]
    canvas = Image.alpha_composite(
        canvas, _glow((W, H), [left - 40, top - 40, right + 40, bottom + 40],
                      A_COLOR, 45, 60))
    draw = ImageDraw.Draw(canvas)
    draw.line(coords, fill=A_COLOR, width=9, joint="curve")

    unit = spec.get("unit", "%")
    for index, (x, y) in enumerate(coords):
        last = index == len(coords) - 1
        color = B_COLOR if last else A_COLOR
        radius = 20 if last else 13
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)
        draw.text((x, y - radius - 18), f"{values[index]:g}{unit}",
                  font=font["mid"] if last else font["small"],
                  fill=color, anchor="ms")
        draw.text((x, bottom + 34), points[index]["label"], font=font["small"],
                  fill=DIM, anchor="mt")
    return canvas.convert("RGB")


def choice(design: dict, spec: dict) -> Image.Image:
    """양자택일을 좌우로 세우고 가운데에 물음표를 둔다. 도입부용."""
    W, H = design["width"], design["height"]
    canvas = _ground((W, H)).convert("RGBA")
    font = _fonts(design)

    box_w, box_h = int(W * 0.40), 300
    gap = int(W * 0.06)
    x_left = (W - (box_w * 2 + gap)) // 2
    y0 = int(H * 0.17)
    for index, (label, color) in enumerate(
            [(spec["a_label"], A_COLOR), (spec["b_label"], B_COLOR)]):
        x0 = x_left + index * (box_w + gap)
        canvas = Image.alpha_composite(
            canvas, _glow((W, H), [x0, y0, x0 + box_w, y0 + box_h], color, 60, 45))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=28,
                               outline=color, width=5)
        draw.text((x0 + box_w / 2, y0 + box_h / 2), label, font=font["mid"],
                  fill=color, anchor="mm")

    draw = ImageDraw.Draw(canvas)
    draw.text((W / 2, y0 + box_h / 2), spec.get("center", "?"), font=font["big"],
              fill=INK, anchor="mm")
    return canvas.convert("RGB")


BUILDERS = {"versus": versus, "number": number, "split": split,
            "cta": cta, "choice": choice, "trend": trend}


def build(design: dict, spec: dict) -> Image.Image:
    kind = spec.get("type", "versus")
    if kind not in BUILDERS:
        raise SystemExit(f"모르는 도해 종류: {kind}")
    return BUILDERS[kind](design, spec)
