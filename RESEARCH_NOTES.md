# 연구노트

## 2026-06-19 15:48 KST - 현재 상태 정리와 기록 방식 정리

오늘은 새 기능을 넣기보다, 지금 프로젝트가 어디까지 와 있는지 한 번 멈춰서 정리했다. 앞으로 작업 내용이 계속 쌓일 예정이라, 나중에 다시 읽어도 맥락을 잃지 않도록 연구노트와 에이전트 작업 지침을 같이 만들었다.

요청은 세 가지였다. 지금까지 구현된 내용을 제대로 파악할 것, 이후 작업자가 볼 수 있는 `AGENTS.md`를 최신 상태로 둘 것, 그리고 앞으로의 작업을 연구노트에 남길 기준을 잡을 것.

### 확인한 범위

- 루트 폴더: `C:\Users\USER\Documents\01_Git\recording_AB`
- 주요 코드: `app.py`, `web/index.html`, `web/styles.css`, `web/app.js`
- 기존 기록: `Claude/work-log.md`
- 테스트: `Claude/tests/test_sensor_pipeline.py`
- 설정/운영 파일: `.env.example`, `.gitignore`, `.claude/launch.json`
- 샘플 녹화 결과: `recordings/20260617132113`, `recordings/20260617132214`

참고로 이 폴더는 현재 `git status` 기준으로 Git 저장소로 잡히지 않았다. 그래서 오늘 변경사항은 Git diff가 아니라 파일 내용 기준으로 확인했다.

### 현재 앱이 하는 일

이 프로젝트는 105, 106, 107번 RTSP 카메라를 한 화면에서 보고, 버튼을 누르면 3초 뒤부터 30초 동안 세 카메라 영상을 동시에 저장하는 로컬 녹화 앱이다.

흐름은 단순하다.

```text
RTSP 카메라 3대
  -> go2rtc 로컬 중계
  -> 브라우저 WebRTC 라이브 화면
  -> OpenCV 프레임 캡처
  -> 30초 MP4 저장
  -> 마네킹 센서/라벨/세션 메타데이터 저장
```

여기에 마네킹 환기 센서가 붙어 있다. 최근 `val1` 패킷이 들어와야 녹화 시작 조건을 통과한다. 단순히 카메라만 켜져 있다고 바로 녹화되는 구조가 아니라, 카메라 3대 프레임과 마네킹 기준값이 모두 준비되어야 한다.

### 코드 구조 메모

- `app.py`가 백엔드 대부분을 맡고 있다.
- FastAPI 라우트, go2rtc 실행, 카메라 프레임 읽기, 마네킹 센서 수집, 녹화, 라벨 생성, 세션 파일 저장이 모두 이 파일에 있다.
- 프론트엔드는 지금 `web/` 아래 정적 파일로 분리되어 있다. 별도 빌드 도구나 Node 환경은 없다.
- `web/app.js`는 `/api/config`로 카메라 정보를 받고, WebRTC 연결을 먼저 시도한 뒤 실패하면 MJPEG로 대체한다.
- 화면 상태는 `/api/status`를 0.5초마다 읽어서 갱신한다.
- 실험 조건 입력값은 녹화 시작 요청과 함께 넘어가며, 술자 원본 ID는 저장하지 않고 salt 기반 hash만 저장한다.

### 녹화 산출물

성공한 세션 폴더에는 대체로 아래 파일들이 생긴다.

```text
recordings/yyyyMMddHHmmss/
  105.mp4
  106.mp4
  107.mp4
  sensor.csv
  mannequin_packets.csv
  mannequin_packets.jsonl
  labels.json
  session.json
```

현재 코드에는 프레임 타임스탬프 동기화용 `frames_index.csv`, `frame_ts_sync.json` 생성 로직도 들어가 있다. 다만 기존 샘플 세션 두 개는 그 기능이 들어가기 전에 만들어진 것으로 보이며, 해당 파일은 들어 있지 않았다.

`recordings/TEST`에는 이전 테스트 녹화들이 많이 있고 `TEST.zip`도 있다. 이 폴더는 연구 데이터로 보고, 명시적인 요청 없이 정리하거나 삭제하면 안 된다.

### 오늘 확인한 실행 상태

이미 서버가 떠 있었다.

- Python PID: `28604`
- 시작 시각: 2026-06-17 14:13:01 KST
- 주소: `http://127.0.0.1:8000`

확인 결과는 다음과 같다.

- `/health`: `ok`
- `/api/config`: 105/106/107 카메라가 설정되어 있고, 녹화 30초/카운트다운 3초로 내려온다.
- go2rtc: 프로세스가 살아 있고 ready 상태다.
- ABC 센서 브리지: `ws://127.0.0.1:8010/ws`에 연결되어 있다.
- 마네킹 센서: 전체 패킷은 계속 들어오지만 현재 `val1` 센서 패킷은 0개다.
- 그래서 `gt_ready=false`이고, preflight는 "최근 10초 안에 val1 패킷 없음"으로 막혀 있다.
- 녹화 상태는 idle이다.

