# 미구현 위험요소에 대한 타 산업 방법론 로컬라이징 조사

작성 시점: 2026-06-23 12:13 KST  
대상 프로젝트: `recording_AB` / iPhone LiDAR Teacher / RGB-only Student / YOLOX 4-class detector

## 1. 결론

다른 산업 분야의 유사 사례를 보면 방향은 하나로 모인다. 어려운 문제를 카메라 단일 모델로 직접 맞히려 하지 않는다. 대신 다음 네 가지를 분리한다.

1. **정답 센서 또는 기준 계측값**: 실제로 맞고 틀림을 판정하는 기준이다.
2. **시각 proxy**: 카메라, depth, bbox, pose처럼 정답을 설명하거나 예측하는 보조 신호다.
3. **시간축 상태모델**: 한 프레임이 아니라 전후 상태 변화로 판단한다.
4. **불확실성/unknown 처리**: 가려졌거나 기준이 약하면 억지로 yes/no를 내지 않는다.

우리 프로젝트도 이 구조가 맞다.

- BVM squeeze/ventilation의 최종 기준은 계속 마네킹 `val1`/flow 계열 GT다.
- YOLOX는 `abdomen`, `face`, `bvm_mask`, `bvm_bag` 위치를 잡는 detector다.
- iPhone LiDAR/RGB-D는 chest rise, bag deformation, head/chin/neck geometry의 Teacher metric이다.
- 최종 Student는 RGB-only로 줄일 수 있지만, 학습/검증 단계에서는 센서 GT와 depth Teacher를 버리면 안 된다.

이 조사에서 실제로 가져와 쓸 수 있는 방법론은 다음 9개다.

| 가져올 방법론 | 원래 쓰이는 산업 | 우리 프로젝트 적용 |
|---|---|---|
| Sensor fusion with source-of-truth hierarchy | 자율주행, 로봇 | LiDAR/depth/serial/RGB를 한 frame index와 sync report로 묶기 |
| Occlusion-aware tracking | 자율주행 | BVM mask로 얼굴이 가려진 뒤 airway 상태를 last-visible + decay + indirect evidence로 추적 |
| Occlusion detector + fallback | 운전자 모니터링 | mask occlusion 상태를 먼저 감지하고 direct face cue에서 neck/chest/sensor cue로 전환 |
| Measurement System Analysis, Gage R&R | 산업용 비전 검사/계측 | LiDAR chest-rise, bag-compression proxy가 반복 가능한지 operator/position/session별 검증 |
| Markerless motion capture validation | 스포츠/재활 생체역학 | LiDAR chest displacement를 sensor GT와 비교하고 bias/limits-of-agreement를 남김 |
| Time-series/video assisted annotation | 웨어러블 HAR | squeeze/chest-rise/ventilation event를 frame bbox가 아니라 interval label로 만들기 |
| Verb-object-tool temporal segmentation | 산업 조립/작업 분석 | `airway_maneuver`, `mask_place`, `squeeze`, `compression`, `pause`를 protocol event로 분해 |
| Action Quality Assessment | 스포츠, 재활, 산업 훈련 | 단순 “했음/안했음”이 아니라 quality score와 feedback 요소로 분리 |
| Flow/volume feedback device logic | BVM/응급의료 장비 | 영상 추정은 squeeze proxy까지만, volume/rate/leak은 flow/GT와 결합해 판단 |

## 2. 지금 걱정거리별로 바로 가져올 설계

### 2.1 기도 확보를 어떻게 확인할 것인가

다른 산업의 유사 문제는 운전자 모니터링과 스포츠 생체역학에 가깝다. 운전자 모니터링에서는 눈동자 하나를 항상 직접 보지 못하므로 head pose, gaze region, RGB/IR fallback, occlusion detection을 조합한다. 스포츠 생체역학에서는 markerless video만 믿지 않고 marker-based motion capture나 force plate 같은 기준 장비와 비교해 쓸 수 있는 범위를 정한다.

우리식으로 바꾸면, 기도확보는 “기도 내부가 열렸는지 직접 봤다”가 아니라 **airway-open posture candidate**로 둬야 한다.

권장 상태 정의:

