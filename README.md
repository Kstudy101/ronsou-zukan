# ニッポン論争図鑑 — 쇼츠 제작 파이프라인

일본인이 자기들끼리 다투는 주제를 1분짜리 세로 쇼츠로 만든다.
채널: [@ronsou_zukan](https://www.youtube.com/@ronsou_zukan)

대본 한 파일(`script.json`)만 쓰면 나레이션·도해·BGM·영상·섬네일이 만들어지고
유튜브에 올라간다. 스톡 소재를 쓰지 않고 **수치를 도해로 직접 그린다.**

## 준비

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install requests Pillow numpy imageio-ffmpeg edge-tts \
    google-api-python-client google-auth-oauthlib google-auth-httplib2
cp config.example.toml config.toml     # 키를 채운다. 커밋 대상이 아니다
```

ffmpeg 은 `imageio-ffmpeg` 가 가져오는 바이너리를 쓴다. 따로 설치하지 않아도 된다.

**VOICEVOX 엔진**(일본어 나레이션)은 따로 받아 띄운다.
[릴리스](https://github.com/VOICEVOX/voicevox_engine/releases)에서 `windows-cpu` 를 받아 풀고:

```bash
"C:/Users/<사용자>/voicevox_engine/windows-cpu/run.exe" --host 127.0.0.1 --port 50021
```

## 한 번에 만들기

```bash
.venv/Scripts/python.exe make.py ep03_seniority
.venv/Scripts/python.exe pipeline/upload_youtube.py ep03_seniority --privacy private
```

단계별로:

```bash
python make.py <회차> --only render          # 렌더만
python make.py <회차> --skip images          # 이미지 수집만 건너뛰고
python pipeline/voicevox.py check <회차>     # 합성 없이 읽는 법만 확인
python pipeline/voicevox.py speakers         # 화자 목록
```

| 단계 | 파일 | 하는 일 |
|------|------|---------|
| images | `pipeline/fetch_images.py` | 위키미디어 공용에서 사진 수집. PD·CC0·CC BY·CC BY-SA 만 통과. 도해로만 만드는 회차는 건너뛴다 |
| voice | `pipeline/tts.py` | VOICEVOX 로 장면마다 합성. `readings.txt` 에 실제 읽은 카나를 남긴다 |
| bgm | `pipeline/bgm.py` | 배경음악을 파형으로 직접 합성. 제3자 권리가 없다 |
| render | `pipeline/render.py` | 도해 + 텔롭 + 나레이션·BGM 믹스 → 1080×1920 H.264, −14 LUFS |
| thumb | `pipeline/thumbnail.py` | 1280×720 섬네일 |

## 지켜야 할 규칙 셋

이 채널은 소개문에 「数字はすべて出典つき」라고 써 두었다. 그것을 코드로 강제한다.

**① 출처 없는 수치는 화면에 못 올라간다.**
`versus`·`number`·`split` 도해에 `source` 가 없으면 렌더가 실패한다.
지어낸 숫자에는 적을 출처가 없으므로 구조적으로 막힌다.

**② 읽는 법을 합성 전에 확인한다.**
일본 시청자가 합성음성 영상을 이탈하는 가장 큰 이유가 한자 오독이다.
`voicevox.py check` 로 카나를 눈으로 보고, 틀리면 `script.json` 의 `dictionary`
에 등록해 고친다.

**③ 비율이 아닌 값을 대립 막대로 그리지 않는다.**
막대는 실제 비율 그대로 나뉜다. 전후 변화는 `delta` 섬네일이나 `trend` 도해를 쓴다.

## script.json

```jsonc
{
  "topic_ja": "エスカレーター論争",
  "source": "NEXER／日本トレンドリサーチ 2021年10月 n=1,400",
  "expected_comments": ["大阪は逆やで", "..."],   // 3개가 안 나오면 만들지 않는다

  "voice": { "provider": "voicevox", "character": "ずんだもん",
             "style": "ノーマル", "speed": 1.08, "intonation": 1.15 },
  "dictionary": [{ "surface": "大曽根", "pronunciation": "オオゾネ", "accent_type": 0 }],

  "design": { "subtitle_mode": "ja", "subtitle_center_y_pct": 57.0,
              "main_size": 104, "bgm_style": "bright" },

  "scenes": [
    { "id": "s09", "ja": "大曽根駅で歩く人を数えたのだ",
      "tsukkomi": "なんで",                       // 제3의 화자가 거는 딴지
      "visual": { "type": "number", "value": "17.3", "unit": "%",
                  "label": "条例前 2023年8月", "side": "b",
                  "source": "東海テレビ 大曽根駅 実測" },
      "hold": 0.35 }
  ]
}
```

도해 종류: `versus`(대립 막대) · `number`(큰 숫자) · `split`(도도부현 개수) ·
`trend`(연도별 추이) · `choice`(양자택일) · `cta`(댓글 유도) · `photo`(수집 사진)

## 일본 사양 텔롭

일본 방송 자막은 1990년대 이후 「정보 보완」에서 **「츳코미」**로 역할이 바뀌었다.
한국식 발화 요약만 넣으면 밋밋하게 읽힌다.

| 항목 | 값 | 근거 |
|---|---|---|
| 문자 크기 | 104px | 세로형 화면 세로의 6~8% |
| 테두리 | 검정 단색, 문자 크기의 7.5% | 컬러 테두리·글로우는 회피 대상 |
| 색 수 | 4색 이내 | 무채색 2 + 액센트 2 |
| 위치 | 57%, **첫 줄 높이 고정** | 블록 중심을 고정하면 줄 수에 따라 위아래로 튄다 |
| 줄바꿈 | 동적계획법 | 숫자↔단위 금지, 조사 앞·한자어 중간에 벌점. 최소 줄 수로 금지 규칙을 못 지키면 줄을 늘린다 |

## 회차

| # | 주제 | 출처 |
|---|---|---|
| 01 | トイレットペーパー論争 (1,465 대 1,464) | Jタウン研究所 全国投票 n=2,929 |
| 02 | エスカレーター論争 (17.3%→10.5%) | NEXER n=1,400 / 東海テレビ 실측 |
| 03 | 年功序列 vs 成果主義 (36년 만의 역전) | 産業能率大学 第36回 n=369 |
| 05 | きのこの山 vs たけのこの里 (공식전 1승 1패, 2018년 1.2pt 차) | 明治 国民総選挙 2018(約1,593万票)·2019(1,058万票) |
| 06 | エアコン28度 (「28도」를 정확히 아는 사람 32.3%) | ダイキン工業 2018 n=500 / 環境省 COOLBIZ 掲載 n=1,342 |
| 07 | お好み焼きは「ごはんのおかず」か (관동 49.2%로 반반) | オタフクソース 地域別コナモン調査 2018 n=500 |
| 08 | ら抜き言葉 (「見れた」48.4%가 처음으로 역전) | 文化庁 平成27年度 国語に関する世論調査 n=1,959 |

`episodes/ep01_bicameral/` 은 이 채널 이전에 만든 한일 비교 회차다. 참고용으로 남겨 둔다.

## 소재

[일본쇼츠조사.md](일본쇼츠조사.md) — 리서치 4건 전문 (2,760줄, 출처 링크 659개).
실시간 논쟁 화제 / 정번 영구논쟁 / 숏폼 포맷·정책 / 제도·돈.

**중요**: YouTube 수익화 정책의 「AI Personas Related to Sensitive Topics」 조항에
따라, AI 음성으로 **건강·법률·금융**을 다루면 수익화 대상에서 빠진다.
그래서 소재는 생활·매너·지역·언어·세대에 한정한다. 조사 문서의 「A존」이 그것이다.

## 커밋하지 않는 것

`config.toml`(Typecast 키) · `client_secret.json`(Google OAuth) ·
`youtube_token.json`(리프레시 토큰) · 수집 이미지 · 음성 wav · BGM · 완성 mp4.

`candidates.json`·`timeline.json`·`readings.txt`·`credits.md` 는 무엇을 썼는지
남기기 위해 커밋한다.
