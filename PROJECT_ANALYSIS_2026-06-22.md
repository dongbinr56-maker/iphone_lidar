# recording_AB 프로젝트 분석 보고서

작성 시점: 2026-06-22 18:49 KST  
대상 경로: `C:\Users\USER\Documents\01_Git\recording_AB`

## 1. 한 줄 결론

이 프로젝트는 원래 `105 / 106 / 107` RTSP 카메라를 로컬 PC에서 WebRTC로 모니터링하고 30초 MP4로 저장하는 FastAPI 녹화기였고, 현재는 마네킹 `val1` 센서 GT, 실험 메타데이터, 프레임 타임라인 산출물을 함께 남기는 BVM/기도확보 연구용 데이터 수집기로 확장된 상태다. 최근 작업 방향은 여기에 iPhone LiDAR/RGB-D 캡처 데이터를 가져와 Teacher 신호로 쓰고, RGB-only YOLOX-tiny Student detector를 학습시키는 쪽으로 이어져 있다.

## 2. 현재 프로젝트의 실제 정체

현재 작업물은 세 층으로 나뉜다.

1. `app.py` + `web/`
   - Windows PC에서 돌아가는 실제 운영 앱이다.
   - 105/106/107 RTSP 카메라를 `go2rtc`로 WebRTC 변환해 브라우저에서 보고, 버튼을 누르면 3초 카운트다운 후 30초 동안 세 카메라 MP4를 저장한다.
   - 마네킹 센서 입력을 받아 `val1` 기준값을 녹화 전 조건과 자동 라벨의 source of truth로 쓴다.
   - 세션 메타데이터를 검증하고, operator 원문은 저장하지 않고 HMAC-SHA256 해시만 남긴다.

2. `iphone_lidar/`
   - 이름은 iPhone LiDAR지만, 현재 이 폴더는 실제 iOS/ARKit 캡처 앱 소스가 아니다.
   - 현재 `recording_AB` FastAPI 앱의 GitHub 스냅샷/클론 성격이다.
   - 실제 iPhone 캡처 앱 또는 Mac 쪽 `ABC_Lidar` 소스는 이 Windows 작업 폴더 안에는 없다.

3. `yolox_lora_train/`
   - iPhone 캡처 RGB 프레임에서 나온 bbox 라벨을 이용해 RGB-only Student detector를 학습시키는 분리 디렉토리다.
   - 모델은 YOLOX-tiny이고, LoRA를 neck/head 쪽에 주입한 뒤 export 시 merge해서 순정 YOLOX ONNX로 내보내는 구조다.
   - 현재 실제 데이터 기준 클래스는 4개다: `abdomen`, `face`, `bvm_mask`, `bvm_bag`.

## 3. 절대 유지해야 하는 소스 오브 트루스 경계

- BVM squeeze 라벨의 기준은 CV 추론이 아니라 마네킹 센서 GT다.
- 특히 `val1`이 있는 `sensor_stream`만 최신 GT 후보로 취급한다.
- CV/YOLOX는 bbox, ROI, posture, mask/bag/head/abdomen 관찰을 돕는 Student/보조 신호다. squeeze 유무 자체를 CV가 마음대로 판정하는 구조로 되돌리면 안 된다.
- operator 식별자는 원문을 세션 산출물에 저장하지 않는다.
- operator hash는 `operator_id_raw.strip().lower()` 후 공백, `_`, `-` 제거한 값을 `MANNEQUIN_OPERATOR_SALT`로 HMAC-SHA256 처리한다.
- 이 앱의 동기화 산출물 이름은 `frame_ts_sync.json`이다. 다른 POC의 `sync_report.json`과 섞으면 안 된다.

## 4. 백엔드 구조

핵심 파일은 `app.py` 하나다.

- 설정/상수
  - `CAMERA_IDS = ("105", "106", "107")`
  - 녹화 기본값: 3초 카운트다운, 30초 녹화, 기본 30fps
  - `go2rtc` 포트: API `11984`, 로컬 RTSP `18554`, WebRTC `18555`
  - `go2rtc.exe`는 `abc_collector_v3\tools\go2rtc\go2rtc.exe`를 참조한다.