| 상태 | 의미 |
|---|---|
| `NEUTRAL_BASELINE` | 처치 전 중립 자세 |
| `AIRWAY_MANEUVER_VISIBLE` | head tilt/chin lift/jaw/neck 변화가 보이는 구간 |
| `AIRWAY_OPEN_POSE_CANDIDATE` | 자세 proxy가 기도확보 쪽으로 충분히 이동한 상태 |
| `AIRWAY_OPEN_OCCLUDED_MAINTAINED` | mask 때문에 얼굴이 가려졌지만 직전 자세와 간접 증거가 유지되는 상태 |
| `AIRWAY_COLLAPSE_SUSPECTED` | 머리/턱/목 각도 또는 ventilation evidence가 무너지는 상태 |
| `AIRWAY_UNKNOWN` | occlusion이나 품질 저하로 판단 보류 |

우리 데이터에서 볼 신호:

- `face`/`bvm_mask` bbox 전후의 head pose 변화
- chin-neck gap, neck exposure, face-to-abdomen/chest relative angle
- LiDAR depth 기준 head/neck/chest plane 변화
- mask 적용 후에는 직전 open posture 유지 여부와 ventilation evidence

핵심은 **visible phase와 occluded phase를 같은 모델로 판정하지 않는 것**이다. mask 전에는 자세를 보고, mask 후에는 last-visible posture와 간접 증거를 본다.

### 2.2 BVM mask가 얼굴을 가릴 때 기도확보 유지/붕괴를 어떻게 판단할 것인가

자율주행의 occlusion-aware tracking 방법론을 그대로 가져올 수 있다. 자율주행에서는 차량이나 보행자가 버스/건물 뒤로 가려져도 “없다”고 하지 않는다. 마지막 관측 상태를 기반으로 몇 개의 가능한 상태를 유지하고, 새 관측이 들어오면 다시 연결한다.

우리 프로젝트 적용:

1. mask가 얼굴을 덮는 순간을 `OCCLUSION_ENTER`로 표시한다.
2. 그 직전 1-2초의 airway posture score를 저장한다.
3. mask 적용 중에는 얼굴 세부점 대신 다음 간접 신호를 본다.
   - mask bbox가 face 중심에 안정적으로 놓였는지
   - 머리/목/상체의 상대 각도가 유지되는지
   - squeeze 시 chest-rise Teacher metric이 나오는지
   - `val1`/flow가 ventilation event를 만들었는지
4. 시간이 지날수록 last-visible airway score의 신뢰도를 낮춘다.
5. 증거가 약하면 `UNKNOWN_OCCLUDED`로 둔다.

이게 중요한 이유는 mask occlusion 상황에서 binary 판정을 강제하면 false airway-open이 커지기 때문이다. 이 프로젝트에서 더 위험한 오류는 “실패했는데 성공이라고 말하는 것”이다.

### 2.3 BVM squeeze를 어떻게 확인하고, 어느 정도까지 계산 가능한가

BVM feedback device 쪽은 답이 분명하다. BENGI 같은 장비는 bag 모양을 보지 않고 flow sensor로 tidal volume, respiratory rate, inspiratory/expiratory time을 계산한다. ZOLL Real BVM Help 같은 상용 장비도 BVM 사이에 센서를 넣어 ventilation volume/rate 피드백을 준다.

따라서 우리도 다음 경계를 지켜야 한다.

| 항목 | 영상/LiDAR로 가능 | 최종 기준 |
|---|---|---|
| squeeze attempt | 가능. bag bbox/contour/optical flow/depth deformation | CV + temporal model |
| squeeze phase | 가능. compression/release/rebound 주기 | CV + temporal model |
| squeeze intensity proxy | 제한적으로 가능. area delta, depth delta, wrinkle/edge energy | calibration 전에는 proxy |
| tidal volume | 영상만으로 직접 산출 불가 | `val1`/flow 또는 별도 flow sensor |
| oxygen flow | 영상/LiDAR로 산출 불가 | 산소 유량계/장비 metadata |
| mask leak | 영상만으로 확정 불가 | flow/expired volume/val1 + chest rise + visual risk |
| effective ventilation | 영상 단독 확정 금지 | sensor GT 중심 + chest rise + state model |

