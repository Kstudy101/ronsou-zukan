"""회차 섬네일을 만든다.

작게 줄었을 때도 읽히는 것이 전부다. 배경은 어둡게 눌러 두고, 왼쪽에 큰 글자,
오른쪽에 주제를 한눈에 보여 주는 사진 한 장을 원형으로 넣는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.common import episode_dir, load_script  # noqa: E402
from pipeline.render import load_font  # noqa: E402

W, H = 1280, 720


def _find(candidates: dict, title: str) -> dict | None:
    key = title.removeprefix("File:").replace("_", " ")
    for entry in (c for group in candidates.values() for c in group if c.get("file")):
        if entry["title"].removeprefix("File:").replace("_", " ") == key:
            return entry
    return None


def _cover(src: Image.Image, size: tuple[int, int]) -> Image.Image:
    ratio = max(size[0] / src.width, size[1] / src.height)
    scaled = src.resize((int(src.width * ratio) + 1, int(src.height * ratio) + 1),
                        Image.LANCZOS)
    left, top = (scaled.width - size[0]) // 2, (scaled.height - size[1]) // 2
    return scaled.crop((left, top, left + size[0], top + size[1]))


def build_graphic(episode_id: str, out_name: str = "thumbnail.jpg") -> Path:
    """도해형 섬네일 — 사진 대신 수치를 그대로 얼굴로 쓴다.

    작게 줄었을 때 읽히는 것이 전부이므로, 헤드라인 한 덩어리와 대립 막대
    하나만 남기고 나머지는 버린다.
    """
    from pipeline.graphics import A_COLOR, B_COLOR, DIM, INK, _ground, _glow

    script = load_script(episode_id)
    design = script["design"]
    spec = script["thumbnail"]

    canvas = _ground((W, H)).convert("RGBA")
    face = design["ja_font"]
    # 헤드라인은 글자 수가 회차마다 달라 고정 크기로는 반드시 넘친다.
    # 폭에 맞을 때까지 줄인다.
    head_font = load_font(face, spec.get("headline_size", 190), "Black")
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    size = spec.get("headline_size", 190)
    while size > 60 and probe.textlength(spec["headline"], font=head_font) > W - 150:
        size -= 6
        head_font = load_font(face, size, "Black")
    sub_font = load_font(face, 52, "Bold")
    num_font = load_font(face, 86, "Black")
    tag_font = load_font(face, 34, "Bold")

    draw = ImageDraw.Draw(canvas)

    # 헤드라인 — 화면의 절반을 준다.
    head = spec["headline"]
    canvas = Image.alpha_composite(
        canvas, _glow((W, H), [70, 96, W - 70, 330], (120, 160, 220), 55, 60))
    draw = ImageDraw.Draw(canvas)
    draw.text((W / 2, 214), head, font=head_font, fill=INK, anchor="mm",
              stroke_width=12, stroke_fill=(0, 0, 0, 235))

    if spec.get("sub"):
        draw.text((W / 2, 352), spec["sub"], font=sub_font, fill=DIM, anchor="mm")

    if spec.get("mode") == "delta":
        # 비율이 아니라 전후 변화를 보여줄 때. 대립 막대로 그리면 두 숫자가
        # 한 덩어리의 몫처럼 보여 오해를 부르므로 화살표로 잇는다.
        # 화살표는 「전후 변화」를 뜻한다. 두 지역·두 집단을 견주는 것뿐이라면
        # separator 를 vs 로 두어야 한다. 안 그러면 A가 B로 변한 것처럼 읽힌다.
        sep = spec.get("separator", "→")
        big = load_font(face, 132, "Black")
        lab = load_font(face, 40, "Medium")
        # 자릿수가 많으면 가운데 기호와 부딪힌다. 겹치지 않을 때까지 줄인다.
        size = 132
        while size > 70 and max(draw.textlength(spec["before"], font=big),
                                draw.textlength(spec["after"], font=big)) > W * 0.38:
            size -= 6
            big = load_font(face, size, "Black")
        y = 500
        draw.text((W * 0.26, y), spec["before"], font=big, fill=B_COLOR, anchor="mm",
                  stroke_width=9, stroke_fill=(0, 0, 0, 220))
        draw.text((W * 0.26, y + 108), spec["before_label"], font=lab,
                  fill=DIM, anchor="mm")
        draw.text((W * 0.50, y), sep, font=load_font(face, 96, "Black"),
                  fill=INK, anchor="mm")
        draw.text((W * 0.74, y), spec["after"], font=big, fill=A_COLOR, anchor="mm",
                  stroke_width=9, stroke_fill=(0, 0, 0, 220))
        draw.text((W * 0.73, y + 108), spec["after_label"], font=lab,
                  fill=DIM, anchor="mm")
        tag = f"{script['series']} #{script['episode']:02d}"
        tw = draw.textlength(tag, font=tag_font)
        draw.rounded_rectangle([56, 40, 56 + tw + 44, 96], radius=28,
                               fill=(255, 255, 255, 34))
        draw.text((56 + 22 + tw / 2, 68), tag, font=tag_font,
                  fill=(226, 236, 255), anchor="mm")
        dest = episode_dir(episode_id) / "out" / out_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(dest, quality=92, subsampling=0)
        print(f"섬네일: {dest}  ({dest.stat().st_size / 1024:.0f}KB)")
        return dest

    # 대립 막대 — 실제 비율. 팽팽하면 경계선이 한가운데 선다.
    a_value, b_value = float(spec["a_value"]), float(spec["b_value"])
    ratio = a_value / (a_value + b_value or 1)
    bar_w, bar_h = int(W * 0.78), 96
    x0, y0 = (W - bar_w) // 2, 440
    split_x = x0 + int(bar_w * ratio)
    draw.rounded_rectangle([x0, y0, split_x, y0 + bar_h], radius=8, fill=A_COLOR)
    draw.rounded_rectangle([split_x, y0, x0 + bar_w, y0 + bar_h], radius=8, fill=B_COLOR)
    draw.rectangle([split_x - 4, y0 - 16, split_x + 4, y0 + bar_h + 16], fill=INK)

    draw.text((x0 + 16, y0 + bar_h / 2), f"{a_value:,.0f}", font=num_font,
              fill=(255, 255, 255), anchor="lm",
              stroke_width=7, stroke_fill=(0, 0, 0, 210))
    draw.text((x0 + bar_w - 16, y0 + bar_h / 2), f"{b_value:,.0f}", font=num_font,
              fill=(255, 255, 255), anchor="rm",
              stroke_width=7, stroke_fill=(0, 0, 0, 210))
    draw.text((x0, y0 + bar_h + 40), spec["a_label"], font=sub_font,
              fill=A_COLOR, anchor="lt")
    draw.text((x0 + bar_w, y0 + bar_h + 40), spec["b_label"], font=sub_font,
              fill=B_COLOR, anchor="rt")

    tag = f"{script['series']} #{script['episode']:02d}"
    tw = draw.textlength(tag, font=tag_font)
    draw.rounded_rectangle([56, 40, 56 + tw + 44, 96], radius=28,
                           fill=(255, 255, 255, 34))
    draw.text((56 + 22 + tw / 2, 68), tag, font=tag_font,
              fill=(226, 236, 255), anchor="mm")

    dest = episode_dir(episode_id) / "out" / out_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(dest, quality=92, subsampling=0)
    print(f"섬네일: {dest}  ({dest.stat().st_size / 1024:.0f}KB)")
    return dest


def build(episode_id: str, out_name: str = "thumbnail.jpg") -> Path:
    script = load_script(episode_id)
    if script["thumbnail"].get("type") == "graphic":
        return build_graphic(episode_id, out_name)
    design = script["design"]
    spec = script["thumbnail"]
    base = episode_dir(episode_id)
    images = base / "assets" / "images"
    candidates = json.loads((images / "candidates.json").read_text(encoding="utf-8"))

    backdrop_entry = _find(candidates, spec["backdrop_title"])
    subject_entry = _find(candidates, spec["image_title"])
    if not backdrop_entry or not subject_entry:
        raise SystemExit("섬네일에 쓸 사진을 수집 목록에서 찾지 못했다.")

    # 배경 — 흐리고 어둡게 눌러 글자가 뜨게 한다.
    canvas = _cover(Image.open(images / backdrop_entry["file"]).convert("RGB"), (W, H))
    canvas = canvas.filter(ImageFilter.GaussianBlur(7))
    canvas = Image.blend(canvas, Image.new("RGB", (W, H), (9, 11, 18)), 0.60)

    # 왼쪽에서 오른쪽으로 어두워지는 그라데이션(글자 쪽을 더 진하게)
    shade = Image.new("L", (W, 1))
    for x in range(W):
        shade.putpixel((x, 0), int(215 * max(0.0, 1.0 - (x / W) / 0.80)))
    shade = shade.resize((W, H))
    canvas = Image.composite(Image.new("RGB", (W, H), (6, 8, 14)), canvas, shade)

    # 오른쪽 주제 사진 — 원형으로 오려 붙인다.
    diameter = 400
    subject = _cover(Image.open(images / subject_entry["file"]).convert("RGB"),
                     (diameter, diameter))
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, diameter - 1, diameter - 1], fill=255)
    cx, cy = W - 258, H // 2

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [cx - diameter // 2 - 16, cy - diameter // 2 - 16,
         cx + diameter // 2 + 16, cy + diameter // 2 + 16],
        fill=(255, 208, 120, 90))
    canvas = Image.alpha_composite(
        canvas.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(22))).convert("RGB")
    canvas.paste(subject, (cx - diameter // 2, cy - diameter // 2), mask)

    ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse(
        [cx - diameter // 2, cy - diameter // 2, cx + diameter // 2, cy + diameter // 2],
        outline=(255, 216, 140, 235), width=6)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), ring).convert("RGB")

    draw = ImageDraw.Draw(canvas)
    chip = load_font(design["ko_font"], 30, "Bold")
    tag = f"{script['series']} #{script['episode']:02d}"
    tw = draw.textlength(tag, font=chip)
    draw.rounded_rectangle([64, 52, 64 + tw + 48, 110], radius=29,
                           fill=(255, 255, 255, 34))
    draw.text((64 + 24 + tw / 2, 81), tag, font=chip, fill=(232, 240, 255), anchor="mm")

    headline = load_font(design["ko_font"], 108, "Black")
    lines = spec["headline"]
    line_height = 118
    top = H / 2 - (len(lines) * line_height) / 2 - 26
    for index, line in enumerate(lines):
        draw.text((70, top + index * line_height), line, font=headline,
                  fill="#FFFFFF", stroke_width=8, stroke_fill=(0, 0, 0, 220))

    sub = load_font(design["ko_font"], 46, "Bold")
    draw.text((74, top + len(lines) * line_height + 22), spec["sub"], font=sub,
              fill="#FFD46B", stroke_width=6, stroke_fill=(0, 0, 0, 215))

    dest = base / "out" / out_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, quality=92, subsampling=0)
    print(f"섬네일: {dest}  ({dest.stat().st_size / 1024:.0f}KB)")
    return dest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode")
    parser.add_argument("--out", default="thumbnail.jpg")
    args = parser.parse_args()
    build(args.episode, args.out)
