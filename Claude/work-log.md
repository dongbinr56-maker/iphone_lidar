# 프론트엔드 전면 재설계 작업 기록

> 작업자: Claude (Opus 4.8) · 시작: 2026-06-17
> 대상: `105 / 106 / 107 실시간 영상 녹화기` 프론트엔드

---

## 1. 배경 / 문제 정의

기존 프론트엔드의 문제:

- **코드 구조**: `app.py`(2007줄) 안에 HTML/CSS/JS가 거대한 인라인 f-string(1299~1801줄)으로 박혀 있음. 파이썬 백엔드와 프론트가 한 파일에 뒤엉킴.
- **레이아웃**: `header → main(좌: 카메라 3 그리드 / 우: 320px 사이드바) → footer`. 카메라 그리드가 `align-content: start` + 16:9 고정, 사이드바도 `align-self: start`라서 **모든 콘텐츠가 상단에 몰리고 화면 하단이 비어버림**(사용자가 지적한 "썰렁한 하단").
- **시각 품질**: 칙칙한 다크, 우측 패널에 마네킹 센서 정보가 깨알 텍스트 한 덩어리로 뭉쳐 있어 가독성 낮음.

사용자 요구:
1. 깔끔하고 공간활용 잘 되는 디자인
2. 프레임워크 변경 허용
3. 레퍼런스: `C:\Users\USER\Documents\01_Git\extension` (깔끔한 카드 + 일관된 컬러 토큰 + 명확한 위계)

---

## 2. 확정된 방향 (사용자 승인)

- **구현 방식**: 바닐라 분리 — 인라인 HTML을 걷어내고 `web/index.html · styles.css · app.js` 정적 파일로 분리. 빌드 단계 없음(node_modules 없음). FastAPI가 정적 서빙, 데이터는 JSON API.
- **테마**: 정제된 다크 (컨트롤룸/NVR 모니터링 월 스타일).

---

## 3. 설계

### 3.1 아키텍처

```
web/
  index.html   # 정적 셸 (서버 주입 없음)
  styles.css   # 전체 스타일 (다크 토큰 시스템)
  app.js       # WebRTC + /api/status 폴링 + 동적 렌더
app.py
  GET  /              -> web/index.html (FileResponse)
  GET  /styles.css    -> web/styles.css
  GET  /app.js        -> web/app.js
  GET  /api/config    -> { go2rtc_api_port, cameras:[{id,label}] }   # 신규: 부팅 1회
  GET  /api/status    -> (기존 그대로) 500ms 폴링
  POST /api/record/start, /stream/{id}.mjpeg, ... (기존 유지)
```

`/api/config`로 카메라 목록·go2rtc 포트를 받아 JS가 카메라 타일을 동적 생성 → app.py에서 프론트가 100% 빠짐.

### 3.2 레이아웃 (전체 높이 컨트롤룸 대시보드)

```
┌ topbar ─ 제목 · LIVE 3/3 · 중계 상태 · 실시간 시계 ──────────────┐
├ stage (1fr) ───────────────────────────────┬ rail (360px, full h)┤
│  카메라 월 (flex:1) 105/106/107 큰 타일      │ ◉ 녹화 시작 (큰 CTA) │
│   ├ 헤더: 카메라 105 · 영상수신 핀          │ 상태 카드            │
│   ├ 영상(flex:1, object-fit:contain)        │ 실행 전 확인 체크리스트│
│   └ fps·지연 오버레이                        │  ✓ 카메라 3/3        │
│                                             │  ⚠ 기준값 대기       │
│  텔레메트리 스트립 (하단을 채우는 핵심)        │                     │
│   마네킹 기준값(val1·환기량·환기시간·패킷)    │                     │
│   + 저장 파일                                │                     │
├ footer ─ 안내 문구 ──────────────────────────┴─────────────────────┤
```

핵심: `100vh` 앱 셸 + 카메라 월 `flex:1` + **하단 텔레메트리 스트립**으로 비던 하단을 기존 센서 정보로 채움(새 데이터 아님, 재배치).

### 3.3 데이터 매핑 (기존 → 신규 DOM)

| 기존 우측 패널 텍스트 | 신규 위치 |
|---|---|
| 상태(recordState) | 우측 레일 `상태` 카드 + 녹화 버튼 상태 |
| 실행 전 확인(preflight) | 우측 레일 체크리스트 (✓/⚠ 행) |
| 마네킹 기준값(sensor) | 하단 텔레메트리 카드 (수치 분해) |
| 저장 파일(output) | 하단 텔레메트리 카드 |

WebRTC + MJPEG 폴백 로직은 동작하던 것을 그대로 이식.

---

## 4. 진행 로그