로컬 구현 이름도 보수적으로 잡는 것이 맞다.

- `squeeze_attempt`
- `squeeze_phase`
- `bag_compression_proxy`
- `visual_mask_seal_risk`
- `chest_rise_candidate`
- `effective_ventilation_gt`

반대로 지금 단계에서 피해야 할 이름:

- `delivered_volume_ml_from_video`
- `oxygen_flow_estimated_from_video`
- `seal_success_from_yolo`
- `airway_open_confirmed_by_rgb`

### 2.4 흉부가 올라오지 않은 기준은 언제 잡아야 하는가

산업용 계측과 생체역학 검증에서는 baseline을 한 번만 잡지 않는다. 장비 설치, 대상 위치, 조명, 센서 각도, operator에 따라 기준점이 흔들리기 때문에 baseline을 **session-specific** 또는 **local baseline**으로 잡는다.

우리 프로젝트 적용:

#### ventilation-only 또는 airway-bvm-only

1. 마네킹과 카메라/iPhone 위치를 고정한다.
2. squeeze 전 1-2초를 `quiet_baseline_pre_squeeze`로 잡는다.
3. chest/abdomen ROI에서 depth plane median, confidence mask, bbox 안정성을 저장한다.
4. squeeze 이후 displacement curve가 baseline noise band를 넘는지 본다.

#### CPR 30:2 포함

한 세션 시작 baseline 하나만 쓰면 안 된다. compression이 들어가면 chest plane이 계속 흔들리고 recoil 후에도 잔류 변위가 남을 수 있다.

권장 방식:

- ventilation window마다 local baseline을 다시 잡는다.
- `compression_end -> recoil_settled -> ventilation_start` 사이의 짧은 안정 구간을 baseline 후보로 둔다.
- baseline 품질이 낮으면 해당 breath는 `chest_rise_unknown`으로 둔다.

즉, “흉부 상승 없음”의 기준은 **처치 전 정적 기준**이 아니라, 해당 ventilation event 직전의 local quiet baseline이다.

### 2.5 30:2 CPR까지 포함하려면 세팅을 바꿔야 하는가

큰 장비 구성을 당장 갈아엎기보다, protocol state와 camera role을 분리하는 쪽이 맞다.

Red Cross/ILCOR 2025 기준에서도 adult cardiac arrest에서 advanced airway가 없는 경우 30 compressions to 2 ventilations가 표준 CPR 선택지로 유지되고, advanced airway가 있으면 ventilation과 compression의 관계가 달라진다. AHA 2025 Adult BLS도 성인 cardiac arrest에서 compressions와 ventilations를 함께 수행하는 것을 다루며, ventilation은 visible chest rise를 만들 정도로 충분하되 hypo/hyperventilation을 피하라고 정리한다.

우리 프로젝트 적용:

| 프로토콜 | 지금 포함 여부 | 이유 |
|---|---|---|
| `ventilation_only` | 즉시 필요 | BVM/chest-rise/flow GT를 가장 깨끗하게 검증 |
| `airway_bvm_only` | 즉시 필요 | airway-mask-squeeze-ventilation 연결 검증 |
| `cpr_30_2_no_advanced_airway` | 다음 단계 | compression artifact와 ventilation 구간 분리 필요 |
| `advanced_airway_continuous_compression` | 나중 | 현재 BVM mask/airway 문제와 다른 프로토콜 |

카메라/센서 역할:

- 105: head/face/mask/airway maneuver 중심
- 106 또는 iPhone LiDAR: chest/abdomen rise와 bag squeeze 중심
- 107: 전체 동작, compression/ventilation phase, operator occlusion QA
- 마네킹 serial: compression/ventilation GT가 가능하면 무조건 수집

세팅 변경의 핵심은 카메라를 더 많이 붙이는 것이 아니라, **compression artifact를 ventilation chest-rise와 분리할 수 있게 protocol event label을 남기는 것**이다.

## 3. 산업별로 가져올 수 있는 방법론

### 3.1 자율주행: multi-sensor fusion과 occlusion-aware tracking