테스트도 돌렸다.

```powershell
python Claude/tests/test_sensor_pipeline.py
```

결과는 `43 passed, 0 failed`였다. 센서 payload 정규화, preflight 조건, 실험 메타데이터 검증, operator hash, 라벨/프레임 타임스탬프 산출 쪽은 현재 테스트 기준으로 통과한다.

### 눈에 띈 주의점

가장 중요한 점은 카메라 readiness다.

`/api/status`는 현재 `ready=true`를 반환하지만, 자세히 보면 세 카메라 모두 `opened=false`이고 RTSP open failure가 찍혀 있었다. 그런데 예전에 잡힌 마지막 프레임이 남아 있어서 `has_frame=true`로 보인다. `frame_age_seconds`도 매우 큰 값이었다.

즉, 지금 코드는 "프레임이 한 번이라도 있음"과 "지금 라이브로 정상 수신 중"을 완전히 구분하지 못할 수 있다. 실제 연구 녹화에서는 stale frame이 들어가면 곤란하므로, 다음 개선에서는 `opened`, `error`, `frame_age_seconds`를 함께 보고 신선한 프레임만 ready로 인정하는 쪽을 먼저 검토하는 게 좋다.

### 오늘 만든 문서

- `AGENTS.md`: 앞으로 작업하는 에이전트가 따라야 할 짧은 지침
- `CLAUDE.md`: `AGENTS.md`와 같은 내용을 가리키는 Windows 하드링크
- `RESEARCH_NOTES.md`: 앞으로 계속 이어서 쓸 연구노트

처음에는 `CLAUDE.md`를 심볼릭 링크로 만들려고 했지만 Windows 관리자 권한이 필요해서 실패했다. 대신 하드링크로 묶었다. `AGENTS.md`를 수정하면 `CLAUDE.md`도 같은 내용으로 유지된다.

### 앞으로 기록할 때의 기준

연구노트는 "나중에 사람이 읽고 바로 이어서 작업할 수 있는 기록"으로 쓴다. 너무 반듯한 보고서 문체보다, 그날 무엇을 봤고 무엇이 찝찝했는지 드러나는 쪽이 낫다.

앞으로 각 작업에는 최소한 아래 내용이 들어가면 좋겠다.

- 언제 무엇을 요청받았는지
- 어떤 파일을 건드렸는지
- 왜 그렇게 바꿨는지
- 어떤 명령으로 확인했는지
- 새로 생긴 녹화/라벨/이미지 같은 산출물이 있는지
- 다음에 볼 사람이 조심해야 할 점

글로 설명하기 어려운 구조나 흐름은 다이어그램을 넣어도 된다. 단순한 코드 흐름은 Mermaid나 텍스트 다이어그램이면 충분하고, 사람이 직관적으로 봐야 하는 화면 구성이나 개념 설명은 이미지 생성 도구로 만든 시각 자료를 붙이는 것도 허용한다. 다만 이미지를 넣을 때는 장식용이 아니라 이해를 돕는 경우에만 넣는다.

### 앞으로 작업할 때의 태도

작업은 늘 가능한 한 끝까지 밀고 간다. 필요한 경우 코드 읽기, 테스트, 웹 리서치, 이미지 생성, 보조 에이전트, 설계 검토, 디버깅 도구를 아끼지 않고 쓴다. 다만 "최선을 다한다"는 게 무조건 크게 만들거나 파일을 많이 건드린다는 뜻은 아니다. 이 프로젝트에서는 연구 데이터가 중요하므로, 목표와 직접 관련된 부분을 깊게 보고 검증까지 끝내는 쪽이 맞다.

애매한 부분은 혼자 단정하지 않고 가정과 선택지를 먼저 적는다. 위험한 변경, 녹화 데이터 삭제, 실험 결과를 바꿀 수 있는 작업은 명확한 요청 없이 진행하지 않는다. 반대로 구현이 필요한 요청은 분석만 하고 멈추지 말고, 가능한 범위에서는 실제 수정과 확인까지 이어간다.

### 다음에 보면 좋을 일

- 카메라 ready 조건에 frame freshness를 넣기
- README를 현재 기능 기준으로 다시 정리하기
- `requirements.txt` 또는 `pyproject.toml`로 실행 환경 고정하기
- `_uvicorn.out.log`가 너무 커지지 않도록 로그 수준이나 rotation 검토하기
- 예전 세션과 최신 세션의 산출물 차이를 README나 연구노트에 명확히 표시하기

## 2026-06-22 09:11 KST - LiDAR Teacher 방법론 문서와 현재 녹화기 비교

오늘은 `C:\Users\USER\Documents\01_Git\4DSG_POC\03_breath_v4\docs\ABC_LiDAR_RGB_AB_Methodology.md`를 읽고, 지금 `recording_AB`가 그 목표까지 가려면 무엇이 비어 있는지 봤다.

문서의 핵심은 분명하다. LiDAR iPhone은 최종 제품 센서가 아니라 학습용 Teacher다. 105와 106 위치에 LiDAR iPhone을 두고 RGB-D, confidence, pose, serial을 모아 3D 라벨을 만든다. 최종 배포 모델은 105/106 RGB만 보고 A/B 상태를 추론해야 한다.

