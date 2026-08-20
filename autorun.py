"""대기 중인 회차를 하나 골라 만들고 비공개로 올린다.

윈도우 작업 스케줄러가 1시간마다 이 파일을 부른다. 한 번 부를 때마다
**한 편만** 처리하고 끝낸다.

    python autorun.py              # 한 편 처리
    python autorun.py --status     # 큐 상태만 보기
    python autorun.py --dry-run    # 만들되 올리지는 않음

대본(script.json)은 사람이 미리 써 둔다. 수치에 출처를 달아야 렌더가 통과하는
구조라서, 대본까지 자동으로 지어내면 그 장치가 무의미해진다. 이 실행기는
「이미 검증된 대본을 영상으로 만들어 올리는」 부분만 맡는다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline.common import EPISODES, ROOT, load_config, load_script  # noqa: E402

STATE_PATH = ROOT / "autorun_state.json"
LOG_PATH = ROOT / "autorun.log"
ENGINE_URL = "http://127.0.0.1:50021"
ENGINE_EXE = Path.home() / "voicevox_engine" / "windows-cpu" / "run.exe"

# 업로드 1건에 약 1,600 유닛이 든다. 기본 일일 한도가 10,000 이라 6건이 상한이다.
# 그와 별개로, 짧은 간격의 대량 투고는 「양산형」 신호로 읽힌다는 지적이 있어
# 실제 운용값은 그보다 낮게 잡는 것이 안전하다.
DAILY_CAP = 4


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")


# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"uploaded": {}, "daily": {}, "failed": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def pending(state: dict) -> list[str]:
    """대본은 있는데 아직 안 올라간 회차. 회차 번호 순으로 돌려준다."""
    done = set(state.get("uploaded", {}))
    ready = []
    for path in sorted(EPISODES.glob("*/script.json")):
        name = path.parent.name
        if name in done:
            continue
        # 세 번 넘게 실패한 회차는 사람이 볼 때까지 건너뛴다.
        if state.get("failed", {}).get(name, 0) >= 3:
            continue
        try:
            script = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        ready.append((int(script.get("episode", 999)), name))
    return [name for _, name in sorted(ready)]


# --------------------------------------------------------------------------- #
def engine_alive() -> bool:
    try:
        urllib.request.urlopen(f"{ENGINE_URL}/version", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_engine() -> bool:
    """VOICEVOX 엔진이 꺼져 있으면 띄운다. 스케줄러가 부를 때는 아무도 없다."""
    if engine_alive():
        return True
    if not ENGINE_EXE.exists():
        log(f"엔진 실행 파일이 없다: {ENGINE_EXE}")
        return False
    log("엔진이 꺼져 있어 새로 띄운다")
    subprocess.Popen(
        [str(ENGINE_EXE), "--host", "127.0.0.1", "--port", "50021"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(60):                       # 모델을 읽는 데 시간이 걸린다
        time.sleep(2)
        if engine_alive():
            log("엔진 기동 확인")
            return True
    log("엔진이 시간 안에 뜨지 않았다")
    return False


def needs_images(episode_id: str) -> bool:
    script = load_script(episode_id)
    return any(scene.get("visual", {}).get("type") == "photo"
               for scene in script["scenes"])


# --------------------------------------------------------------------------- #
def run_once(dry_run: bool = False) -> int:
    state = load_state()
    count = state.setdefault("daily", {}).get(today(), 0)
    if count >= DAILY_CAP:
        log(f"오늘 {count}건을 올려 한도({DAILY_CAP})에 닿았다. 건너뛴다")
        return 0

    queue = pending(state)
    if not queue:
        log("대기 중인 회차가 없다. 대본을 추가해라")
        return 0

    episode_id = queue[0]
    log(f"대기 {len(queue)}편 중 {episode_id} 를 처리한다")

    if not ensure_engine():
        return 1

    steps = ["voice", "bgm", "render", "thumb"]
    if needs_images(episode_id):
        steps.insert(0, "images")

    try:
        subprocess.run(
            [sys.executable, str(ROOT / "make.py"), episode_id, "--only", *steps],
            cwd=ROOT, check=True,
        )
    except subprocess.CalledProcessError as exc:
        state.setdefault("failed", {})[episode_id] = \
            state.get("failed", {}).get(episode_id, 0) + 1
        save_state(state)
        log(f"제작 실패 {episode_id} (exit {exc.returncode}) — "
            f"누적 {state['failed'][episode_id]}회")
        return 1

    if dry_run:
        log(f"--dry-run 이라 {episode_id} 를 올리지 않았다")
        return 0

    from pipeline.upload_youtube import upload

    try:
        url = upload(episode_id, privacy="private")
    except Exception as exc:  # noqa: BLE001
        state.setdefault("failed", {})[episode_id] = \
            state.get("failed", {}).get(episode_id, 0) + 1
        save_state(state)
        log(f"업로드 실패 {episode_id}: {exc}")
        return 1

    state["uploaded"][episode_id] = {
        "url": url,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    state["daily"][today()] = count + 1
    state.get("failed", {}).pop(episode_id, None)
    save_state(state)
    log(f"완료 {episode_id} → {url}  (오늘 {count + 1}/{DAILY_CAP})")
    return 0


def show_status() -> None:
    state = load_state()
    queue = pending(state)
    print(f"업로드 완료 : {len(state.get('uploaded', {}))}편")
    for name, info in state.get("uploaded", {}).items():
        print(f"   {name:<20} {info['url']}  {info['at'][:16]}")
    print(f"\n대기 중     : {len(queue)}편")
    for name in queue:
        print(f"   {name}")
    failed = state.get("failed", {})
    if failed:
        print("\n실패 누적   :")
        for name, times in failed.items():
            mark = "  ← 3회 이상, 건너뜀" if times >= 3 else ""
            print(f"   {name:<20} {times}회{mark}")
    print(f"\n오늘 업로드 : {state.get('daily', {}).get(today(), 0)}/{DAILY_CAP}")
    print(f"엔진        : {'가동 중' if engine_alive() else '정지'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        sys.exit(run_once(args.dry_run))