자율주행에서는 카메라, LiDAR, radar가 서로 다른 장단점을 가진다. 카메라는 texture와 contour가 강하지만 거리/속도/악천후에 약하고, radar/LiDAR는 거리와 움직임에 강하지만 sparse하거나 비싼 문제가 있다. 그래서 fusion 연구는 calibration, data alignment, fusion operation을 핵심 문제로 본다.

우리에게 필요한 로컬라이징:

- RGB, depth, confidence, pose, serial, RTSP frame을 하나의 `frame_index` 또는 timestamp mapping으로 묶는다.
- 각 frame마다 `sync_quality`, `depth_confidence`, `camera_frame_age`, `sensor_gt_age`를 저장한다.
- iPhone LiDAR는 “좋은 이미지”가 아니라 metric scale anchor로 쓴다.
- mask occlusion은 “정보 없음”이 아니라 별도 상태로 추적한다.

구현 산출물:

- `session_sync_report.json`
- `frame_quality.csv`
- `occlusion_events.json`
- `teacher_metrics/chest_rise.csv`
- `teacher_metrics/airway_pose.csv`
- `teacher_metrics/bag_deformation.csv`

### 3.2 운전자 모니터링: occlusion detector와 fallback logic

운전자 모니터링은 얼굴이 손, 선글라스, 조명, 그림자, 카메라 위치 때문에 가려지는 문제가 많다. 최근 occlusion-aware DMS 연구는 occlusion을 먼저 감지하고, RGB/IR 같은 다른 modality 또는 더 보수적인 system logic으로 전환한다.

우리에게 필요한 로컬라이징:

- `bvm_mask`가 `face` bbox를 일정 비율 이상 덮으면 `face_occluded_by_mask=true`.
- 이때 face landmark 기반 airway score를 끄고, neck/chest/sensor 기반 상태로 전환한다.
- occlusion 중 airway 판정은 `maintained`, `suspected collapse`, `unknown` 세 가지로만 둔다.

즉, mask가 붙은 뒤에는 “얼굴이 안 보이니 실패”도 아니고 “직전이 좋았으니 계속 성공”도 아니다. **occlusion-aware state**로 다뤄야 한다.

### 3.3 산업용 비전 계측: calibration과 Gage R&R

제조업에서 machine vision으로 치수를 재려면 pixel calibration만으로 끝내지 않는다. 기준 물체/standard로 보정하고, 반복 측정과 operator/환경 변화에 따른 variation을 본다. Gage R&R은 같은 물체를 여러 번 재도 같은 값이 나오는지, operator나 시간/환경이 바뀌어도 버티는지 보는 방식이다.

우리에게 필요한 로컬라이징:

- LiDAR chest-rise displacement가 몇 mm 단위로 안정적인지 확인한다.
- 같은 세팅에서 동일 마네킹/동일 자세를 10회 이상 반복해 baseline noise를 잰다.
- operator, iPhone 위치, 조명, 마스크 위치, 옷/담요 조건을 바꾼 repeatability/reproducibility를 본다.
- 모델 AP보다 먼저 metric 자체가 구분 가능한지 확인한다.

실험 예:

| 조건 | 반복 |
|---|---|
| no squeeze, no chest rise | 10회 |
| squeeze with visible chest rise | 10회 |
| squeeze with mask leak/no chest rise | 10회 |
| CPR compression only | 10회 |
| compression then ventilation | 10회 |

결과 파일:

- `measurement_system_analysis_YYYYMMDD.md`
- `metric_noise_floor.csv`
- `repeatability_report.json`

### 3.4 스포츠/재활 생체역학: markerless motion capture 검증

스포츠와 재활에서는 markerless video motion capture가 싸고 편하지만, gold standard인 marker-based motion capture나 force plate와 비교해 어느 동작/각도에서 쓸 수 있는지 검증한다. 최근 데이터셋도 synchronized multi-camera video, marker-based motion capture, force plate를 같이 제공해 markerless 방법을 검증하도록 만든다.

우리에게 필요한 로컬라이징:

