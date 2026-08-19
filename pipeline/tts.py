"""장면별 나레이션을 합성하고 타임라인을 만든다.

기본은 Typecast(config.toml 의 typecast.api_key), 키가 없으면 Edge TTS 로
자동 대체한다. 장면마다 개별 파일로 합성하므로 자막 타이밍이 정확히 맞는다.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.common import (  # noqa: E402
    ffprobe_duration,
    episode_dir,
    load_config,
    load_script,
    save_json,
)

TYPECAST_BASE = "https://api.typecast.ai"

# TTS 가 헷갈리는 표기만 읽는 법으로 바꾼다(자막 원문은 그대로 둔다).
READING = [
    (r"4·19", "사일구"),
    (r"5·16", "오일륙"),
    (r"제41조", "제 사십일 조"),
    (r"제42조", "제 사십이 조"),
    (r"[\"'‘’“”]", ""),
]


def to_speech_text(scene: dict) -> str:
    text = scene.get("tts_text") or scene["ko"]
    for pattern, repl in READING:
        text = re.sub(pattern, repl, text)
    return text.strip()


# --------------------------------------------------------------------------- #
# Typecast
# --------------------------------------------------------------------------- #
def typecast_voices(api_key: str) -> list[dict]:
    resp = requests.get(
        f"{TYPECAST_BASE}/v1/voices",
        headers={"X-API-KEY": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("result", data.get("voices", []))


def typecast_tts(api_key: str, voice_id: str, model: str, text: str, dest: Path,
                 tempo: float = 1.0) -> None:
    payload = {
        "voice_id": voice_id,
        "text": text,
        "model": model,
        "language": "kor",
        "prompt": {"emotion_preset": "normal", "emotion_intensity": 1},
        "output": {"volume": 100, "audio_pitch": 0, "audio_tempo": tempo,
                   "audio_format": "wav"},
        "seed": 42,
    }
    resp = requests.post(
        f"{TYPECAST_BASE}/v1/text-to-speech",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Typecast {resp.status_code}: {resp.text[:400]}")
    dest.write_bytes(resp.content)


# --------------------------------------------------------------------------- #
# Edge TTS (대체)
# --------------------------------------------------------------------------- #
async def _edge_one(text: str, voice: str, dest: Path, rate: str = "+0%") -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice, rate=rate).save(str(dest))


def edge_tts_batch(items: list[tuple[str, Path]], voice: str, rate: str) -> None:
    async def runner():
        for text, dest in items:
            await _edge_one(text, voice, dest, rate)
            print(f"  · {dest.name}")

    asyncio.run(runner())


# --------------------------------------------------------------------------- #
# VOICEVOX (일본어)
# --------------------------------------------------------------------------- #
def voicevox_batch(script: dict, out_dir: Path) -> dict:
    """일본어 대본을 VOICEVOX 로 합성한다.

    합성하면서 실제로 읽은 카나를 모아 두었다가 readings.txt 로 남긴다.
    한자 오독은 일본 시청자 이탈의 주된 원인이라, 매번 눈으로 확인할 수
    있어야 한다.
    """
    from pipeline import voicevox

    voicevox.ensure_engine()
    voice = script["voice"]
    voicevox.register_words(script.get("dictionary", []))
    character = voice.get("character", "ずんだもん")
    style = voice.get("style", "ノーマル")
    speaker = voicevox.find_style(character, style)
    speed = float(voice.get("speed", 1.0))
    intonation = float(voice.get("intonation", 1.0))
    pitch = float(voice.get("pitch", 0.0))
    print(f"· VOICEVOX {character}／{style} (id={speaker}) speed={speed}")

    readings = []
    for scene in script["scenes"]:
        text = scene.get("tts_text") or scene["ja"]
        dest = out_dir / f"{scene['id']}.wav"
        kana = voicevox.speak(
            text, speaker, dest,
            speed=speed, pitch=pitch, intonation=intonation,
            # 장면마다 앞뒤 무음을 달리해 낭독에 호흡을 준다.
            pre=float(scene.get("pre_pause", 0.1)),
            post=float(scene.get("post_pause", 0.1)),
        )
        readings.append(f"[{scene['id']}] {text}\n       {kana}")
        print(f"  · {dest.name}")

    (out_dir / "readings.txt").write_text("\n".join(readings), encoding="utf-8")
    print(f"  읽기 확인용: {out_dir / 'readings.txt'}")
    return {"character": character, "style": style, "speaker": speaker,
            "speed": speed, "intonation": intonation, "pitch": pitch}


# --------------------------------------------------------------------------- #
def synthesize(episode_id: str, force_provider: str | None = None) -> dict:
    script = load_script(episode_id)
    config = load_config()
    voice_cfg = script["voice"]
    out_dir = episode_dir(episode_id) / "assets" / "voice"
    out_dir.mkdir(parents=True, exist_ok=True)

    api_key = (config.get("typecast") or {}).get("api_key", "")
    provider = force_provider or voice_cfg.get("provider") \
        or ("typecast" if api_key else "edge")

    used: dict = {"provider": provider}

    if provider == "voicevox":
        used |= voicevox_batch(script, out_dir)
    elif provider == "typecast":
        if not api_key:
            raise SystemExit("config.toml 에 typecast.api_key 가 없다.")
        voice_id = (config.get("typecast") or {}).get("voice_id") \
            or voice_cfg.get("typecast_voice_id", "")
        model = (config.get("typecast") or {}).get("model", "ssfm-v30")
        if not voice_id:
            # 목록에 언어·성별 정보가 없어 자동 선택은 영어 화자를 집을 수 있다.
            raise SystemExit(
                "config.toml 의 typecast.voice_id 가 비어 있다. "
                "pipeline/voice_audition.py 로 후보를 듣고 골라 채워라."
            )
        tempo = float(voice_cfg.get("typecast_tempo", 1.0))
        used |= {"voice_id": voice_id, "model": model, "tempo": tempo}
        names = {v["voice_id"]: v["voice_name"] for v in typecast_voices(api_key)}
        print(f"· Typecast {names.get(voice_id, voice_id)} / {model} / tempo {tempo}")
        for scene in script["scenes"]:
            dest = out_dir / f"{scene['id']}.wav"
            typecast_tts(api_key, voice_id, model, to_speech_text(scene), dest, tempo)
            print(f"  · {dest.name}")
    else:
        voice = voice_cfg.get("edge_voice", "ko-KR-InJoonNeural")
        rate = voice_cfg.get("edge_rate", "+0%")
        used |= {"voice_id": voice, "rate": rate}
        print(f"· Edge TTS 대체 사용: {voice}")
        edge_tts_batch(
            [(to_speech_text(s), out_dir / f"{s['id']}.mp3") for s in script["scenes"]],
            voice, rate,
        )

    ext = "mp3" if provider == "edge" else "wav"
    timeline, clock = [], 0.0
    for scene in script["scenes"]:
        path = out_dir / f"{scene['id']}.{ext}"
        speech = ffprobe_duration(path)
        hold = float(scene.get("hold", 0.35))
        timeline.append(
            {
                "id": scene["id"],
                "audio": path.name,
                "start": round(clock, 3),
                "speech": round(speech, 3),
                "hold": hold,
                "duration": round(speech + hold, 3),
            }
        )
        clock += speech + hold

    result = {"voice": used, "total": round(clock, 3), "scenes": timeline}
    save_json(out_dir / "timeline.json", result)
    print(f"\n총 길이: {clock:.1f}초 ({len(timeline)}장면)")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode")
    parser.add_argument("--provider", choices=["voicevox", "typecast", "edge"])
    args = parser.parse_args()
    synthesize(args.episode, args.provider)