- 카메라 경로
  - `.env`의 `RTSP_URL_105`, `RTSP_URL_106`, `RTSP_URL_107`을 읽는다.
  - `Go2RtcRelay`가 go2rtc 설정을 만들고 실행한다.
  - `CameraReader`가 OpenCV로 프레임을 계속 읽고 snapshot을 제공한다.
  - 프론트는 WebRTC를 먼저 시도하고 실패하면 MJPEG로 fallback한다.

- 센서 경로
  - `MannequinSerialReader`: 직접 serial 포트에서 Laerdal 계열 패킷을 읽는다.
  - `AbcSensorBridge`: `ws://127.0.0.1:8010/ws` 같은 외부 WebSocket bridge에서 serial 이벤트를 받는다.
  - `/api/sensor/event`: 외부 합성/테스트 패킷을 HTTP로 주입할 수 있다.
  - `MannequinSensorBuffer`: 모든 이벤트를 보관하되, `category == "sensor_stream"`이고 숫자 `val1`이 있는 이벤트만 latest GT sensor로 본다.

- 녹화 경로
  - `/api/record/start`는 먼저 실험 메타데이터를 검증한다.
  - 그 다음 카메라와 센서 preflight를 확인한다.
  - 센서가 활성화되어 있으면 연결 상태와 최근 `val1` 수신 freshness를 통과해야 녹화를 시작한다.
  - `RecordingController`는 3초 대기 후 각 카메라 MP4 writer를 열고 30초 분량을 쓴다.
  - 각 write index마다 camera별 `source_frame_ts`와 duplicate 여부를 `frame_rows`로 보관한다.

## 5. 프론트엔드 구조

프론트는 `web/index.html`, `web/styles.css`, `web/app.js`의 정적 파일이다. Node/build tool은 없다.

현재 화면은 단순 랜딩 페이지가 아니라 운영 대시보드다.

- 상단: 카메라 수신 상태, relay 상태, 시계
- 중앙: 105/106/107 카메라 wall
- 하단 telemetry: 마네킹 기준값, 표시 환기량, 원본 `val1`, 환기 시간, 패킷 수, 저장 파일
- 우측 rail: 실험 조건 입력, 녹화 버튼, 상태, 실행 전 확인 checklist

실험 조건 폼은 `/api/record/start`로 다음 메타를 보낸다: protocol, scenario, glove, torso, operator, mask, bag, adjunct, lighting, occlusion, manikin, calibration, notes.

## 6. 세션 산출물 계약

새 녹화 세션은 `recordings/yyyyMMddHHmmss/` 아래에 다음 계열 파일을 만든다.

- `105.mp4`, `106.mp4`, `107.mp4`
- `sensor.csv`
- `mannequin_packets.csv`
- `mannequin_packets.jsonl`
- `labels.json`
- `frames_index.csv`
- `frame_ts_sync.json`
- `session.json`

중요한 의미:

- `labels.json`: `source = mannequin_sensor_gt`, 라벨은 `bvm_squeeze`
- `sensor.csv`: 비디오 시간축으로 환산한 `val1`, flow, inspiratory time, ventilation output
- `frames_index.csv`: write index별 camera id, source frame timestamp, duplicate 여부
- `frame_ts_sync.json`: reference camera 105 기준 camera pair offset/drift 요약
- `session.json`: 전체 세션 메타, artifact path, label source, timestamp alignment 설명

현재 작업 폴더의 `recordings/`는 확인 시점 기준 비어 있다. 즉 이 폴더 안에 최신 schema 샘플 세션은 남아 있지 않다.

## 7. 실험 메타데이터와 개인정보 처리

`validate_experiment_meta()`는 다음을 검증한다.

- protocol: `cpr_30_2`, `advanced_airway`, `respiratory_arrest`
- scenario: `null` 또는 1-23 정수
- glove: `bare`, `blue_latex`
- torso: `bare`, `clothed`
- mask: `infant`, `child`, `adult_s`, `adult_m`, `adult_l`
- adjunct: `none`, `opa`, `npa`
- lighting: `normal`, `dim`, `bright`
- occlusion: `none`, `partial`, `heavy`