현재 `recording_AB`는 그 중 앞단 일부만 맡고 있다. 지금 있는 것은 105/106/107 RGB 녹화, go2rtc/WebRTC 모니터링, 마네킹 serial/ABC WebSocket 수집, `val1` 기반 preflight, 실험 메타데이터 입력, MP4/CSV/JSON 산출물 저장이다. 이건 데이터 수집기의 출발점으로는 쓸 수 있지만, 아직 LiDAR Teacher나 RGB Student 학습 파이프라인은 없다.

현재 코드에서 이미 맞아 있는 부분도 있다.

- 세션 메타에 `protocol_type`, `scenario_id`, `glove_condition`, `torso_clothing`, `operator_id_hash`가 들어간다.
- 마네킹 serial에서 `val1`, flow, 환기 관련 필드를 받아 `sensor.csv`, `labels.json`으로 내보낸다.
- 프레임 타임스탬프 동기화용 `frames_index.csv`, `frame_ts_sync.json` 생성 로직이 들어가 있다.
- 105/106/107 카메라를 동시에 녹화하는 구조는 있으므로, 107을 QA/보조 RGB로 두는 방향과 충돌하지 않는다.

비어 있는 부분은 더 크다.

- LiDAR iPhone에서 RGB, depthMap, confidenceMap, pose, intrinsics를 받는 수집 경로가 없다.
- iPhone timestamp와 마네킹 serial timestamp를 맞추는 sync 이벤트/offset 보정 로직이 없다.
- 105/106 depth를 공통 좌표로 묶을 때 필요한 AprilTag/ArUco 캘리브레이션 경로가 없다.
- `session.json`은 아직 방법론 문서의 dataset schema보다 단순하다. camera frame_id/timestamp_ns, intrinsics/extrinsics, lidar path, quality flag, teacher_label_3d, student_label_2d가 없다.
- head angle, chin-neck distance, mask gap, bag compression, chest rise 같은 Teacher metric 계산 코드가 없다.
- `bvm_bag`, `bvm_mask`, `head`, `torso`, `hand` 5-class detector 라벨링/학습 파이프라인이 없다.
- YOLOX + ROI heads + TCN 학습/ONNX export/DeepX·TensorRT 컴파일 검증 경로가 없다.
- 실시간 RGB-only 추론 상태머신도 아직 앱에 붙어 있지 않다.

작업 순서는 문서의 MVP 로드맵을 그대로 따르는 게 맞다. 다만 지금 코드 상태를 보면 Phase 0 전에 녹화기 안정화가 먼저 필요하다.

1. 먼저 현재 녹화기를 데이터 수집기로 안정화한다. stale PID, 서버 재시작, frame freshness, RTSP timeout, 로그 폭증, dependency manifest를 정리해야 한다.
2. 세션 스키마를 versioned manifest로 확장한다. 기존 MP4/serial 산출물을 유지하면서 camera/lidar/serial/labels/quality 그룹을 넣을 수 있게 만든다.
3. LiDAR iPhone 105/106 수집 또는 import 경로를 만든다. 처음부터 완전 실시간일 필요는 없고, iPhone에서 저장한 RGB-D 세션을 `recording_AB` 세션 폴더로 가져오는 방식이 현실적이다.
4. sync 이벤트를 정한다. 화면 blink나 소리 이벤트를 넣고, iPhone timestamp와 serial timestamp offset을 계산해 `sync_quality`로 저장한다.
5. A-static부터 시작한다. 105 LiDAR로 head extension, chin-neck distance가 RGB 프레임에 제대로 투영되는지 확인한다.
6. 그 다음 A-dynamic, B-squeeze, seal/vent 순서로 Teacher metric을 늘린다.
7. Teacher 라벨이 쌓이면 5-class detector 라벨셋과 RGB-only Student 학습 repo를 분리해서 만든다.
8. 마지막에 ONNX export, DeepX DXNN, TensorRT 변환을 조기 스모크 테스트로 확인하고 앱에 실시간 추론 상태머신을 붙인다.

오늘 추가로 확인한 현재 실행 상태도 남긴다. `http://127.0.0.1:8000/api/status`는 응답하지 않았다. `.server.pid`에는 `28604`가 남아 있지만 실제 프로세스는 없어서 stale pid다. `_uvicorn.err.log`에는 이전 OpenCV/FFmpeg stream timeout warning이 계속 쌓여 있었다. 다음 구현 전에 서버 실행/정지 상태를 먼저 정리하는 게 좋다.

## 2026-06-22 09:45 KST - E2E 브리프 기준 구현 방향 재정리

방금 받은 브리프는 실제 구현 순서를 더 분명하게 정리해준다. 결론은 `recording_AB`를 계속 RTSP 카메라 중심으로 키우는 것이 아니라, iPhone LiDAR 캡처 세션을 받아들이는 PC-side 데이터 허브로 바꾸는 쪽이다. 현재 앱에서 살릴 것은 메타데이터 폼, 마네킹 serial GT, session 산출물 작성기, frame index/sync report다. 교체 대상은 카메라 소스와 녹화 루프다.