- LiDAR chest-rise curve를 sensor `val1`/flow event와 나란히 놓고 본다.
- 단순 correlation만 보지 말고 bias와 event timing error를 본다.
- frame별 displacement curve가 어떤 구간에서 과대/과소 추정되는지 남긴다.
- chest rise를 ventilation success와 동일시하지 않고, ventilation evidence 중 하나로 둔다.

권장 metric:

- `chest_rise_peak_mm`
- `chest_rise_auc_mm_s`
- `rise_onset_time`
- `sensor_vent_onset_time`
- `onset_delta_ms`
- `baseline_noise_mm`
- `confidence_valid_ratio`

### 3.5 웨어러블 HAR: synchronized video + time-series annotation

웨어러블 activity recognition에서는 센서 시계열만 보고 라벨을 붙이기 어렵다. 그래서 sensor signal과 video를 동기화한 annotation tool을 만들고, ML suggestion으로 start/end point 후보를 띄운 뒤 사람이 수정하는 방식이 쓰인다.

우리에게 필요한 로컬라이징:

- BVM squeeze와 chest rise는 bbox frame label이 아니라 interval/event label로 만든다.
- 영상, depth curve, `val1`/flow curve를 한 타임라인에서 같이 보며 라벨링한다.
- 모델이 제안한 event 후보를 사람이 confirm/reject/revise한다.
- 긴 세션 전체를 수동으로 보지 않고, sensor peak와 bag deformation peak 주변을 우선 검토한다.

이 방식은 현재 데이터 규모가 작을 때 특히 유용하다. 완전 자동 라벨링이 아니라 “annotation assistance”로 두면 실험 신뢰도를 해치지 않는다.

### 3.6 산업 조립/작업 분석: verb-object-tool segmentation

산업 조립 데이터셋은 작업을 한 덩어리 action으로 보지 않는다. 예를 들어 “걷기”, “들기”, “공구 사용”, “부품 장착”을 verb-object-tool 구조와 시간 구간으로 나눠 라벨링한다. OpenMarcie 같은 산업 환경 action recognition 데이터셋도 egocentric video, inertial, thermal, audio를 시간적으로 정렬하고, 의미 있는 작업 segment를 붙인다.

우리에게 필요한 로컬라이징:

- `airway_maneuver`: head tilt/chin lift 수행 구간
- `mask_place`: mask가 face에 접근/접촉/안정화되는 구간
- `bag_squeeze`: bag compression/release 구간
- `effective_ventilation_gt`: sensor GT상 환기 성공 구간
- `chest_rise_candidate`: LiDAR/RGB-D상 흉부 상승 후보 구간
- `chest_compression`: CPR 압박 구간
- `pause_for_breath`: 30:2에서 ventilation을 위한 pause 구간

이렇게 나누면 “BVM을 잘했다”라는 모호한 말 대신, 어떤 단계가 실패했는지 말할 수 있다.

### 3.7 Action Quality Assessment: 하나의 점수보다 원인별 feedback

Action Quality Assessment는 스포츠, 재활, 산업 훈련에서 “동작을 했는가”보다 “얼마나 잘했는가”를 본다. 이 분야의 공통점은 평가를 하나의 classifier로 끝내지 않고, 자세/타이밍/일관성/오류 패턴을 나눠 feedback으로 만든다는 점이다.

우리에게 필요한 로컬라이징:

최종 출력은 다음처럼 분리해야 한다.

- `airway_pose_score`
- `mask_position_score`
- `bag_squeeze_attempt`
- `bag_squeeze_quality_proxy`
- `chest_rise_candidate_score`
- `effective_ventilation_gt`
- `false_effective_risk`
- `unknown_reason`

이 분해가 있어야 연구노트와 사용자 피드백에서 “왜 실패했는지”를 설명할 수 있다.

### 3.8 BVM feedback device: volume/rate/leak은 센서 기반

BENGI 연구와 ZOLL Real BVM Help 같은 장비의 공통점은 BVM 사이에 flow/pressure 계열 센서를 넣어 ventilation 품질을 본다는 점이다. 이 접근은 우리 프로젝트의 GT 경계와 잘 맞는다.

우리에게 필요한 로컬라이징:

