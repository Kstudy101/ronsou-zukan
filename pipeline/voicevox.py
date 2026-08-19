"""VOICEVOX 로컬 엔진으로 일본어 나레이션을 만든다.

합성 전에 **읽는 법(카나)을 먼저 뽑아 확인**하는 것이 이 모듈의 핵심이다.
일본 시청자가 합성음성 영상을 이탈하는 가장 큰 이유가 한자 오독이기 때문에,
대본을 고칠 때마다 `check` 로 읽기를 눈으로 확인하고, 틀린 것은 사용자 사전에
등록해 고친다.

    python pipeline/voicevox.py check ep01_toiletpaper    # 읽기만 확인
    python pipeline/voicevox.py speakers                  # 화자 목록
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.common import load_config, load_script  # noqa: E402

DEFAULT_BASE = "http://127.0.0.1:50021"
ENGINE_EXE = Path.home() / "voicevox_engine" / "windows-cpu" / "run.exe"


def base_url() -> str:
    return (load_config().get("voicevox") or {}).get("base_url", DEFAULT_BASE)


# --------------------------------------------------------------------------- #
# 엔진 통신
# --------------------------------------------------------------------------- #
def _request(method: str, path: str, params: dict | None = None,
             body=None, raw: bool = False, timeout: int = 120):
    url = base_url() + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else (
        b"" if method == "POST" else None)
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return payload if raw else json.loads(payload)


def is_alive() -> bool:
    try:
        _request("GET", "/version", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_engine() -> None:
    """엔진이 안 떠 있으면 안내한다(자동 기동은 하지 않는다)."""
    if is_alive():
        return
    raise SystemExit(
        "VOICEVOX 엔진이 응답하지 않는다. 아래를 실행해 띄운 뒤 다시 돌려라:\n"
        f'  "{ENGINE_EXE}" --host 127.0.0.1 --port 50021'
    )


def speakers() -> list[dict]:
    return _request("GET", "/speakers", timeout=30)


def find_style(character: str, style: str = "ノーマル") -> int:
    """캐릭터 이름과 스타일로 speaker id 를 찾는다."""
    for entry in speakers():
        if entry["name"] == character:
            for item in entry["styles"]:
                if item["name"] == style:
                    return item["id"]
            raise SystemExit(
                f"{character} 에 '{style}' 스타일이 없다. "
                f"가능: {[s['name'] for s in entry['styles']]}"
            )
    raise SystemExit(f"화자를 찾지 못했다: {character}")


# --------------------------------------------------------------------------- #
# 사용자 사전 — 오독 교정
# --------------------------------------------------------------------------- #
def register_words(entries: list[dict]) -> None:
    """script.json 의 dictionary 블록을 엔진 사용자 사전에 등록한다.

    항목: {"surface": "真っ二つ", "pronunciation": "マップタツ", "accent_type": 4}
    발음은 전각 가타카나여야 한다.
    """
    for entry in entries:
        params = {
            "surface": entry["surface"],
            "pronunciation": entry["pronunciation"],
            "accent_type": entry.get("accent_type", 0),
        }
        if "word_type" in entry:
            params["word_type"] = entry["word_type"]
        try:
            _request("POST", "/user_dict_word", params=params, timeout=30)
            print(f"  사전 등록: {entry['surface']} → {entry['pronunciation']}")
        except urllib.error.HTTPError as exc:
            print(f"  ! 사전 등록 실패 {entry['surface']}: {exc.read()[:200]!r}")


# --------------------------------------------------------------------------- #
# 합성
# --------------------------------------------------------------------------- #
def audio_query(text: str, speaker: int) -> dict:
    return _request("POST", "/audio_query", params={"text": text, "speaker": speaker})


def reading(text: str, speaker: int) -> str:
    """합성하지 않고 읽는 법만 얻는다. 대본 검수용."""
    return audio_query(text, speaker)["kana"]


def tune(query: dict, *, speed: float = 1.0, pitch: float = 0.0,
         intonation: float = 1.0, pre: float = 0.1, post: float = 0.1) -> dict:
    """프로소디를 손으로 만진다.

    합성음성이 「기계적이고 지루하다」는 평을 듣는 주된 원인이 균일한 간격이다.
    문장 앞뒤 무음을 장면마다 다르게 주면 낭독에 호흡이 생긴다.
    """
    query = dict(query)
    query["speedScale"] = speed
    query["pitchScale"] = pitch
    query["intonationScale"] = intonation
    query["prePhonemeLength"] = pre
    query["postPhonemeLength"] = post
    query["outputSamplingRate"] = 44100
    query["outputStereo"] = False
    return query


def synthesize(query: dict, speaker: int, dest: Path) -> None:
    wav = _request("POST", "/synthesis", params={"speaker": speaker},
                   body=query, raw=True, timeout=300)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(wav)


def speak(text: str, speaker: int, dest: Path, **tuning) -> str:
    """한 줄을 합성하고, 실제로 읽은 카나를 돌려준다."""
    query = audio_query(text, speaker)
    kana = query["kana"]
    synthesize(tune(query, **tuning), speaker, dest)
    return kana


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cmd_speakers() -> None:
    ensure_engine()
    for entry in speakers():
        styles = ", ".join(f"{s['name']}({s['id']})" for s in entry["styles"])
        print(f"{entry['name']:<20} {styles}")


def _cmd_check(episode_id: str) -> None:
    """대본 전체의 읽는 법을 뽑아 보여준다. 합성은 하지 않는다."""
    ensure_engine()
    script = load_script(episode_id)
    voice = script["voice"]
    register_words(script.get("dictionary", []))
    speaker = find_style(voice.get("character", "ずんだもん"),
                         voice.get("style", "ノーマル"))
    print(f"\n화자: {voice.get('character')} / {voice.get('style')} (id={speaker})\n")
    for scene in script["scenes"]:
        text = scene.get("tts_text") or scene["ja"]
        print(f"[{scene['id']}] {text}")
        print(f"       {reading(text, speaker)}\n")
    print("한자 오독이 보이면 script.json 의 dictionary 에 추가하고 다시 확인해라.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("speakers")
    checker = sub.add_parser("check")
    checker.add_argument("episode")
    args = parser.parse_args()

    if args.cmd == "speakers":
        _cmd_speakers()
    else:
        _cmd_check(args.episode)
