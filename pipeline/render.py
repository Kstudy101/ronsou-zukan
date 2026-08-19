"""장면 이미지 + 한/일 자막 + 나레이션 + BGM 을 세로 쇼츠로 합성한다.

프레임을 Pillow 로 만들어 ffmpeg 표준입력으로 흘려보낸다. 필터그래프를 크게
쓰지 않으므로 자막 위치와 타이밍을 정확히 제어할 수 있다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.common import episode_dir, ffmpeg_exe, load_script, save_json  # noqa: E402

SR = 44100
PLATE_SCALE = 1.10           # 켄번스 여유분(클수록 움직임이 크고 레이아웃이 흔들린다)
CROSSFADE = 0.45
SUB_FADE = 0.15


# --------------------------------------------------------------------------- #
# 폰트
# --------------------------------------------------------------------------- #
def load_font(path: str, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path, size)
    if weight:
        try:
            font.set_variation_by_name(weight)
        except Exception:  # noqa: BLE001  가변폰트가 아니면 그대로 쓴다
            pass
    return font


# 줄 첫머리에 오면 안 되는 문자(일본어 금칙 처리 최소판)
NO_LINE_START = "。、）」』】・？！,.);:ーぁぃぅぇぉっゃゅょァィゥェォッャュョ"
NO_LINE_END = "（「『【("

# 조사·조동사. 앞말에 붙는 것이라 이 앞에서 끊으면 낱말이 두 동강 난다.
PARTICLES = "はがをにへとでもやかのねよねばなら"
BREAK_AFTER = "。、！？」』）"      # 여기서 끊으면 가장 자연스럽다


UNITS = "％%年月日円人票県市区時分秒個回目度割倍万億千ポ"   # 숫자에 붙는 단위(ポ=ポイント)
FORBIDDEN = float("inf")


def _is_kanji(char: str) -> bool:
    return "一" <= char <= "鿿"


def _is_kana(char: str) -> bool:
    return "ぁ" <= char <= "ゟ"


def _is_katakana(char: str) -> bool:
    return "ァ" <= char <= "ヿ"


def _break_penalty(text: str, index: int) -> float:
    """text[index] 뒤에서 줄을 끊을 때의 어색함. 낮을수록 좋은 자리다."""
    if index + 1 >= len(text):
        return 0.0
    before, after = text[index], text[index + 1]

    # 숫자는 절대 가르지 않는다. 「10.5 / ％」 같은 것도 막는다.
    if before.isdigit() and (after.isdigit() or after in ".,"):
        return FORBIDDEN
    if before in ".," and after.isdigit():
        return FORBIDDEN
    if before.isdigit() and after in UNITS:
        return FORBIDDEN
    if before in "万億千" and after.isdigit():
        return FORBIDDEN                   # 16万/9447 처럼 자릿수 사이를 가르지 않는다
    if before in "万億千" and after in UNITS:
        return FORBIDDEN                   # 1593万/票

    if before in BREAK_AFTER:
        return -40.0                       # 문장부호 뒤 — 가장 자연스럽다
    if after in PARTICLES:
        return 95.0                        # 조사 앞 — 앞말과 떨어진다
    if before in PARTICLES:
        return -20.0                       # 조사 뒤 — 낱말이 끝난 자리
    if before == after:
        return 90.0                        # 半々, 各々 같은 반복 표기
    if _is_katakana(before) and _is_katakana(after):
        return 85.0                        # シングル 같은 외래어를 가른다
    if _is_kanji(before) and _is_kanji(after):
        return 75.0                        # 한자어 중간을 가른다
    if _is_kanji(before) and _is_kana(after):
        return 60.0                        # 呼/び 처럼 오쿠리가나를 뗀다
    if _is_kana(before) and _is_kana(after):
        return 30.0                        # ほ/ぼ 처럼 낱말 한가운데일 확률
    return 0.0


def wrap_balanced(draw: ImageDraw.ImageDraw, text: str, font,
                  max_width: int) -> list[str]:
    """줄 길이를 고르게 맞추면서, 끊기 좋은 자리를 골라 나눈다.

    앞에서부터 꽉 채우는 방식은 마지막 줄에 한 글자만 남기거나 「両方」처럼
    붙어 있어야 할 낱말을 가른다. 필요한 줄 수를 먼저 정하고, 그 줄 수 안에서
    「들쭉날쭉함 + 끊는 자리의 어색함」이 가장 작은 조합을 고른다.
    """
    text = text.strip()
    if not text or draw.textlength(text, font=font) <= max_width:
        return [text]

    widths = [draw.textlength(text[:i], font=font) for i in range(len(text) + 1)]
    length = len(text)

    def width_of(start: int, end: int) -> float:
        return widths[end] - widths[start]

    # 줄 수는 최소로 고정한다. 자유롭게 두면 끊는 자리의 벌점을 피하려고
    # 줄만 늘려 「あなたの / 街は / …」처럼 잘게 쪼개진다.
    rows, cursor = 0, 0
    while cursor < length:
        step = cursor
        while step < length and width_of(cursor, step + 1) <= max_width:
            step += 1
        cursor = max(step, cursor + 1)
        rows += 1

    # 최소 줄 수로는 금지 규칙(숫자↔단위 등)을 지킬 수 없는 문장이 있다.
    # 「しかも今、国は28度と言っていない」가 두 줄에 안 들어가는 경우가 그렇다.
    # 이때 글자 단위 줄바꿈으로 떨어지면 벌점을 아예 안 보므로 「28 / 度」처럼
    # 가장 나쁜 자리에서 갈린다. 한 줄 늘려서라도 규칙을 지키는 쪽을 먼저 쓴다.
    for count in (rows, rows + 1, rows + 2):
        lines = _split_rows(text, widths, count, max_width)
        if lines is not None:
            return lines
    return wrap(draw, text, font, max_width)       # 그래도 안 되면 예전 방식


def _split_rows(text: str, widths: list[float], rows: int,
                max_width: int) -> list[str] | None:
    """정확히 rows 줄로 나눈다. 금지된 자리만 피해서는 못 나누면 None."""
    length = len(text)
    target = widths[-1] / rows
    INF = float("inf")
    cost = [[INF] * (rows + 1) for _ in range(length + 1)]
    back = [[0] * (rows + 1) for _ in range(length + 1)]
    cost[0][0] = 0.0

    for end in range(1, length + 1):
        for row in range(1, rows + 1):
            for start in range(end):
                if cost[start][row - 1] == INF:
                    continue
                span = widths[end] - widths[start]
                if span > max_width:
                    continue
                if text[start] in NO_LINE_START or text[end - 1] in NO_LINE_END:
                    continue
                penalty = _break_penalty(text, end - 1)
                if penalty == FORBIDDEN:
                    continue
                # 줄 길이를 고르게(들쭉날쭉함) + 끊는 자리가 자연스럽게(벌점)
                slack = (target - span) / max(target, 1.0)
                value = cost[start][row - 1] + slack * slack * 120.0 + penalty
                if value < cost[end][row]:
                    cost[end][row] = value
                    back[end][row] = start

    if cost[length][rows] == INF:
        return None

    lines, end, row = [], length, rows
    while row > 0:
        start = back[end][row]
        lines.append(text[start:end])
        end, row = start, row - 1
    return lines[::-1]


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """글자 단위로 재되, 공백이 있으면 공백에서 끊는다.

    일본어에는 단어 사이 공백이 없어 공백 기준 줄바꿈만으로는 화면을 넘친다.
    """
    lines, current = [], ""
    for char in text:
        trial = current + char
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
            continue
        space = current.rfind(" ")
        if space > 0:
            lines.append(current[:space])
            current = current[space + 1:] + char
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)

    tidy: list[str] = []
    for line in (l.strip() for l in lines):
        if not line:
            continue
        if tidy and line[0] in NO_LINE_START:
            tidy[-1] += line[0]
            line = line[1:].strip()
        if tidy and tidy[-1] and tidy[-1][-1] in NO_LINE_END:
            line = tidy[-1][-1] + line
            tidy[-1] = tidy[-1][:-1]
        if line:
            tidy.append(line)
    return tidy or [text]


# --------------------------------------------------------------------------- #
# 장면 배경(플레이트)
# --------------------------------------------------------------------------- #
def build_plate(image_path: Path, design: dict) -> Image.Image:
    W = int(design["width"] * PLATE_SCALE)
    H = int(design["height"] * PLATE_SCALE)
    src = Image.open(image_path).convert("RGB")

    # 흐린 배경으로 화면을 꽉 채운다(가로 사진을 세로 화면에 넣기 위한 처리).
    ratio = max(W / src.width, H / src.height)
    bg = src.resize((int(src.width * ratio) + 1, int(src.height * ratio) + 1),
                    Image.LANCZOS)
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H))
    bg = bg.filter(ImageFilter.GaussianBlur(46))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (10, 12, 18)), 0.62)

    # 머리말 아래, 자막 위에 사진 카드를 얹는다.
    card_w = int(W * 0.935)
    card_h = int(H * 0.360)
    fit = min(card_w / src.width, card_h / src.height)
    photo = src.resize((max(1, int(src.width * fit)), max(1, int(src.height * fit))),
                       Image.LANCZOS)

    radius = int(20 * PLATE_SCALE)
    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, photo.width - 1, photo.height - 1], radius=radius, fill=255)

    x = (W - photo.width) // 2
    y = int(H * 0.148) + (card_h - photo.height) // 2

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [x - 6, y - 4, x + photo.width + 6, y + photo.height + 12],
        radius=radius + 6, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    bg = Image.alpha_composite(bg.convert("RGBA"), shadow).convert("RGB")

    bg.paste(photo, (x, y), mask)

    border = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(
        [x, y, x + photo.width - 1, y + photo.height - 1],
        radius=radius, outline=(255, 255, 255, 46), width=max(1, int(2 * PLATE_SCALE)))
    return Image.alpha_composite(bg.convert("RGBA"), border).convert("RGB")


def oversize(image: Image.Image, design: dict) -> Image.Image:
    """도해를 켄번스가 쓸 수 있도록 플레이트 크기로 키운다."""
    return image.resize(
        (int(design["width"] * PLATE_SCALE), int(design["height"] * PLATE_SCALE)),
        Image.LANCZOS,
    )


def kenburns(plate: Image.Image, progress: float, design: dict, mode: int) -> Image.Image:
    """플레이트에서 창을 잘라내 천천히 밀고 당긴다."""
    W, H = design["width"], design["height"]
    span = PLATE_SCALE - 1.0
    if mode % 2 == 0:                       # 서서히 확대
        zoom = PLATE_SCALE - span * progress
    else:                                   # 서서히 축소
        zoom = 1.0 + span * progress
    # int() 로 만든 플레이트가 W*PLATE_SCALE 보다 살짝 작을 수 있어 창을 가둔다.
    win_w = min(W * zoom, plate.width)
    win_h = min(H * zoom, plate.height)
    drift = (progress - 0.5) * span * W * 0.28 * (1 if mode % 4 < 2 else -1)
    cx = plate.width / 2 + drift
    cy = plate.height / 2 + (progress - 0.5) * span * H * 0.10
    left = min(max(cx - win_w / 2, 0.0), plate.width - win_w)
    top = min(max(cy - win_h / 2, 0.0), plate.height - win_h)
    return plate.resize((W, H), Image.BILINEAR,
                        box=(left, top, left + win_w, top + win_h))


# --------------------------------------------------------------------------- #
# 고정 오버레이(자막·머리말·출처)
# --------------------------------------------------------------------------- #
def build_header(script: dict, design: dict) -> Image.Image:
    W, H = design["width"], design["height"]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    face = design.get("ko_font") or design["ja_font"]
    label = load_font(face, 32, "Bold")
    title = load_font(face, 46, "Bold")

    tag = f"{script['series']} #{script['episode']:02d}"
    tw = draw.textlength(tag, font=label)
    draw.rounded_rectangle([(W - tw) / 2 - 26, 74, (W + tw) / 2 + 26, 128],
                           radius=28, fill=(255, 255, 255, 30))
    draw.text((W / 2, 101), tag, font=label, fill=(226, 236, 255, 230), anchor="mm")

    head = " · ".join(x for x in (script.get("topic_ko"), script.get("topic_ja")) if x)
    draw.text((W / 2, 172), head, font=title, fill=(255, 255, 255, 236),
              anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0, 150))
    return layer


def build_subtitle_ja(main: str, tsukkomi: str, credit: str,
                      design: dict) -> Image.Image:
    """일본 사양 텔롭.

    일본 방송 자막은 1990년대 이후 「정보 보완」에서 「츳코미」로 역할이
    바뀌었다. 그래서 본문 한 줄만 놓지 않고, 나레이션에 딴지를 거는 두 번째
    화자를 따로 세운다. 색은 무채색 2 + 액센트 2 = 4색을 넘기지 않는다.
    """
    W, H = design["width"], design["height"]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    main_size = design.get("main_size", 104)
    font = load_font(design["ja_font"], main_size, design.get("ja_weight", "Black"))
    # 세로형은 좌우 UI 를 피해 화면 끝에서 10% 안쪽에 둔다.
    max_w = int(W * 0.80)
    lines = wrap_balanced(draw, main, font, max_w)
    line_h = int(main_size * 1.26)

    # 줄 수가 장면마다 달라도 첫 줄이 늘 같은 높이에서 시작하게 위를 고정한다.
    # 블록 중심을 고정하면 1줄·2줄·3줄 장면에서 자막이 위아래로 튄다.
    top = H * design["subtitle_center_y_pct"] / 100.0 - line_h / 2

    # 테두리는 검정 단색, 굵기는 문자 크기의 5~10%.
    stroke = max(4, round(main_size * 0.075))
    y = top
    for line in lines:
        draw.text((W / 2, y + line_h / 2), line, font=font,
                  fill=design.get("main_color", "#FFFFFF"), anchor="mm",
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 235))
        y += line_h

    if tsukkomi:
        size = design.get("tsukkomi_size", 52)
        tk_font = load_font(design["ja_font"], size, "Bold")
        # 본문과 나란히 두지 않고 살짝 어긋나게 놓아야 「다른 화자」로 읽힌다.
        tx, ty = W * 0.70, y + size * 1.35
        draw.text((tx, ty), tsukkomi, font=tk_font,
                  fill=design.get("tsukkomi_color", "#7FE3C4"), anchor="mm",
                  stroke_width=max(3, round(size * 0.08)), stroke_fill=(0, 0, 0, 230))

    if credit:
        small = load_font(design["ja_font"], 26, "Medium")
        draw.text((W / 2, H - 96), credit, font=small,
                  fill=(190, 200, 215, 170), anchor="mm",
                  stroke_width=3, stroke_fill=(0, 0, 0, 170))
    return layer


def build_subtitle(ko: str, ja: str, credit: str, design: dict) -> Image.Image:
    W, H = design["width"], design["height"]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    ko_font = load_font(design["ko_font"], design["ko_size"], "Bold")
    ja_font = load_font(design["ja_font"], design["ja_size"], "Medium")
    max_w = int(W * 0.87)

    ko_lines = wrap(draw, ko, ko_font, max_w)
    ja_lines = wrap(draw, ja, ja_font, max_w)
    ko_lh = int(design["ko_size"] * 1.30)
    ja_lh = int(design["ja_size"] * 1.34)
    gap = 22

    block_h = len(ko_lines) * ko_lh + gap + len(ja_lines) * ja_lh
    center_y = H * design["subtitle_center_y_pct"] / 100.0
    top = center_y - block_h / 2

    # 글자가 배경에 묻히지 않도록 뒤에 옅은 판을 깐다.
    pad_x, pad_y = 34, 26
    widths = [draw.textlength(line, font=ko_font) for line in ko_lines]
    widths += [draw.textlength(line, font=ja_font) for line in ja_lines]
    plate_w = min(W - 40, max(widths + [1.0]) + pad_x * 2)
    backing = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(backing).rounded_rectangle(
        [(W - plate_w) / 2, top - pad_y, (W + plate_w) / 2, top + block_h + pad_y],
        radius=30, fill=(8, 10, 16, 128))
    layer = Image.alpha_composite(layer, backing)
    draw = ImageDraw.Draw(layer)

    y = top
    for line in ko_lines:
        draw.text((W / 2, y + ko_lh / 2), line, font=ko_font,
                  fill=design["ko_color"], anchor="mm",
                  stroke_width=6, stroke_fill=(0, 0, 0, 205))
        y += ko_lh
    y += gap
    for line in ja_lines:
        draw.text((W / 2, y + ja_lh / 2), line, font=ja_font,
                  fill=design["ja_color"], anchor="mm",
                  stroke_width=5, stroke_fill=(0, 0, 0, 195))
        y += ja_lh

    if credit:
        small = load_font(design["ja_font"], 24)
        draw.text((W / 2, H - 78), credit, font=small,
                  fill=(198, 205, 220, 168), anchor="mm",
                  stroke_width=3, stroke_fill=(0, 0, 0, 150))
    return layer


# --------------------------------------------------------------------------- #
# 오디오
# --------------------------------------------------------------------------- #
def decode_pcm(path: Path) -> np.ndarray:
    """ffmpeg 로 디코딩해 44.1k 스테레오 float 배열로 만든다."""
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path),
         "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4").reshape(-1, 2).astype(np.float32)


def build_audio(episode_id: str, timeline: dict, design: dict, dest: Path) -> float:
    voice_dir = episode_dir(episode_id) / "assets" / "voice"
    total = int(np.ceil(timeline["total"] * SR)) + SR // 2
    narration = np.zeros((total, 2), dtype=np.float32)

    for scene in timeline["scenes"]:
        pcm = decode_pcm(voice_dir / scene["audio"])
        start = int(scene["start"] * SR)
        end = min(start + len(pcm), total)
        narration[start:end] += pcm[: end - start]

    narration *= 0.89 / (np.abs(narration).max() or 1.0)

    bgm_path = episode_dir(episode_id) / "assets" / "bgm" / "bgm.wav"
    with wave.open(str(bgm_path), "rb") as fp:
        frames = np.frombuffer(fp.readframes(fp.getnframes()), dtype="<i2")
        bgm = frames.reshape(-1, fp.getnchannels()).astype(np.float32) / 32768.0
    if bgm.shape[1] == 1:
        bgm = np.repeat(bgm, 2, axis=1)
    if len(bgm) < total:
        bgm = np.pad(bgm, ((0, total - len(bgm)), (0, 0)))
    bgm = bgm[:total]

    gain = 10 ** (design["bgm_gain_db"] / 20.0)

    # 말이 나오는 구간에서는 BGM 을 더 낮춘다(사이드체인 대용).
    envelope = np.abs(narration).max(axis=1)
    window = int(0.35 * SR)
    smooth = np.convolve(envelope, np.ones(window) / window, mode="same")
    duck = 1.0 - 0.45 * np.clip(smooth / (np.percentile(smooth, 92) or 1.0), 0, 1)

    mixed = np.tanh((narration + bgm * gain * duck[:, None]) * 1.06) * 0.97
    pcm = (np.clip(mixed, -1, 1) * 32767).astype("<i2")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as fp:
        fp.setnchannels(2)
        fp.setsampwidth(2)
        fp.setframerate(SR)
        fp.writeframes(pcm.tobytes())
    return total / SR


# --------------------------------------------------------------------------- #
def resolve_pick(scene: dict, candidates: dict) -> dict:
    """장면이 쓸 컷을 고른다.

    파일 순번(s01_c0.jpg)은 재수집할 때마다 가리키는 사진이 달라진다. 그래서
    공용 파일 제목(pick_title)으로 찾는 것을 우선한다. 출처를 표기할 수 없는
    컷은 쓰지 않는다.
    """
    pool = [c for group in candidates.values() for c in group if c.get("file")]
    wanted = scene.get("pick_title")
    if wanted:
        key = wanted.removeprefix("File:").replace("_", " ")
        for entry in pool:
            if entry["title"].removeprefix("File:").replace("_", " ") == key:
                return entry
        raise RuntimeError(
            f"{scene['id']}: '{wanted}' 를 수집 목록에서 찾지 못했다. "
            f"fetch_images 를 다시 돌려라."
        )
    for entry in pool:
        if entry["file"] == scene.get("pick"):
            return entry
    raise RuntimeError(f"{scene['id']}: 출처를 확인할 수 없는 컷은 쓰지 않는다.")


def credit_line(entry: dict | None) -> str:
    if not entry:
        return ""
    name = entry["title"].removeprefix("File:").rsplit(".", 1)[0]
    return f"{name[:48]} — {entry['license']} / Wikimedia Commons"


def render(episode_id: str, out_name: str = "video.mp4") -> Path:
    script = load_script(episode_id)
    design = script["design"]
    base = episode_dir(episode_id)
    timeline = json.loads((base / "assets" / "voice" / "timeline.json")
                          .read_text(encoding="utf-8"))
    manifest = base / "assets" / "images" / "candidates.json"
    candidates = json.loads(manifest.read_text(encoding="utf-8"))         if manifest.exists() else {}

    W, H, fps = design["width"], design["height"], design["fps"]
    scenes = {s["id"]: s for s in script["scenes"]}
    marks = timeline["scenes"]

    japanese = design.get("subtitle_mode") == "ja"

    print("· 장면 배경 준비")
    plates, subs, credits = [], [], []
    for mark in marks:
        scene = scenes[mark["id"]]
        if japanese:
            # 도해는 화면 전체를 쓴다. 사진 카드로 감싸지 않는다.
            spec = scene["visual"]
            if spec.get("type") == "photo":
                entry = resolve_pick(scene, candidates)
                plates.append(build_plate(base / "assets" / "images" / entry["file"],
                                          design))
                credits.append(entry)
                credit = credit_line(entry)
            else:
                from pipeline import graphics

                # 수치를 그리는 도해는 출처를 반드시 달게 한다. 대립 막대는
                # 실제 비율 그대로 그려야 신뢰가 서는데, 근거 없는 숫자가
                # 하나라도 섞이면 채널 전체가 무너지기 때문이다.
                if spec.get("type") in {"versus", "number", "split"}                         and not spec.get("source"):
                    raise RuntimeError(
                        f"{scene['id']}: 수치 도해({spec['type']})에 source 가 없다. "
                        f"출처를 적을 수 없는 숫자는 화면에 올리지 않는다."
                    )
                plates.append(oversize(graphics.build(design, spec), design))
                credits.append(None)
                credit = spec.get("source") or script.get("source", "")
            subs.append(build_subtitle_ja(scene["ja"], scene.get("tsukkomi", ""),
                                          credit, design))
            continue
        entry = resolve_pick(scene, candidates)
        plates.append(build_plate(base / "assets" / "images" / entry["file"], design))
        credits.append(entry)
        subs.append(build_subtitle(scene["ko"], scene["ja"],
                                   credit_line(entry), design))

    header = build_header(script, design)
    audio_path = base / "out" / "audio.wav"
    duration = build_audio(episode_id, timeline, design, audio_path)
    total_frames = int(duration * fps)
    print(f"· 프레임 {total_frames}장 렌더 ({duration:.1f}초)")

    out_path = base / "out" / out_name
    proc = subprocess.Popen(
        [ffmpeg_exe(), "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(fps), "-i", "-",
         "-i", str(audio_path),
         "-c:v", "libx264", "-preset", "medium", "-crf", "19",
         "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
         "-g", str(fps * 2), "-movflags", "+faststart",
         # 화자마다 음량이 크게 달라서 피크 정규화만으로는 들쭉날쭉하다.
         # 유튜브 기준(-14 LUFS)에 맞춰 라우드니스를 통일한다.
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-shortest", str(out_path)],
        stdin=subprocess.PIPE,
    )

    def scene_at(t: float) -> int:
        for index, mark in enumerate(marks):
            if t < mark["start"] + mark["duration"]:
                return index
        return len(marks) - 1

    cache: dict[tuple[int, int], Image.Image] = {}

    def frame_of(index: int, t: float) -> Image.Image:
        mark = marks[index]
        progress = min(max((t - mark["start"]) / mark["duration"], 0.0), 1.0)
        key = (index, int(progress * 1000))
        if key not in cache:
            if len(cache) > 4:
                cache.clear()
            cache[key] = kenburns(plates[index], progress, design, index)
        return cache[key]

    try:
        for frame_index in range(total_frames):
            t = frame_index / fps
            index = scene_at(t)
            mark = marks[index]
            image = frame_of(index, t)

            local = t - mark["start"]
            if index > 0 and local < CROSSFADE:
                image = Image.blend(frame_of(index - 1, t), image, local / CROSSFADE)

            canvas = Image.alpha_composite(image.convert("RGBA"), header)

            alpha = min(local / SUB_FADE, 1.0) if local < SUB_FADE else 1.0
            remaining = mark["duration"] - local
            if remaining < SUB_FADE:
                alpha = min(alpha, max(remaining, 0.0) / SUB_FADE)
            if alpha > 0.01:
                sub = subs[index]
                if alpha < 0.99:
                    sub = sub.copy()
                    sub.putalpha(sub.getchannel("A").point(
                        lambda v, a=alpha: int(v * a)))
                canvas = Image.alpha_composite(canvas, sub)

            proc.stdin.write(canvas.convert("RGB").tobytes())
            if frame_index % 240 == 0:
                print(f"  {frame_index}/{total_frames}")
    finally:
        proc.stdin.close()
        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패 (exit {proc.returncode})")

    save_json(base / "out" / "credits.json",
              [{"scene": m["id"], **(c or {})} for m, c in zip(marks, credits)])
    lines = ["# 이미지 출처 (모두 퍼블릭 도메인 또는 CC 라이선스)", ""]
    for mark, entry in zip(marks, credits):
        if entry:
            lines.append(
                f"- {mark['id']}: {entry['title']} — {entry['artist']} / "
                f"{entry['license']} — {entry['descriptionurl']}"
            )
    (base / "out" / "credits.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n완성: {out_path}  "
          f"({duration:.1f}초, {out_path.stat().st_size / 1e6:.1f}MB)")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode")
    parser.add_argument("--out", default="video.mp4")
    args = parser.parse_args()
    render(args.episode, args.out)