- 영상은 squeeze event와 posture/mask context를 설명한다.
- flow/`val1`은 ventilation success와 volume/rate에 가까운 기준을 제공한다.
- leak은 영상만으로 확정하지 않고, expired/inspired volume 차이 또는 sensor evidence가 있을 때만 강하게 말한다.

따라서 연구 주장은 이렇게 써야 한다.

> “RGB-D/YOLOX는 BVM 수행의 시각적 proxy와 Teacher metric을 제공하고, effective ventilation은 마네킹/flow GT와 결합해 판정한다.”

## 4. 지금 추가로 놓치기 쉬운 부분

### 4.1 metric uncertainty를 문서화해야 한다

YOLO AP, chest-rise displacement, bag compression proxy, airway posture score 모두 오차가 있다. 보고서에는 평균 성능뿐 아니라 실패 조건을 같이 써야 한다.

최소 기록:

- lighting condition
- camera/iPhone position
- operator id hash
- mannequin pose/setup
- mask type
- torso clothing
- depth confidence ratio
- session sync offset/drift
- baseline noise

### 4.2 negative control이 부족하면 모델이 쉽게 속는다

꼭 필요한 음성/실패 케이스:

- squeeze 했지만 mask leak으로 chest rise 없음
- squeeze 했지만 airway posture가 무너져 chest rise 없음
- mask는 잘 놓였지만 squeeze 없음
- chest motion은 CPR compression 때문이고 ventilation은 아님
- 손/팔이 bag이나 face를 가려 detection이 흔들림
- LiDAR confidence가 낮아 chest displacement가 깨짐

이 케이스가 없으면 Student는 “bag이 움직였다 = ventilation 성공” 같은 잘못된 shortcut을 배울 수 있다.

### 4.3 protocol version을 데이터에 박아야 한다

`ventilation_only`, `airway_bvm_only`, `cpr_30_2_no_advanced_airway`는 같은 라벨 체계로 섞으면 안 된다. 각 세션에 protocol version을 박고, 모델 평가도 protocol별로 분리해야 한다.

필요 필드:

- `protocol_type`
- `protocol_version`
- `phase_label_schema_version`
- `gt_source`
- `teacher_metric_version`
- `student_model_version`

### 4.4 ONNX/실시간 후처리 parity도 방법론 리스크다

현재 V2 checkpoint native eval과 ONNX 공통 후처리 결과 사이에 차이가 있었다. 연구 주장은 실제 배포 후처리와 같은 조건에서 확인해야 한다.

필요한 검증:

- PyTorch native output vs ONNX output numeric comparison
- NMS/threshold/resize/letterbox parity
- class mapping parity
- FPS/latency under actual camera resolution

### 4.5 unknown을 제품 기능으로 인정해야 한다

의료/훈련 평가에서는 모르는 것을 틀리게 말하는 것보다 모른다고 하는 편이 낫다. 특히 airway와 effective ventilation에서 false positive는 위험하다.

권장 규칙:

- confidence 낮음: `unknown`
- occlusion 심함: `unknown_occluded`
- sync 품질 낮음: `unknown_sync`
- sensor GT 없음: `effective_ventilation_unverifiable`
- chest rise와 sensor GT 충돌: `needs_review`

## 5. 바로 실행할 실험 계약

다음 순서가 가장 안전하다.

### Phase A. ventilation-only measurement sanity

목표: LiDAR chest-rise와 `val1`/flow가 같은 breath event를 보는지 확인한다.

산출물:

- `ventilation_only_session_<id>/`
- `chest_rise_curve.csv`
- `sensor_vent_events.csv`
- `alignment_report.json`
- overlay 이미지 3장 이상

합격 기준:

- squeeze 없는 구간에서 chest-rise false peak가 낮아야 한다.
- sensor ventilation event와 chest-rise peak의 시간 차이가 보고 가능해야 한다.
- baseline noise와 event peak가 분리되어야 한다.

### Phase B. airway + mask occlusion state

목표: mask 전후로 airway visible/occluded 상태 전환을 안정적으로 기록한다.

산출물:

- `airway_state_events.json`
- `mask_occlusion_events.json`
- `airway_pose_teacher.csv`

합격 기준:

