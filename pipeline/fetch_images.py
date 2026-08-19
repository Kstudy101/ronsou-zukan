"""위키미디어 공용에서 장면별 역사 이미지를 수집한다.

퍼블릭 도메인 / CC0 / CC BY / CC BY-SA 만 통과시키고, 저작자·라이선스·원본
링크를 credits.json 에 남긴다. 비자유(공정이용) 파일은 받지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.common import USER_AGENT, episode_dir, load_script, save_json  # noqa: E402

API = "https://commons.wikimedia.org/w/api.php"

ALLOWED = re.compile(
    r"^(pd|public\s*domain|cc0|cc[\-\s]?by(\-sa)?(\-\d(\.\d)?)?|"
    r"cc[\-\s]?pd|attribution)", re.I
)
BLOCKED = re.compile(r"fair\s*use|non[\-\s]?free|no\s*derivative|nc\b|noncommercial", re.I)

MIN_WIDTH = 800
MIN_HEIGHT = 600


def filename_for(title: str) -> str:
    """공용 파일 제목에서 저장 파일명을 만든다.

    순번(s01_c0)으로 저장하면 재수집 때 후보 순서가 바뀌면서 예전에 받아 둔
    다른 사진이 그대로 재사용된다. 그러면 화면에 나가는 사진과 출처 표기가
    어긋난다. 제목에서 이름을 만들면 그런 어긋남이 생기지 않는다.
    """
    name = title.removeprefix("File:").rsplit(".", 1)[0]
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    slug = re.sub(r"[^0-9A-Za-z가-힣ぁ-んァ-ヶ一-龥]+", "-", name).strip("-")[:48]
    return f"{slug or 'img'}_{digest}"


def _is_valid_image(path: Path) -> bool:
    """실제로 디코딩되는 이미지인지 확인한다(중단된 다운로드·오류 페이지 걸러내기)."""
    if not path.exists() or path.stat().st_size < 10_000:
        return False
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:  # noqa: BLE001
        return False

# 위키미디어는 익명 대량 요청을 조인다. 요청 간 간격을 두고, 429 는 백오프한다.
THROTTLE_SEC = 2.0
MAX_RETRY = 5


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.headers["Accept-Encoding"] = "gzip"
    return s


_last_call = 0.0


def _polite_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """요청 간격을 지키고 429/503 은 지수 백오프로 재시도한다."""
    global _last_call
    for attempt in range(MAX_RETRY):
        gap = THROTTLE_SEC - (time.monotonic() - _last_call)
        if gap > 0:
            time.sleep(gap)
        resp = session.get(url, **kwargs)
        _last_call = time.monotonic()
        if resp.status_code not in (429, 503):
            resp.raise_for_status()
            return resp
        wait = float(resp.headers.get("Retry-After") or 0) or (2.0 * (2 ** attempt))
        print(f"    (대기 {wait:.0f}s — {resp.status_code})")
        time.sleep(wait)
    resp.raise_for_status()
    return resp


def _meta(entry: dict, key: str) -> str:
    value = entry.get("extmetadata", {}).get(key, {}).get("value", "")
    return re.sub(r"<[^>]+>", "", value).strip()


def _license_ok(entry: dict) -> tuple[bool, str]:
    short = _meta(entry, "LicenseShortName")
    ident = _meta(entry, "License")
    terms = _meta(entry, "UsageTerms")
    blob = " ".join([short, ident, terms])
    if BLOCKED.search(blob):
        return False, short or ident
    for candidate in (ident, short):
        if candidate and ALLOWED.match(candidate.strip()):
            return True, short or ident
    return False, short or ident


def search(session: requests.Session, query: str, limit: int = 12) -> list[dict]:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size|mime",
        "iiurlwidth": "1600",
    }
    resp = _polite_get(session, API, params=params, timeout=30)
    pages = resp.json().get("query", {}).get("pages", {})
    results = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        if not info:
            continue
        if info.get("mime") not in {"image/jpeg", "image/png"}:
            continue
        if info.get("width", 0) < MIN_WIDTH or info.get("height", 0) < MIN_HEIGHT:
            continue
        ok, license_name = _license_ok(info)
        if not ok:
            continue
        results.append(
            {
                "title": page.get("title", ""),
                "url": info.get("thumburl") or info.get("url"),
                "descriptionurl": info.get("descriptionurl", ""),
                "width": info.get("width"),
                "height": info.get("height"),
                "license": license_name,
                "artist": _meta(info, "Artist") or "unknown",
                "credit": _meta(info, "Credit"),
                "description": _meta(info, "ImageDescription")[:300],
                "query": query,
            }
        )
    return results


def by_titles(session: requests.Session, titles: list[str]) -> list[dict]:
    """지정한 공용 파일명을 그대로 가져온다(검색보다 정확도가 높다)."""
    if not titles:
        return []
    params = {
        "action": "query",
        "format": "json",
        "titles": "|".join(t if t.startswith("File:") else f"File:{t}" for t in titles),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size|mime",
        "iiurlwidth": "1600",
    }
    resp = _polite_get(session, API, params=params, timeout=30)
    order = {t.removeprefix("File:").replace("_", " "): i for i, t in enumerate(titles)}
    results = []
    for page in resp.json().get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        if not info:
            print(f"  ! 파일 없음: {page.get('title')}")
            continue
        ok, license_name = _license_ok(info)
        if not ok:
            print(f"  ! 라이선스 부적합, 제외: {page.get('title')} [{license_name}]")
            continue
        results.append(
            {
                "title": page.get("title", ""),
                "url": info.get("thumburl") or info.get("url"),
                "descriptionurl": info.get("descriptionurl", ""),
                "width": info.get("width"),
                "height": info.get("height"),
                "license": license_name,
                "artist": _meta(info, "Artist") or "unknown",
                "credit": _meta(info, "Credit"),
                "description": _meta(info, "ImageDescription")[:300],
                "query": "(지정)",
            }
        )
    results.sort(key=lambda r: order.get(r["title"].removeprefix("File:"), 99))
    return results


def collect(episode_id: str, per_scene: int = 4) -> dict:
    script = load_script(episode_id)
    out_dir = episode_dir(episode_id) / "assets" / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    session = _session()

    manifest: dict[str, list[dict]] = {}
    for scene in script["scenes"]:
        sid = scene["id"]
        picked: list[dict] = []
        seen: set[str] = set()
        for hit in by_titles(session, scene.get("files", [])):
            seen.add(hit["title"])
            picked.append(hit)
        for query in scene.get("queries", []):
            for hit in search(session, query):
                if hit["title"] in seen:
                    continue
                seen.add(hit["title"])
                picked.append(hit)
                if len(picked) >= per_scene:
                    break
            if len(picked) >= per_scene:
                break

        saved = []
        for hit in picked:
            ext = ".png" if hit["url"].lower().endswith(".png") else ".jpg"
            dest = out_dir / f"{filename_for(hit['title'])}{ext}"
            try:
                if _is_valid_image(dest):
                    print(f"  = {sid} 이미 있음 {dest.name}")
                else:
                    blob = _polite_get(session, hit["url"], timeout=60)
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    tmp.write_bytes(blob.content)
                    if not _is_valid_image(tmp):
                        tmp.unlink(missing_ok=True)
                        raise RuntimeError("내려받은 파일이 온전한 이미지가 아니다")
                    tmp.replace(dest)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! 내려받기 실패 {hit['title']}: {exc}")
                continue
            hit["file"] = dest.name
            saved.append(hit)
            print(f"  · {sid} <- {hit['title']}  [{hit['license']}]")
        manifest[sid] = saved
        if not saved:
            print(f"  !! {sid}: 조건을 만족하는 이미지를 찾지 못했다")

    save_json(episode_dir(episode_id) / "assets" / "images" / "candidates.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode")
    parser.add_argument("--per-scene", type=int, default=4)
    args = parser.parse_args()
    collect(args.episode, args.per_scene)
