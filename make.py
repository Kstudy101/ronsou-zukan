"""한일 비교 쇼츠를 한 번에 만든다.

    python make.py ep01_bicameral                 # 전체
    python make.py ep01_bicameral --skip images   # 이미지는 이미 받았을 때
    python make.py ep01_bicameral --only render   # 렌더만 다시

단계: images(위키미디어 수집) → voice(TTS) → bgm(합성) → render(영상)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import bgm, fetch_images, render, thumbnail, tts  # noqa: E402
from pipeline.common import episode_dir  # noqa: E402

STEPS = ("images", "voice", "bgm", "render", "thumb")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("episode")
    parser.add_argument("--skip", nargs="*", default=[], choices=STEPS)
    parser.add_argument("--only", nargs="*", default=[], choices=STEPS)
    parser.add_argument("--provider", choices=["voicevox", "typecast", "edge"],
                        help="TTS 제공자를 강제한다. 기본은 키가 있으면 typecast")
    parser.add_argument("--out", default="video.mp4")
    args = parser.parse_args()

    todo = [s for s in STEPS if (not args.only or s in args.only) and s not in args.skip]
    base = episode_dir(args.episode)

    if "images" in todo:
        print("\n[1/5] 이미지 수집 (위키미디어 공용, PD·CC 만)")
        fetch_images.collect(args.episode, per_scene=3)

    if "voice" in todo:
        print("\n[2/5] 나레이션 합성")
        tts.synthesize(args.episode, args.provider)

    timeline_path = base / "assets" / "voice" / "timeline.json"
    total = json.loads(timeline_path.read_text(encoding="utf-8"))["total"]

    if "bgm" in todo:
        print("\n[3/5] 배경음악 합성")
        design = json.loads((base / "script.json").read_text(encoding="utf-8"))
        style = design.get("design", {}).get("bgm_style", "documentary")
        bgm.build(args.episode, total + 0.6, style)

    if "render" in todo:
        print("\n[4/5] 영상 렌더")
        render.render(args.episode, args.out)

    if "thumb" in todo:
        print("\n[5/5] 섬네일")
        thumbnail.build(args.episode)


if __name__ == "__main__":
    main()