가장 먼저 해야 할 일은 iOS Swift 캡처 앱이다. 앱은 ARKit `ARWorldTrackingConfiguration`에 `.sceneDepth`를 켜고, 매 프레임마다 RGB, depthMap, confidenceMap, intrinsics, camera transform, ARKit timestamp를 저장해야 한다. 중요한 점은 depth를 mp4 안에 넣을 수 없다는 것이다. RGB는 HEVC/mp4로 저장하고, depth/confidence는 프레임별 파일로 분리한 뒤 `manifest.json`에서 frame_index로 묶어야 한다.

PC 쪽 첫 구현은 `ingest_iphone_session()`이다. iPhone에서 넘어온 세션을 현재 `recordings/yyyyMMddHHmmss/` 아래 표준 레이아웃으로 안착시키고, RGB frame과 depth/confidence/pose/intrinsics를 같은 frame_index로 찾을 수 있게 만들어야 한다. 이 단계가 흔들리면 뒤의 Teacher 라벨 공장도 전부 흔들린다.

브리프 기준으로 지금 당장 구현 우선순위는 이렇게 잡는다.

1. iOS Swift capture app: 105/106 각각 RGB-D+pose+manifest 저장.
2. PC ingest: iPhone 파일공유/USB pull 세션을 표준 session layout으로 복사·검증.
3. depth↔RGB 정합 시각 검증: depth overlay, confidence mask, pose/intrinsics sanity check.
4. serial sync 준비: ARKit mach monotonic timestamp와 PC epoch serial timestamp 사이 offset을 sync event로 매핑.
5. A-static Teacher MVP: head extension/chin-neck metric을 만들고 RGB 프레임에 투영되는지 확인.
6. 그 뒤 B-squeeze, mask/seal, ventilation proxy 순서로 확장.

안전 규칙도 구현 기준에 넣어야 한다. 영상만 보고 tidal volume이나 good seal을 단정하면 안 된다. `effective_ventilation`은 squeeze, visual seal, chest rise, serial 증거를 같이 봐야 한다. 위험지표는 `false_effective`와 `false_airway_open`을 별도 KPI로 관리한다.

이번 브리프에서 재사용/교체 경계가 명확해졌다.

- 재사용: experiment meta 검증, `operator_id_hash`, `glove_condition`, `torso_clothing`, `MannequinSerialReader`, `AbcSensorBridge`, `gt_ready`, session/label 파일 작성 코드.
- 교체: go2rtc/RTSP `CameraReader`, `_record_once` 중심 녹화 흐름.
- 새로 추가: iOS capture app, PC ingest, depth/RGB overlay QA, Teacher metric 계산, 5-class detector dataset export, Student 학습/배포 파이프라인.

이제 다음 작업을 시작한다면 바로 코드 수정으로 들어가기보다, 먼저 `docs/iphone_capture_contract.md` 같은 세션 계약서를 작성하는 게 안전하다. iOS 앱과 PC ingest가 같은 파일명, timestamp 단위, 좌표계, frame_index 규칙을 공유해야 하기 때문이다. 계약서 없이 각자 구현하면 나중에 depth/RGB/serial 정합에서 시간을 많이 잃는다.

### 라벨링 기준 메모

YOLO 5-class 검출 라벨은 iPhone에서 녹화한 RGB 영상 프레임을 기준으로 만든다. 최종 Student 입력이 RGB-only이므로 bbox 좌표도 RGB 이미지 좌표계에 있어야 한다. depthMap이나 confidenceMap 위에 직접 YOLO 라벨을 붙이는 방식이 아니다.

다만 RGB 프레임만 따로 떼어내서 관리하면 안 된다. 각 이미지가 iPhone manifest의 `frame_index`를 유지해야 하고, 같은 `frame_index`로 depth/confidence/pose/intrinsics를 다시 찾을 수 있어야 한다. LiDAR Teacher는 이 연결을 이용해 3D metric과 pseudo-label을 만들고 RGB 프레임으로 투영한다. 사람이 수동 라벨링을 하더라도 기준 이미지는 RGB frame이고, LiDAR/depth는 라벨 품질 확인과 자동 보조에 쓴다.

검출 클래스는 `bvm_bag`, `bvm_mask`, `head`, `torso`, `hand`만 둔다. `hand_on_bag` 같은 접촉 상태는 클래스가 아니라 bbox overlap 후처리로 계산한다. A_state, squeeze_phase, ventilation 같은 상태 라벨은 YOLO bbox 라벨이 아니라 frame/event-level 라벨로 따로 저장한다.

### iPhone 연결 직후 해야 할 일 / 모델 역할 메모

