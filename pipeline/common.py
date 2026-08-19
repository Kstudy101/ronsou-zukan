"""공통 경로·설정 로더."""
from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # hanil/
EPISODES = ROOT / "episodes"
CONFIG_PATH = ROOT / "config.toml"

USER_AGENT = (
    "HanilLawShorts/1.0 (educational Korea-Japan law comparison shorts; "
    "contact via repo owner) python-requests"
)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("rb") as fp:
        return tomllib.load(fp)


def episode_dir(episode_id: str) -> Path:
    return EPISODES / episode_id


def load_script(episode_id: str) -> dict:
    path = episode_dir(episode_id) / "script.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def ffprobe_duration(path: str | Path) -> float:
    """ffprobe가 없는 배포판이므로 ffmpeg로 디코딩해 길이를 잰다."""
    out = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stderr
    last = ""
    for line in out.splitlines():
        if "time=" in line:
            last = line
    if not last:
        raise RuntimeError(f"길이를 측정하지 못했다: {path}")
    token = last.rsplit("time=", 1)[1].split()[0]
    hh, mm, ss = token.split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


def run(cmd: list[str], desc: str = "") -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise RuntimeError(f"{desc or cmd[0]} 실패 (exit {proc.returncode}):\n{tail}")