- [x] 양 프로젝트 구조 파악, app.py 프론트/백엔드 데이터 흐름 분석
- [x] 방향 확정 (바닐라 분리 + 다크) — 사용자 승인
- [x] `web/` 정적 파일 작성 (index.html / styles.css / app.js)
- [x] `app.py` 패치 (정적 서빙 + /api/config, 인라인 HTML 17,780자 → 라우트 866자)
- [x] 서버 재기동 + 라이브 검증 (go2rtc ready, 카메라 3/3 WebRTC 수신, 녹화 버튼 상태 정확)
- [x] 버그 수정: `.wall-empty`가 `display:grid`로 `hidden` 속성을 덮어 빈 박스가 안 사라지던 문제 → `.wall-empty[hidden]{display:none}` 추가
- [ ] 사용자 피드백 반영 (대기 중)

## 5. 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `web/index.html` | 신규 — 정적 셸 (topbar / camera-wall / telemetry / rail / footer + 카메라 타일 `<template>`) |
| `web/styles.css` | 신규 — 다크 토큰 디자인 시스템, 전체 높이 그리드 레이아웃, 반응형(1180/920px) |
| `web/app.js` | 신규 — `/api/config` 부팅, 카메라 타일 동적 생성, WebRTC+MJPEG 폴백(기존 이식), `/api/status` 500ms 폴링 렌더 |
| `app.py` | import `HTMLResponse`→`FileResponse`, `WEB_DIR` 추가, 인라인 HTML `index()`+`camera_cards_html()` 제거 → `/`,`/styles.css`,`/app.js`,`/api/config` 라우트 |
| `.claude/launch.json` | 신규 — 프리뷰/검증용 dev 서버 정의(`recorder`, port 8000). 운영 필수 아님. |

## 6. 실행

기존과 동일:

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

`http://127.0.0.1:8000` — 백엔드 API(`/api/status`, `/api/record/start`, `/stream/*.mjpeg`, WebRTC 11984)는 전부 그대로. 프론트만 교체됨.

---

## 7. 검증 / 테스트 결과

### 7.1 카메라 3대 WebRTC — 직접 실행 라이브 검증

브라우저(헤드리스 Chromium)로 실제 페이지를 띄워 PeerConnection 상태를 직접 확인.

- go2rtc `/api/streams`: 105/106/107 모두 `producers=1` (소스 RTSP 정상 인입)
- 브라우저 eval 결과 — 3대 전부:
  - `transport=webrtc` (MJPEG 폴백 0건)
  - `pcConnectionState=connected`, `iceConnectionState=connected`
  - `video.readyState=4`, 실제 `1920×1080` 프레임 디코딩 중
- 콘솔 에러/경고 0건
- 상단 상태: `카메라 3/3 수신`, `중계 준비됨`

→ **PASS**: 기존 3대 WebRTC 그대로 정상 구동. 새 프론트의 WebRTC+폴백 이식에 회귀 없음.

### 7.2 마네킹 센서 패킷 — 코드단 검증 (하드웨어 비의존)

**(a) 순수 함수 단위 테스트** — `Claude/tests/test_sensor_pipeline.py` (별도 프로세스 import, COM/스레드 안 건드림)

`python Claude/tests/test_sensor_pipeline.py` → **25 passed, 0 failed**
- `normalize_external_sensor_payload`: val1/volume_ml 정규화, 누락 시 거절
- `MannequinSensorBuffer`: `latest_sensor`는 sensor_stream+숫자 val1만 선별, ts 보존
- `sensor_api_status` gt_ready 공식: 연결+최신 val1 → True / 오래됨 → False / 연결 끊김 → False
- `recording_preflight_status`: 카메라 누락·val1 부재·연결 끊김 각각 차단 사유 생성, 센서 비활성화 시 건너뜀

**(b) 실행 서버 end-to-end 합성 주입** — `POST /api/sensor/event {"val1":500}`
- 주입 전: `connected=True, gt_ready=False`, preflight 차단(“최근 10초 안에 val1 없음”)
- 주입 직후: `gt_ready=True`, `preflight.ready=True`, blockers 비움, 표시 환기량 500 mL
- 프론트(브라우저): 녹화 버튼 `녹화 시작`·활성화, 센서 `기준값 정상`, 차단 박스 사라짐
- 단발 패킷은 10초 후 만료되어 자동으로 `대기` 복귀 → freshness 로직 정상
- 녹화는 트리거하지 않음(센서 주입·상태 조회만)

→ **PASS**: 패킷 정규화→버퍼→gt_ready→프리플라이트→프론트 표시 전 경로 정상. 브리지 연결(ws://127.0.0.1:8010) 자체도 `connected`. 평소 화면의 “기준값 대기”는 코드 결함이 아니라 **실제 마네킹이 val1(0xd0)을 송신하지 않는 데이터 조건**.

### 7.3 최종 상태
서버는 평소 방식(detached uvicorn, `.server.pid`)으로 재기동되어 `localhost:8000`에서 정상 동작 중 (relay ready, 카메라 3/3, recording idle).