- mask 전 visible cue와 mask 후 indirect evidence를 분리해 기록한다.
- 판단 불가 구간이 `unknown`으로 남아야 한다.

### Phase C. BVM squeeze decomposition

목표: squeeze attempt, squeeze phase, compression proxy, effective ventilation GT를 분리한다.

산출물:

- `bag_deformation_curve.csv`
- `squeeze_events_cv.json`
- `effective_ventilation_gt.json`
- `failure_case_review.md`

합격 기준:

- squeeze attempt가 있어도 effective ventilation이 없는 negative case를 포함한다.
- bag proxy와 `val1`/flow를 섞어 하나의 label로 만들지 않는다.

### Phase D. CPR 30:2 protocol

목표: compression artifact와 ventilation chest-rise를 분리한다.

산출물:

- `phase_events.json`
- `compression_events.csv`
- `ventilation_windows.json`
- `local_baseline_report.json`

합격 기준:

- ventilation window마다 local baseline이 잡힌다.
- CPR compression peak를 ventilation chest rise로 오인한 사례가 따로 집계된다.

## 6. 적용 우선순위

1. `frame_index`와 sync report를 실제 최신 세션에서 닫는다.
2. LiDAR chest-rise curve를 sensor GT와 나란히 놓는 재현 산출물을 만든다.
3. airway visible/occluded state schema를 만든다.
4. BVM squeeze를 attempt/quality/effective ventilation으로 분리한다.
5. negative control 세션을 의도적으로 녹화한다.
6. Gage R&R식 반복성 검증을 작게라도 한다.
7. 그 다음 30:2 CPR로 들어간다.

## 7. 참고 출처

- Radar and Camera Fusion for Object Detection and Tracking, 2024: https://arxiv.org/html/2410.19872v1
- Augmented Vehicle Tracking under Occlusions for Decision-Making in Autonomous Driving, 2015: https://robots.engin.umich.edu/publications/egalceran-2015c.pdf
- POSEidon: Face-from-Depth for Driver Pose Estimation, CVPR 2017: https://openaccess.thecvf.com/content_cvpr_2017/papers/Borghi_POSEidon_Face-From-Depth_for_CVPR_2017_paper.pdf
- Occlusion-aware Driver Monitoring System, 2025: https://arxiv.org/pdf/2504.20677
- AWS SageMaker Ground Truth sensor fusion labeling, 2020: https://aws.amazon.com/blogs/machine-learning/labeling-data-for-3d-object-tracking-and-sensor-fusion-in-amazon-sagemaker-ground-truth/
- Synchronised Video, Motion Capture and Force Plate Dataset for Validating Markerless Human Movement Analysis, Scientific Data 2024: https://www.nature.com/articles/s41597-024-04077-3
- Markerless vs marker-based motion capture limits of agreement, Scientific Reports 2023: https://www.nature.com/articles/s41598-023-49360-2
- Machine Vision Considerations for Metrology Applications, Automate: https://www.automate.org/vision/industry-insights/machine-vision-considerations-for-metrology-applications
- Understanding Measurement System Analysis / Gage R&R, Instron: https://www.instron.com/wp-content/uploads/2024/07/understanding-gage-r-and-r-concepts-and-its-significance-for-instron-systems.pdf
- Assisting annotators of wearable activity recognition datasets, Frontiers 2025: https://www.frontiersin.org/journals/computer-science/articles/10.3389/fcomp.2025.1696178/full
- OpenMarcie industrial multimodal action recognition dataset, 2026: https://arxiv.org/html/2603.02390v1
- A Decade of Action Quality Assessment, 2025: https://arxiv.org/html/2502.02817v1
- AHA 2025 Adult Basic Life Support: https://cpr.heart.org/en/resuscitation-science/cpr-and-ecc-guidelines/adult-basic-life-support
- Red Cross CPR Techniques and Process, ILCOR 2025: https://guidelines.redcross.org/guidelines-database/cpr-techniques-and-sequence/
- BENGI BVM tidal volume feedback device, BMC Biomedical Engineering 2022: https://link.springer.com/article/10.1186/s42490-022-00066-y
- ZOLL Real BVM Help: https://www.zoll.com/en-us/about/medical-technology/bvm-help