iPhone을 PC에 연결했을 때 바로 할 일은 데이터를 뽑는 것이 아니라, 먼저 캡처 경로가 준비되어 있는지 확인하는 것이다. iOS Swift 캡처 앱이 아직 없다면 PC가 LiDAR depth, confidence, ARKit pose를 자동으로 가져올 방법은 없다. 따라서 첫 단계는 iPhone이 PC에서 신뢰된 장치로 보이는지, 파일 전송 경로가 가능한지, 그리고 캡처 앱이 저장할 세션 폴더 계약이 정해져 있는지 확인하는 것이다.

캡처 앱이 준비된 상태라면 105/106 각각에서 RGB mp4, depth frame, confidence frame, pose/intrinsics, `manifest.json`이 같은 frame_index로 저장되어야 한다. PC는 이 폴더를 `recordings/<session_id>/iphone_105/`, `iphone_106/` 같은 표준 위치로 가져오고, ingest 단계에서 누락 파일, frame count, timestamp monotonic 여부, depth/RGB overlay를 검사한다.

모델 역할은 YOLO 하나로 끝나지 않는다. YOLO는 5개 객체의 위치를 찾는 detector다. 기도확보, BVM squeeze, mask 밀착은 detector 출력 위에 ROI head와 temporal model이 붙어서 판단한다.

- AirwayHead: head/jaw/neck RGB crop과 LiDAR Teacher가 만든 head angle/chin-neck 라벨로 학습한다. 출력은 `OPEN`, `INSUFFICIENT`, `COLLAPSED`, `OVER_EXTENSION_SUSPECTED`, `LOW_CONFIDENCE`.
- BagHead: bvm_bag crop, bbox 크기 변화, 손-bag overlap, optical/temporal cue를 보고 `squeeze_phase`, `compression_proxy`, CPM을 추론한다. Teacher는 bag depth/compression과 serial event를 제공한다.
- MaskHead: face+mask crop, mask bbox 위치, face/mask 정렬, rim 주변 RGB/depth Teacher 라벨을 기반으로 `mask_position`과 `visual_seal_likelihood`를 추론한다. 실제 seal 성공은 영상만으로 확정하지 않고 serial/chest evidence와 결합한다.
- ChestHead/TCN: torso crop sequence와 chest rise Teacher 라벨, serial `val1/flow`를 결합해 `effective_ventilation` 후보를 만든다. 최종 상태머신은 false effective와 false airway open을 줄이는 쪽으로 abstain을 유지한다.

### Mac Codex에 넘길 iOS 앱 프롬프트 작성

Mac 쪽 Codex에게 맡길 첫 구현 범위는 iOS Swift 캡처 앱으로 자른다. 현재는 iPhone이 한 대뿐인 환경으로 본다. 따라서 105/106 동시 캡처, cross-view consistency, 두 시점 동기화는 이번 범위에서 제외하고, 단일 LiDAR iPhone이 RGB-D+pose 세션을 안정적으로 저장하는 것만 성공 기준으로 둔다.

프롬프트에는 SwiftUI/ARKit/AVFoundation 기반 앱, `.sceneDepth` 지원 확인, RGB HEVC mp4, depth/confidence `.npy`, pose/intrinsics manifest, File Sharing export, validation script, README까지 명확히 넣는다. 핵심 계약은 `frame_index`다. RGB 동영상, depth, confidence, intrinsics, camera transform, ARKit timestamp가 모두 같은 frame_index로 다시 연결되어야 한다. 이 앱의 성공 기준은 예쁜 UI가 아니라 5초 테스트 캡처 후 PC에서 manifest와 depth/RGB 매핑을 검증할 수 있는 것이다.

## 2026-06-22 11:02 KST - iOS 캡처 MVP 완료 보고 확인

Notion의 `ABC LiDAR Capture MVP 구현 작업 보고 - 2026-06-22` 내용을 확인했다. Mac 쪽 구현은 생각보다 넓게 들어갔다. SwiftUI 앱, ARKit `.sceneDepth`, RGB HEVC mp4 저장, depth/confidence NPY 저장, frame별 intrinsics/pose manifest, File Sharing export, validator, synthetic session, negative validator tests, README, device checklist, Xcode compile까지 완료된 것으로 보고됐다.

아직 끝난 것은 아니다. 보고서 기준 현재 상태는 "로컬 구현과 비실기기 검증 통과"다. 실제 LiDAR iPhone에서 5초 이상 촬영한 real session을 export하고 validator를 통과시키는 물리 장치 검증이 남아 있다. 이 검증 전까지는 캡처 앱 완료가 아니라 MVP 구현 후보가 준비된 상태로 보는 게 맞다.

사용자가 지금 해줘야 할 일은 실기기 acceptance test다. Mac에서 Xcode를 열고 physical LiDAR iPhone을 선택한 뒤 signing team 설정, 앱 설치, camera permission 승인, 앱 화면에서 `LiDAR scene depth ready` 확인, position tag 선택, 5초 이상 녹화, Stop, Finder 또는 Files 앱으로 `ABC_LiDAR_Captures/<session_id>` export를 해야 한다. 그 다음 Mac에서 `python3 tools/validate_capture_session.py /path/to/ABC_LiDAR_Captures/<session_id> --min-frame-count 40 --min-average-fps 8`을 실행한다.