`operator_id_raw`가 있으면 `MANNEQUIN_OPERATOR_SALT`가 반드시 있어야 한다. `MANNEQUIN_META_REQUIRED=false`여도 operator 입력이 있으면 salt 누락은 hard error다.

`.env`에는 RTSP URL과 센서/bridge/salt 설정이 있다. 분석 중 값은 보지 않고 key만 redacted로 확인했다.

## 8. iPhone LiDAR / RGB-D 방향

현재 실제 Windows 코드에는 `ingest_iphone_session()`이 없다. 즉 iPhone RGB-D 세션을 `recording_AB` 세션 계약으로 가져오는 경로는 아직 구현 전이다.

현재 연구 방향은 다음이다.

- iPhone 캡처 앱에서 RGB, depth, confidence, camera intrinsics/pose, timestamp manifest를 만든다.
- Windows `recording_AB`가 그 export를 ingest해서 PC-side RTSP/마네킹 GT 세션과 연결한다.
- RGB-D/Teacher는 기도확보 자세, mask/bag 위치, abdomen/chest rise 같은 metric과 pseudo-label 생성에 쓴다.
- 배포용 Student는 RGB-only YOLOX 계열로 작게 가져간다.

아직 안 된 것:

- iPhone 앱 원격 start/stop 연동
- iPhone manifest와 PC epoch/frame timeline 동기화 계약
- LiDAR/depth/confidence ingest
- Teacher metric 산출
- Teacher to Student pseudo-label pipeline

## 9. YOLOX-tiny LoRA Student 상태

`yolox_lora_train/`은 별도 학습 디렉토리다.

현재 데이터:

- raw 이미지: 167 jpg
- COCO 변환 결과:
  - train: 134 images, 630 boxes
  - val: 33 images, 161 boxes
- class registry:
  - 0 `abdomen`
  - 1 `face`
  - 2 `bvm_mask`
  - 3 `bvm_bag`

모델/학습:

- YOLOX-tiny, input/test size 416x416
- COCO pretrained `weights/yolox_tiny.pth` 존재
- LoRA rank 8, alpha 16
- backbone feature extractor는 동결하고 neck/head 쪽 LoRA 및 prediction head를 학습한다.
- export 시 `merge_lora()`로 LoRA branch를 base conv에 접어 ONNX/TensorRT/DeepX 친화적인 순정 Conv 구조로 만든다.

현재 남아 있는 run:

- `runs/20260622_184737/`
- `run_manifest.json`과 `train_log.txt`는 있으나 `metrics.jsonl`, `epochs.jsonl`, `run_summary.json`은 비어 있거나 없다.
- 확인 시점에 `python -m yolox.tools.train ... max_epoch 1 ...` 프로세스(PID 23568)가 아직 남아 있었다.
- 따라서 이 run은 완료된 정식 학습 결과로 보면 안 된다. 이전 스모크 학습 검증은 연구노트상 임시 폴더에서 수행 후 삭제된 상태다.

## 10. 현재 검증 결과

이번 분석 중 실행한 검증:

- `python -m py_compile app.py`: 통과
- `python Claude\tests\test_sensor_pipeline.py`: `RESULT: 43 passed, 0 failed`
- `python tests\test_lora_merge.py` in `yolox_lora_train`: `ALL LORA TESTS PASSED`
- `Invoke-RestMethod http://127.0.0.1:8000/health`: 연결 실패

현재 로컬 서버 상태:

- `.server.pid`는 28604를 가리키지만 해당 프로세스는 없다.
- `go2rtc` 프로세스도 확인되지 않았다.
- 즉 현재 앱 서버는 떠 있지 않다.

## 11. 현재 저장소/작업 폴더 상태

이 폴더는 현재 Git 저장소로 인식되지 않는다. `git status`가 `fatal: not a git repository`를 반환했다.

현재 파일 성격:

