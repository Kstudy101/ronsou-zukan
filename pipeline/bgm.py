"""배경음악을 직접 합성한다.

외부 음원을 쓰지 않고 파형을 계산해 만들기 때문에 제3자의 저작권·인접권이
없다. Content ID 클레임 위험도 없다. 차분한 다큐멘터리 톤을 목표로 한다.
"""
from __future__ import annotations

import argparse
import struct
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.common import episode_dir  # noqa: E402

SR = 44100


def note(semitones_from_a4: float) -> float:
    return 440.0 * 2 ** (semitones_from_a4 / 12.0)


# A단조. 반음 단위(A4=0) — Am, F, C, G 의 구성음.
CHORDS = [
    ("Am", [-12, 0, 3, 7]),      # A  C  E
    ("F",  [-16, -4, 0, 5]),     # F  A  C
    ("C",  [-21, -9, -5, 0]),    # C  E  G
    ("G",  [-14, -2, 2, 7]),     # G  B  D
]


def _envelope(n: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n)
    # 마지막 마디가 짧을 수 있으므로 어택·릴리스를 길이 안에 가둔다.
    a = max(1, min(int(attack * SR), n // 2))
    r = max(1, min(int(release * SR), n - a))
    env[:a] = np.linspace(0, 1, a) ** 2
    env[-r:] = np.linspace(1, 0, r) ** 2
    return env


def pad(duration: float, semis: list[float]) -> np.ndarray:
    """느리게 부풀었다 사그라드는 패드."""
    n = int(duration * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for s in semis:
        f = note(s)
        for partial, gain in ((1, 1.0), (2, 0.28), (3, 0.12), (4, 0.06)):
            detune = 1 + 0.0016 * ((partial % 3) - 1)
            out += gain * np.sin(2 * np.pi * f * partial * detune * t)
    lfo = 0.86 + 0.14 * np.sin(2 * np.pi * 0.07 * t)
    return out / (len(semis) * 1.5) * lfo * _envelope(n, 1.6, 2.2)


def pluck(duration: float, semis_seq: list[tuple[float, float]]) -> np.ndarray:
    """감쇠가 빠른 건반풍 음. (시작초, 반음) 목록."""
    out = np.zeros(int(duration * SR))
    for start, s in semis_seq:
        f = note(s)
        length = min(3.2, duration - start)
        if length <= 0:
            continue
        n = int(length * SR)
        t = np.arange(n) / SR
        body = (
            np.sin(2 * np.pi * f * t)
            + 0.35 * np.sin(2 * np.pi * f * 2 * t)
            + 0.14 * np.sin(2 * np.pi * f * 3 * t)
            + 0.05 * np.sin(2 * np.pi * f * 4.2 * t)
        )
        body *= np.exp(-t * 1.9) * (1 - np.exp(-t * 260))
        i = int(start * SR)
        out[i:i + n] += body[: len(out) - i]
    return out * 0.30


def bass(duration: float, roots: list[tuple[float, float, float]]) -> np.ndarray:
    """(시작초, 길이, 반음) 저역."""
    out = np.zeros(int(duration * SR))
    for start, length, s in roots:
        n = int(length * SR)
        t = np.arange(n) / SR
        f = note(s)
        tone = np.sin(2 * np.pi * f * t) + 0.2 * np.sin(2 * np.pi * f * 2 * t)
        tone *= _envelope(n, 0.7, 1.1)
        i = int(start * SR)
        out[i:i + n] += tone[: len(out) - i]
    return out * 0.34


def air(duration: float) -> np.ndarray:
    """아주 낮은 레벨의 공기음. 정적을 덜 허전하게 한다."""
    n = int(duration * SR)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(n)
    kernel = np.exp(-np.linspace(0, 6, 900))
    kernel /= kernel.sum()
    smooth = np.convolve(noise, kernel, mode="same")
    t = np.arange(n) / SR
    return smooth * 0.02 * (0.6 + 0.4 * np.sin(2 * np.pi * 0.045 * t))


def reverb(signal: np.ndarray, seconds: float = 1.8, mix: float = 0.34) -> np.ndarray:
    """지수 감쇠 노이즈를 임펄스 응답으로 삼은 FFT 합성곱 잔향."""
    n = int(seconds * SR)
    rng = np.random.default_rng(3)
    impulse = rng.standard_normal(n) * np.exp(-np.linspace(0, 7, n))
    impulse[: int(0.02 * SR)] = 0
    impulse /= np.abs(impulse).sum()
    size = 1 << int(np.ceil(np.log2(len(signal) + n)))
    wet = np.fft.irfft(
        np.fft.rfft(signal, size) * np.fft.rfft(impulse, size), size
    )[: len(signal)]
    peak = np.abs(wet).max() or 1.0
    return (1 - mix) * signal + mix * wet / peak * (np.abs(signal).max() or 1.0)


# --------------------------------------------------------------------------- #
# 잡학·논쟁 채널용 밝은 스타일
# --------------------------------------------------------------------------- #
# C장조. 잡학물에서 흔한 「궁금함 → 밝은 해결」 감각을 노린다.
BRIGHT_CHORDS = [
    ("C",  [-9, 3, 7, 12]),      # C  E  G
    ("Am", [-12, 0, 4, 7]),      # A  C  E
    ("F",  [-16, 0, 5, 8]),      # F  A  C
    ("G",  [-14, 2, 7, 11]),     # G  B  D
]


def music_box(duration: float, semis_seq: list[tuple[float, float]]) -> np.ndarray:
    """오르골·마림바 계열의 짧고 맑은 음. 잡학물 배경에 잘 붙는다."""
    out = np.zeros(int(duration * SR))
    for start, s in semis_seq:
        f = note(s)
        length = min(1.6, duration - start)
        if length <= 0:
            continue
        n = int(length * SR)
        t = np.arange(n) / SR
        # 홀수 배음 위주 + 빠른 감쇠 = 금속성 타현음
        body = (
            np.sin(2 * np.pi * f * t)
            + 0.42 * np.sin(2 * np.pi * f * 3.01 * t)
            + 0.18 * np.sin(2 * np.pi * f * 5.02 * t)
            + 0.09 * np.sin(2 * np.pi * f * 7.1 * t)
        )
        body *= np.exp(-t * 5.2) * (1 - np.exp(-t * 900))
        i = int(start * SR)
        out[i:i + n] += body[: len(out) - i]
    return out * 0.26


def tick(duration: float, beats: list[float]) -> np.ndarray:
    """아주 짧은 노이즈 클릭. 박자를 잡아 주되 드럼처럼 들리지는 않게."""
    out = np.zeros(int(duration * SR))
    rng = np.random.default_rng(11)
    n = int(0.03 * SR)
    t = np.arange(n) / SR
    grain = rng.standard_normal(n) * np.exp(-t * 180)
    for start in beats:
        i = int(start * SR)
        if i + n < len(out):
            out[i:i + n] += grain
    return out * 0.05


def compose_bright(duration: float) -> np.ndarray:
    bar = 3.43                                  # 약 112 BPM 기준 한 마디
    total = int(duration * SR)
    mix = np.zeros(total)
    beats, plucks = [], []

    for index in range(int(np.ceil(duration / bar))):
        name, semis = BRIGHT_CHORDS[index % len(BRIGHT_CHORDS)]
        start = index * bar
        length = min(bar + 0.9, duration - start)
        if length <= 0.4:
            break
        i = int(start * SR)

        layer = pad(length, semis[1:])
        mix[i:i + len(layer)] += layer[: total - i] * 0.30

        layer = bass(length, [(0.0, min(length, bar), semis[0] - 12)])
        mix[i:i + len(layer)] += layer[: total - i] * 0.8

        # 8분음표 아르페지오. 마디마다 방향을 바꿔 단조로움을 던다.
        steps = [0, 1, 2, 3, 2, 1] if index % 2 == 0 else [3, 2, 1, 0, 1, 2]
        for k, step in enumerate(steps):
            offset = start + k * (bar / 6)
            if offset < duration - 0.3:
                plucks.append((offset, semis[1:][step % 3] + 12))
                if k % 2 == 0:
                    beats.append(offset)

    mix += music_box(duration, plucks)
    mix += tick(duration, beats)
    mix += air(duration) * 0.5
    mix = reverb(mix, seconds=1.1, mix=0.22)

    fade_in, fade_out = int(1.2 * SR), int(3.0 * SR)
    mix[:fade_in] *= np.linspace(0, 1, fade_in) ** 1.4
    mix[-fade_out:] *= np.linspace(1, 0, fade_out) ** 1.4
    return mix / (np.abs(mix).max() or 1.0) * 0.72


def compose(duration: float) -> np.ndarray:
    bar = 8.0                                     # 한 코드당 8초
    total = int(duration * SR)
    mix = np.zeros(total)

    melody_offsets = [0.0, 2.0, 3.5, 5.0, 6.5]
    for index in range(int(np.ceil(duration / bar))):
        name, semis = CHORDS[index % len(CHORDS)]
        start = index * bar
        length = min(bar + 1.8, duration - start)
        if length <= 0.5:
            break
        i = int(start * SR)

        layer = pad(length, semis[1:])
        mix[i:i + len(layer)] += layer[: total - i] * 0.55

        top = semis[-1]
        seq = [(o, top + [0, 5, 7, 12, 7][k % 5]) for k, o in enumerate(melody_offsets)
               if o < length - 0.4]
        if index % 2 == 1:                        # 두 마디마다 한 번은 쉰다
            seq = seq[::2]
        layer = pluck(length, seq)
        mix[i:i + len(layer)] += layer[: total - i]

        layer = bass(length, [(0.0, min(length, bar), semis[0] - 12)])
        mix[i:i + len(layer)] += layer[: total - i]

    mix += air(duration)
    mix = reverb(mix)

    fade_in, fade_out = int(2.5 * SR), int(4.0 * SR)
    mix[:fade_in] *= np.linspace(0, 1, fade_in) ** 1.5
    mix[-fade_out:] *= np.linspace(1, 0, fade_out) ** 1.5
    return mix / (np.abs(mix).max() or 1.0) * 0.72


def write_wav(path: Path, mono: np.ndarray) -> None:
    # 좌우를 아주 살짝 어긋나게 해 폭을 준다.
    delay = int(0.011 * SR)
    left = mono
    right = np.concatenate([np.zeros(delay), mono[:-delay]]) * 0.94 + mono * 0.06
    stereo = np.stack([left, right], axis=1)
    data = np.clip(stereo, -1, 1)
    pcm = (data * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fp:
        fp.setnchannels(2)
        fp.setsampwidth(2)
        fp.setframerate(SR)
        fp.writeframes(pcm.tobytes())


STYLES = {"documentary": compose, "bright": compose_bright}


def build(episode_id: str, duration: float, style: str = "documentary") -> Path:
    if style not in STYLES:
        raise SystemExit(f"모르는 BGM 스타일: {style} (가능: {list(STYLES)})")
    dest = episode_dir(episode_id) / "assets" / "bgm" / "bgm.wav"
    write_wav(dest, STYLES[style](duration))
    print(f"BGM 생성: {dest}  ({duration:.1f}초, {style} — 직접 합성, 저작권 없음)")
    return dest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode")
    parser.add_argument("--duration", type=float, default=66.0)
    parser.add_argument("--style", default="documentary", choices=list(STYLES))
    args = parser.parse_args()
    build(args.episode, args.duration, args.style)