이 테스트가 통과하면 다음 단계는 Windows `recording_AB` 쪽에서 `ingest_iphone_session()`을 만드는 것이다. 통과하지 않으면 validator 출력과 `_invalid/error.json`, 앱 화면의 recent errors, session 폴더 구조를 기준으로 원인을 먼저 잡아야 한다.

## 2026-06-22 11:20 KST - iOS 캡처 MVP 실기기 검증 보고 확인

Notion 보고서가 업데이트되어 실제 LiDAR iPhone 검증 결과까지 확인했다. 이번에는 단순 로컬 빌드나 synthetic session이 아니라, iPhone 14 Pro에서 자동 녹화한 실제 세션이 export되고 validator를 통과했다.

실기기 테스트 내용은 다음과 같다.

- Device: 손동빈의 iPhone, iPhone 14 Pro (`iPhone15,2`), iOS 18.6.2
- Bundle ID: `com.abc-dallgoo.lidar.capture`
- Session ID: `20260622_111830_single`
- Position tag: `single`
- 자동 녹화 시간: 6.0초
- 저장 프레임 수: 69 frames
- 첫 frame: `frame_index=0`, `depth/000000.npy`, `confidence/000000.npy`
- 마지막 frame: `frame_index=68`, `depth/000068.npy`, `confidence/000068.npy`
- export 위치: `/Users/dongbin3763/Documents/Git/ABC_Lidar/device_acceptance_exports/20260622_111812/ABC_LiDAR_Captures/20260622_111830_single`
- acceptance report: `/Users/dongbin3763/Documents/Git/ABC_Lidar/device_acceptance_exports/20260622_111812/device_acceptance_report.md`

validator 결과는 `PASS: session valid (69 frames)`였다. `--min-frame-count 40`과 `--min-average-fps 8` 조건도 통과했다. 남은 경고는 Mac 환경에 `ffprobe`가 없어 MP4 strict duration/frame validation을 스킵했다는 점이다. 따라서 RGB-D 세션 구조와 frame_index 연결은 실기기에서 확인됐고, RGB mp4의 codec/frame count/duration까지 닫으려면 `ffmpeg` 설치 후 `--strict-video`를 한 번 더 돌리면 된다.

추가로 `tools/run_device_acceptance.sh`가 생겼다. 이 스크립트는 physical iPhone 탐지, signing/preflight, 빌드/설치/launch, launch argument 기반 6초 자동 녹화, app data container export, validator 실행, report 생성을 자동화한다. 앱에도 `--abc-auto-record-seconds`, `--abc-position-tag` 기반 자동 녹화 옵션이 들어갔다. 일반 실행에서는 argument가 없으면 자동 녹화가 동작하지 않는다고 보고됐다.

현재 판단은 명확하다. 단일 iPhone LiDAR RGB-D 캡처 MVP는 실제 장치에서 원천 데이터 저장까지 통과했다. 다음 작업은 Windows `recording_AB`로 이 세션을 가져오는 PC ingest 설계/구현이다. 그 전에 선택적으로 Mac에 `ffmpeg`를 설치해 strict video 검증을 닫고, 가능하면 `single`, `105`, `106` 태그별 샘플 세션을 각각 하나씩 확보하면 좋다.

## 2026-06-22 11:27 KST - strict video 재검증 통과 확인

Notion 보고서를 다시 확인했다. 이전에 남아 있던 `ffprobe not found` 경고가 닫혔다. Mac에 `ffmpeg`/`ffprobe`가 설치됐고, 같은 실제 iPhone 세션 `20260622_111830_single`에 대해 strict video 검증을 다시 실행했다.

검증 대상은 동일하다.

- Session ID: `20260622_111830_single`
- Session path: `/Users/dongbin3763/Documents/Git/ABC_Lidar/device_acceptance_exports/20260622_111812/ABC_LiDAR_Captures/20260622_111830_single`
- Device: iPhone 14 Pro (`iPhone15,2`), iOS 18.6.2
- Saved frames: 69
- ffprobe: `/opt/homebrew/bin/ffprobe`, version `8.1.2`

실행 명령은 다음이다.

```bash
python3 tools/validate_capture_session.py /Users/dongbin3763/Documents/Git/ABC_Lidar/device_acceptance_exports/20260622_111812/ABC_LiDAR_Captures/20260622_111830_single --min-frame-count 40 --min-average-fps 8 --strict-video
```

결과는 `PASS: session valid (69 frames)`였다. 이로써 manifest, depth, confidence, timestamp, intrinsics, pose 검증뿐 아니라 RGB mp4의 strict video gate까지 통과했다. 단일 iPhone `single` position 기준으로는 캡처 MVP acceptance를 완료로 봐도 된다.

다음은 선택과 필수로 나뉜다. 선택 작업은 같은 절차로 `105`, `106` position tag 샘플을 각각 확보하는 것이다. 필수 다음 단계는 검증된 세션을 Windows `recording_AB` 쪽으로 옮기고 `ingest_iphone_session()` 설계를 시작하는 것이다.