- tracked 코드처럼 다룰 핵심: `app.py`, `web/`, `Claude/tests/`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `RESEARCH_NOTES.md`, `yolox_lora_train/` 코드
- local config: `.env`는 비밀값이 있어 노출 금지
- runtime/data: `recordings/`, `runtime/`, `_uvicorn.*.log`, `.server.pid`, `__pycache__/`, `*.pyc`
- ML data/artifact: `yolox_lora_train/datasets/`, `weights/`, `runs/`는 연구/학습 산출물 성격이다.

## 12. 현재 위험과 기술 부채

1. 카메라 readiness가 stale frame을 ready로 볼 수 있다.
   - 현재 preflight는 주로 `has_frame`을 본다.
   - `opened`, `error`, `frame_age_seconds`까지 함께 검사해야 live readiness가 더 정확하다.

2. README가 현재 기능보다 오래됐다.
   - README는 아직 단순 105/106/107 recorder 설명에 가깝다.
   - 현재의 센서 GT, 메타데이터, frame sync, YOLOX 학습 디렉토리, iPhone RGB-D 방향이 반영되어 있지 않다.

3. `iphone_lidar/` 이름이 현재 실제 내용과 다르다.
   - 실제 iPhone 앱 소스가 아니라 recorder 스냅샷이라, 다음 작업자가 오해하기 쉽다.

4. iPhone ingest 계약이 아직 없다.
   - 다음 큰 구현 전에는 `session.json` 확장, iPhone manifest 필드, clock alignment, artifact naming을 먼저 문서화해야 한다.

5. 현재 학습 프로세스가 남아 있다.
   - PID 23568은 `yolox.tools.train`로 보인다.
   - 완주 산출물이 없으므로 정상 진행 중인지, 멈춘 것인지 별도 확인이 필요하다.

6. `recordings/`가 현재 비어 있다.
   - 최신 session schema를 검증할 실제 녹화 샘플이 이 폴더에 없다.
   - 세션 계약 변경 전에는 합성 테스트뿐 아니라 실제 1회 녹화 샘플이 필요하다.

## 13. 다음 작업 우선순위

1. 현재 남아 있는 YOLOX train 프로세스 상태를 정리한다.
   - 계속 돌릴 것인지, 중단하고 새 run으로 다시 시작할 것인지 결정해야 한다.

2. README 또는 별도 session contract 문서를 현재 사실에 맞게 갱신한다.
   - 특히 sensor GT boundary, `frame_ts_sync.json`, operator hash, iPhone ingest 미구현 상태를 명확히 해야 한다.

3. stale frame readiness를 고친다.
   - `has_frame`만이 아니라 `opened`, `error`, `frame_age_seconds` 기준을 preflight에 넣고 테스트를 추가한다.

4. iPhone session ingest 설계를 먼저 문서화한다.
   - 입력: RGB mp4/frames, depth, confidence, intrinsics, pose, manifest
   - 출력: 기존 `session.json`/label/session artifact와 어떻게 연결할지
   - clock mapping: PC epoch, iPhone wall time, ARKit timestamp, first committed frame

5. 실제 녹화 샘플을 하나 만들어 최신 schema를 확인한다.
   - `sensor.csv`, `labels.json`, `frames_index.csv`, `frame_ts_sync.json`, `session.json`가 모두 생성되는지 확인한다.

## 14. 최종 판단

지금 우리가 구현하고 있는 것은 단순 카메라 녹화기가 아니다. 핵심 프로젝트는 BVM/기도확보 연구 데이터를 만들기 위한 PC-side acquisition hub이고, source-of-truth는 마네킹 `val1` GT다. 그 위에 iPhone RGB-D를 Teacher 신호로 붙이고, 최종적으로는 RGB-only YOLOX-tiny Student로 배포 가능한 검출기를 만들려는 흐름이다.

현재 완성된 것은 PC-side 3-camera recorder, sensor GT buffer/preflight, metadata privacy gate, frame timestamp artifact, static dashboard, YOLOX-tiny LoRA 학습 scaffold와 4-class 실제 데이터 세팅이다. 아직 완성되지 않은 것은 iPhone ingest, Teacher metric, remote synchronized capture, 실제 배포용 Student 학습 완주/평가다.
