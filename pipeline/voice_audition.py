"""Typecast 화자 후보를 합성해 비교한다.

「차분하고 신뢰감 있는」 목소리는 결국 귀로 정해야 하지만, 후보를 좁히는 데
쓸 수 있는 값은 잰다. 낮은 기본주파수(남성역), 과하지 않은 발화 속도, 작은
억양 변동폭 세 가지다. 결과 wav 를 듣고 최종 선택한다.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.common import ROOT, ffmpeg_exe, load_config  # noqa: E402

SR = 16000
SAMPLE_TEXT = "같은 제도를 두고 한 나라는 지웠고, 한 나라는 남겼습니다."

# 성숙한 남성 내레이터로 흔히 쓰이는 이름 위주로 추렸다.
DEFAULT_CANDIDATES = [
    "Byunghun", "Deokhwan", "Dohan", "Geunseok", "Gunseok", "Hanjun",
    "Jaeho", "Jangho", "Jinwoo", "Joonghyun", "Junho", "Myungil",
    "Sanghyun", "Seungho", "Sungho", "Wonho", "Woosung", "Younghwan",
]


def synthesize(api_key: str, voice_id: str, model: str, text: str, dest: Path) -> None:
    payload = {
        "voice_id": voice_id,
        "text": text,
        "model": model,
        "language": "kor",
        "prompt": {"emotion_preset": "normal", "emotion_intensity": 1},
        "output": {"volume": 100, "audio_pitch": 0, "audio_tempo": 1,
                   "audio_format": "wav"},
        "seed": 42,
    }
    resp = requests.post(
        "https://api.typecast.ai/v1/text-to-speech",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json=payload, timeout=120,
    )
    if resp.status_code >= 400 or not resp.content.startswith(b"RIFF"):
        raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
    dest.write_bytes(resp.content)


def _mono(path: Path) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path),
         "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


def measure(path: Path) -> dict:
    """자기상관으로 유성음 구간의 기본주파수를 재고, 속도·변동폭을 낸다."""
    signal = _mono(path)
    duration = len(signal) / SR
    frame, hop = int(0.040 * SR), int(0.010 * SR)
    lo, hi = SR // 320, SR // 70            # 70~320Hz 를 사람 목소리로 본다

    pitches, voiced = [], 0
    total = 0
    for start in range(0, len(signal) - frame, hop):
        chunk = signal[start:start + frame]
        energy = np.sqrt(np.mean(chunk ** 2))
        total += 1
        if energy < 0.012:
            continue
        chunk = chunk - chunk.mean()
        corr = np.correlate(chunk, chunk, mode="full")[frame - 1:]
        if corr[0] <= 0:
            continue
        segment = corr[lo:hi]
        if not len(segment):
            continue
        lag = int(np.argmax(segment)) + lo
        if corr[lag] / corr[0] < 0.32:      # 주기성이 약하면 무성음으로 본다
            continue
        pitches.append(SR / lag)
        voiced += 1

    if not pitches:
        return {"f0": 0.0, "spread": 0.0, "duration": duration,
                "cps": 0.0, "voiced": 0.0}
    pitches = np.array(pitches)
    return {
        "f0": float(np.median(pitches)),
        "spread": float(np.percentile(pitches, 75) - np.percentile(pitches, 25)),
        "duration": duration,
        "cps": len(SAMPLE_TEXT) / duration,
        "voiced": voiced / max(total, 1),
    }


def audition(names: list[str], text: str = SAMPLE_TEXT) -> list[dict]:
    config = load_config()
    api_key = config["typecast"]["api_key"]
    model = config["typecast"].get("model", "ssfm-v30")

    catalog = requests.get(
        f"https://api.typecast.ai/v1/voices?model={model}",
        headers={"X-API-KEY": api_key}, timeout=30,
    ).json()
    by_name = {v["voice_name"]: v["voice_id"] for v in catalog}

    out_dir = ROOT / "voice_samples"
    out_dir.mkdir(exist_ok=True)

    results = []
    for name in names:
        voice_id = by_name.get(name)
        if not voice_id:
            print(f"  ? {name}: {model} 목록에 없다")
            continue
        dest = out_dir / f"{name}.wav"
        try:
            if not dest.exists():
                synthesize(api_key, voice_id, model, text, dest)
                time.sleep(0.4)
            stats = measure(dest)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {name}: {exc}")
            continue
        results.append({"name": name, "voice_id": voice_id, **stats})
        print(f"  · {name:<12} F0 {stats['f0']:5.1f}Hz  변동 {stats['spread']:4.1f}  "
              f"{stats['cps']:4.1f}자/초  {stats['duration']:.2f}초")

    print(f"\n샘플 위치: {out_dir}")
    return results


def rank(results: list[dict]) -> list[dict]:
    """남성역·차분함 기준으로 정렬한다. 낮은 F0, 작은 변동폭, 중간 속도."""
    male = [r for r in results if 80 <= r["f0"] <= 165] or results
    speeds = np.array([r["cps"] for r in male])
    target = float(np.median(speeds))
    for entry in male:
        entry["score"] = (
            (entry["f0"] - 80) / 85 * 1.0            # 낮을수록 좋다
            + entry["spread"] / 40 * 1.4             # 억양이 덜 흔들릴수록 좋다
            + abs(entry["cps"] - target) / target * 0.8
        )
    return sorted(male, key=lambda r: r["score"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--names", nargs="*", default=DEFAULT_CANDIDATES)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    ranked = rank(audition(args.names))
    print("\n=== 차분한 남성 내레이션에 가까운 순 ===")
    for index, entry in enumerate(ranked[: args.top], 1):
        print(f"{index}. {entry['name']:<12} {entry['voice_id']}  "
              f"F0 {entry['f0']:5.1f}Hz  변동 {entry['spread']:4.1f}  "
              f"{entry['cps']:4.1f}자/초")