### Windows에서 iPhone 녹화 원격 시작 가능성

Windows 앱에서 `녹화 시작`을 누르면 iPhone 캡처 앱도 같이 녹화를 시작하게 만드는 것은 가능하다. 단, iPhone을 USB로 꽂았다고 Windows가 앱을 임의로 깨우거나 직접 제어할 수 있는 구조는 아니다. iPhone 앱이 foreground에서 실행 중이어야 하고, 앱 안에 명령 수신 채널을 만들어야 한다.

가장 현실적인 1차 방식은 같은 Wi-Fi/LAN에서 iPhone 앱이 작은 HTTP 또는 WebSocket server를 열고, Windows FastAPI 앱이 `POST /record/start`, `POST /record/stop`, `GET /status` 같은 명령을 보내는 구조다. iPhone 앱은 명령을 받으면 현재 구현된 capture controller를 호출해 session을 시작하고, manifest에 remote command 수신 시각과 frame start 정보를 남긴다.

USB만으로 처리하는 방식도 가능성은 있지만 1차 선택으로는 피하는 게 낫다. `iproxy`/usbmuxd 계열로 Windows local port를 iPhone 앱 port에 forward하는 구조를 만들 수 있지만, Windows 환경 구성과 iOS 앱 네트워크 리스너 조건이 더 까다롭고 디버깅 비용이 크다. 먼저 Wi-Fi HTTP/WebSocket 제어를 성공시키고, 필요하면 USB tunnel을 2차로 붙이는 게 맞다.

중요한 점은 동기화다. Windows가 start 명령을 보낸 PC epoch 시각과 iPhone이 명령을 받은 ARKit timestamp는 같은 clock이 아니다. 따라서 iPhone manifest에 `remote_control_events`를 추가해 `pc_sent_epoch`, `iphone_received_wall_time`, `arkit_timestamp_at_receive`, `first_committed_frame_index`를 저장해야 한다. 이 기록이 있어야 나중에 마네킹 serial과 iPhone RGB-D를 맞출 수 있다.

### 모델 학습 없이 RGB-D로 바로 볼 수 있는 성과

RGB-D를 도입하면 모델 학습 전에도 바로 확인할 수 있는 결과가 있다. 핵심은 자동 판정이 아니라, 깊이와 3D metric이 실제 술기 움직임을 설명하는지 보는 것이다.

즉시 가능한 것은 depth/RGB overlay, confidence mask, 3D point cloud, 수동 기준점 기반 거리/각도 측정, 시간축 displacement curve다. 105 위치에서는 head extension과 chin-neck distance 같은 A-static 지표를 볼 수 있고, 106 위치에서는 bag compression과 chest rise displacement를 보는 데 유리하다. mask seal은 visual gap proxy까지는 볼 수 있지만, 실제 seal 성공은 serial/chest evidence 없이 단정하면 안 된다.

따라서 다음 데모의 목표는 "학습된 모델이 맞췄다"가 아니라 "RGB-D가 기도확보 자세, 백 압축, 흉부 상승을 숫자와 overlay로 설명한다"가 되어야 한다.

### hand class와 MediaPipe 사용 검토

YOLO에서 `hand` 클래스를 빼고 MediaPipe Hands만 쓰는 선택지는 가능하다. MediaPipe Hand Landmarker는 RGB 이미지에서 손 landmark와 handedness를 반환한다. 손의 bbox보다 더 자세한 keypoint를 주기 때문에 손-bag 접촉, squeeze 순간, 손 위치 안정성을 보는 데 유리할 수 있다. 라이선스도 Apache 2.0 계열이라 큰 방향과는 맞다.

다만 최종 전략은 분리하는 게 낫다. 라벨링 보조와 오프라인 분석에는 MediaPipe를 적극 사용해도 된다. 하지만 YOLO detector에서 `hand`를 완전히 제거하면, 장갑/가림/손 일부만 보이는 연구 장면에서 손 검출 실패를 제어하기 어려워질 수 있다. 또한 DeepX/Jetson 배포 모델을 YOLOX+ROI+TCN으로 단순화하려는 계획과도 런타임 의존성이 갈라진다.

현재 권장안은 hybrid다. YOLO 5-class에는 `hand`를 유지하고, MediaPipe는 hand keypoint pseudo-label 생성, 라벨 검수, hand-bag overlap 보강, BagHead feature 생성에 쓴다. 나중에 데이터로 확인해서 MediaPipe가 장갑/가림 조건에서도 충분히 안정적이면 hand detector 제거를 다시 판단한다.

## 2026-06-22 13:25 KST - 현재 프로젝트 구조 재확인

오늘 한 일:

사용자가 "지금 우리가 진행하려는 프로젝트를 완벽하게 분석해서 이해해놔"라고 요청해서, 현재 `recording_AB` 코드와 기존 연구노트, 이전 메모리 기록, 상위 LiDAR 방법론 문서를 다시 대조했다.

확인한 파일/데이터:

