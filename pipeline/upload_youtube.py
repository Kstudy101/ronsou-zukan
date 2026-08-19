"""완성한 쇼츠를 유튜브에 올린다.

YouTube Data API v3 + OAuth 데스크톱 클라이언트를 쓴다. 처음 한 번만 브라우저로
계정을 승인하고, 그 뒤로는 저장된 토큰을 갱신해 쓴다.

    python pipeline/upload_youtube.py ep01_bicameral                # 비공개로 올림
    python pipeline/upload_youtube.py ep01_bicameral --privacy public
    python pipeline/upload_youtube.py ep01_bicameral --dry-run      # 올릴 내용만 확인

제목·설명·태그는 회차의 script.json 안 `youtube` 블록에서 읽는다. 이미지 출처는
out/credits.md 를 읽어 설명 끝에 자동으로 붙이고, 업로드 뒤 고정 댓글로도 단다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.common import ROOT, episode_dir, load_config, load_script  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
DESCRIPTION_LIMIT = 5000
TITLE_LIMIT = 100
COMMENT_LIMIT = 9000


def get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    config = load_config().get("youtube", {})
    secrets = Path(config.get("client_secrets", "client_secret.json"))
    if not secrets.is_absolute():
        secrets = ROOT / secrets
    token_path = Path(config.get("token_file", "youtube_token.json"))
    if not token_path.is_absolute():
        token_path = ROOT / token_path

    if not secrets.exists():
        raise SystemExit(f"OAuth 클라이언트 파일이 없다: {secrets}")

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        print("\n" + "=" * 68)
        print("브라우저가 열린다. 업로드할 유튜브 채널의 구글 계정으로 승인해라.")
        print("열리지 않으면 아래 주소를 직접 붙여 넣어라. 최대 15분 기다린다.")
        print("=" * 68 + "\n")
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
        # 기본 대기가 60초라 사람이 승인하기 전에 끊긴다.
        creds = flow.run_local_server(
            port=0, prompt="consent", open_browser=True, timeout_seconds=900,
            authorization_prompt_message="승인 주소: {url}",
            success_message="승인됐다. 이 창을 닫아도 된다. 업로드가 이어진다.",
        )
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"· 토큰 저장: {token_path} (다음부터는 브라우저가 뜨지 않는다)")
    return build("youtube", "v3", credentials=creds)


def set_thumbnail(service, video_id: str, path: Path) -> bool:
    """맞춤 섬네일을 올린다. 채널 인증이 안 돼 있으면 거부된다."""
    from googleapiclient.http import MediaFileUpload

    if not path.exists():
        print(f"· 섬네일 파일이 없다: {path}")
        return False
    try:
        service.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(path), mimetype="image/jpeg"),
        ).execute()
        print(f"· 섬네일 적용: {path.name}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"· 섬네일 실패({exc}).\n"
              f"  채널 인증(전화 확인)이 필요하다. 유튜브 스튜디오에서 {path} 를 직접 올려라.")
        return False


def set_privacy(video_id: str, privacy: str) -> None:
    """이미 올라간 영상의 공개범위만 바꾼다."""
    service = get_service()
    service.videos().update(
        part="status",
        body={"id": video_id, "status": {"privacyStatus": privacy}},
    ).execute()
    print(f"· {video_id} → {privacy}")


def update_metadata(episode_id: str, video_id: str, privacy: str | None = None) -> None:
    """제목·설명·태그·섬네일만 갈아 끼운다(영상 파일은 교체할 수 없다)."""
    script = load_script(episode_id)
    meta = script["youtube"]
    service = get_service()

    current = service.videos().list(part="snippet,status", id=video_id).execute()
    if not current.get("items"):
        raise SystemExit(f"영상을 찾지 못했다: {video_id}")
    status = current["items"][0]["status"]

    description, _ = build_description(episode_id, meta)
    lang = meta.get("language", "ja")
    body = {
        "id": video_id,
        "snippet": {
            "title": meta["title"][:TITLE_LIMIT],
            "description": description,
            "tags": meta.get("tags", [])[:30],
            "categoryId": str(meta.get("category_id", "27")),
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": {
            "privacyStatus": privacy or status.get("privacyStatus", "private"),
            "selfDeclaredMadeForKids": bool(meta.get("made_for_kids", False)),
            "containsSyntheticMedia": True,
        },
    }
    service.videos().update(part="snippet,status", body=body).execute()
    print(f"· 메타데이터 갱신: https://youtu.be/{video_id}")
    set_thumbnail(service, video_id, episode_dir(episode_id) / "out" / "thumbnail.jpg")


def build_description(episode_id: str, meta: dict) -> tuple[str, str]:
    """설명 본문과 출처 댓글을 만든다."""
    credits_path = episode_dir(episode_id) / "out" / "credits.md"
    credits = credits_path.read_text(encoding="utf-8") if credits_path.exists() else ""

    description = meta["description"]
    if len(description) > DESCRIPTION_LIMIT:
        description = description[:DESCRIPTION_LIMIT - 3] + "..."

    # 도해를 직접 그린 회차는 표기할 외부 출처가 없다. 머리말만 있는
    # credits.md 로 빈 댓글을 달려다 실패하지 않도록, 항목이 있을 때만 만든다.
    entries = [line for line in credits.splitlines() if line.startswith("- ")]
    comment = ""
    if entries:
        comment = (credits.rstrip()
                   + "\n\n背景音楽は本動画のために合成したもので、第三者の権利はありません。")
    return description, comment[:COMMENT_LIMIT]


def upload(episode_id: str, privacy: str | None = None, video_name: str = "video.mp4",
           dry_run: bool = False) -> str | None:
    from googleapiclient.http import MediaFileUpload

    script = load_script(episode_id)
    meta = script["youtube"]
    video_path = episode_dir(episode_id) / "out" / video_name
    if not video_path.exists():
        raise SystemExit(f"영상이 없다: {video_path}  먼저 make.py 를 돌려라.")

    title = meta["title"][:TITLE_LIMIT]
    description, comment = build_description(episode_id, meta)
    status = privacy or meta.get("privacy_status", "private")
    lang = meta.get("language", "ja")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": meta.get("tags", [])[:30],
            "categoryId": str(meta.get("category_id", "27")),
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": {
            "privacyStatus": status,
            "selfDeclaredMadeForKids": bool(meta.get("made_for_kids", False)),
            # 생성형 음성을 썼으므로 합성 미디어로 신고한다.
            "containsSyntheticMedia": True,
        },
    }

    print(f"제목   : {title}  ({len(title)}자)")
    print(f"공개   : {status}")
    print(f"태그   : {', '.join(body['snippet']['tags'])}")
    print(f"파일   : {video_path.name}  ({video_path.stat().st_size / 1e6:.1f}MB)")
    print(f"설명   : {len(description)}자 / 출처 댓글 {len(comment)}자")
    if dry_run:
        print("\n--dry-run 이라 올리지 않았다.")
        return None

    service = get_service()
    media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response, progress = None, -1
    while response is None:
        chunk, response = request.next_chunk()
        if chunk and int(chunk.progress() * 100) != progress:
            progress = int(chunk.progress() * 100)
            print(f"  올리는 중 {progress}%")

    video_id = response["id"]
    url = f"https://youtu.be/{video_id}"
    print(f"\n업로드 완료: {url}")

    set_thumbnail(service, video_id, episode_dir(episode_id) / "out" / "thumbnail.jpg")

    if not comment:
        print("· 외부 출처가 없어 출처 댓글은 달지 않는다.")
        return url
    try:
        service.commentThreads().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id,
                              "topLevelComment": {"snippet": {"textOriginal": comment}}}},
        ).execute()
        print("· 출처 댓글을 달았다. 유튜브 스튜디오에서 상단 고정을 눌러라.")
    except Exception as exc:  # noqa: BLE001  댓글 실패로 업로드를 되돌릴 이유는 없다
        print(f"· 출처 댓글 실패({exc}). 설명란이나 댓글에 직접 넣어라:\n{comment[:300]}")

    return url


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"])
    parser.add_argument("--video", default="video.mp4")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update", metavar="VIDEO_ID",
                        help="새로 올리지 않고 기존 영상의 제목·설명·태그·섬네일만 갱신한다")
    parser.add_argument("--set-privacy", nargs=2, metavar=("VIDEO_ID", "PRIVACY"),
                        help="기존 영상의 공개범위만 바꾼다")
    args = parser.parse_args()

    if args.set_privacy:
        set_privacy(args.set_privacy[0], args.set_privacy[1])
    elif args.update:
        update_metadata(args.episode, args.update, args.privacy)
    else:
        upload(args.episode, args.privacy, args.video, args.dry_run)