- `app.py`
- `web/index.html`, `web/app.js`, `web/styles.css`
- `Claude/tests/test_sensor_pipeline.py`
- `RESEARCH_NOTES.md`
- `recordings/20260617132113`, `recordings/20260617132214`
- `C:\Users\USER\Documents\01_Git\4DSG_POC\03_breath_v4\docs\ABC_LiDAR_RGB_AB_Methodology.md`

변경한 내용:

코드 변경은 하지 않았다. 이 항목만 연구노트에 남긴다. 현재 이해 기준은 다음과 같다.

- 지금 앱은 105/106/107 RTSP 카메라 녹화기이면서, 마네킹 `val1` GT 센서와 실험 메타데이터를 세션 산출물로 묶는 PC-side 수집기다.
- squeeze 라벨의 기준은 CV 추론이 아니라 마네킹 sensor GT다. 이 경계는 다음 작업에서도 유지해야 한다.
- operator 원문은 저장하지 않고, normalize 후 `MANNEQUIN_OPERATOR_SALT`로 HMAC-SHA256 해시만 저장한다.
- `frame_ts_sync.json`은 이 앱의 동기화 산출물 이름이다. 다른 POC의 `sync_report.json`과 섞으면 안 된다.
- 다음 큰 방향은 RTSP 녹화 루프를 더 키우는 것이 아니라, iPhone LiDAR 캡처 세션을 `recording_AB`로 가져오는 `ingest_iphone_session()` 경로를 만드는 것이다.
- 재사용할 것은 metadata 검증, sensor GT 수집, label/session writer, frame index/sync 작성기다.
- 교체 또는 확장할 것은 go2rtc/RTSP 중심 `CameraReader`와 `_record_once` 흐름, 그리고 iPhone RGB-D manifest/depth/confidence/pose ingest 계약이다.

검증:

- `python -m py_compile app.py` 통과.
- `python Claude\tests\test_sensor_pipeline.py` 결과 `RESULT: 43 passed, 0 failed`.
- `.server.pid`에 적힌 프로세스는 현재 없고, `http://127.0.0.1:8000/health`는 타임아웃이었다. 이번 확인 시점에는 로컬 서버가 떠 있지 않은 상태로 본다.

생긴 산출물:

새 녹화 데이터는 만들지 않았다. 테스트 과정에서 파이썬 런타임 캐시만 갱신될 수 있다.

찝찝한 점 또는 다음에 볼 것:

- 현재 샘플 세션 두 개는 최신 `frames_index.csv`/`frame_ts_sync.json` 도입 전 산출물로 보인다. ingest나 schema 확장 시 하위호환을 유지해야 한다.
- README는 현재 기능보다 오래된 설명이다. 다음 큰 구현 전에 session contract 문서를 먼저 쓰는 편이 안전하다.
- Windows 쪽에는 Mac에서 만든 `ABC_Lidar` 앱 저장소가 보이지 않는다. 실제 iPhone export 샘플을 이 PC로 옮긴 뒤 ingest 설계를 닫아야 한다.

## 2026-06-22 13:32 KST - GitHub iphone_lidar 저장소 업로드

오늘 한 일:

사용자가 `https://github.com/dongbinr56-maker/iphone_lidar` 저장소에 현재 프로젝트를 올려 달라고 요청했다.

가정:

- 원격 저장소의 `main` 브랜치에 현재 `recording_AB` 코드와 문서를 올린다.
- 연구 데이터와 로컬 비밀 설정은 올리지 않는다.
- 원격 저장소가 초기 README만 가진 상태라서, 현재 README는 프로젝트 설명으로 갱신해도 된다.

변경한 내용:

- 코드 변경은 하지 않았다.
- 이 연구노트 항목을 추가했다.
- 업로드 대상은 `app.py`, `web/`, `Claude/tests/`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `.env.example`, `.gitignore`, `.claude/launch.json`, `RESEARCH_NOTES.md`로 잡았다.
- 제외 대상은 `.env`, `recordings/`, `runtime/`, `_uvicorn.*.log`, `.server.pid`, `__pycache__/`, `*.pyc`다.

검증:

- 업로드 전 `git ls-remote`로 원격 `main`이 존재하는 것을 확인했다.
- 업로드 전/후 코드 검증은 `python -m py_compile app.py`, `python Claude\tests\test_sensor_pipeline.py` 기준으로 확인한다.

생긴 산출물:

- 원격 GitHub 저장소 `dongbinr56-maker/iphone_lidar`에 업로드용 commit이 생길 예정이다.

찝찝한 점 또는 다음에 볼 것:

- 이 Windows 폴더 자체는 Git 저장소가 아니어서, 임시 clone을 만들어 그 안에 파일을 복사한 뒤 push하는 방식으로 진행한다.
- 실제 iPhone LiDAR capture app 소스는 이 Windows 폴더에 없으므로 이번 push에는 포함되지 않는다.

## 새 항목 템플릿

```text
## YYYY-MM-DD HH:mm KST - 제목

오늘 한 일:

확인한 파일/데이터:

변경한 내용:

검증:

생긴 산출물:

찝찝한 점 또는 다음에 볼 것:
```
