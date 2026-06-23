# 방법론 외부 사례 조사 및 진행 방향 판단

작성 시점: 2026-06-22 18:54 KST  
대상 프로젝트: `recording_AB` / iPhone RGB-D ingest / YOLOX-tiny Student detector

## 1. 결론

현재 방향은 계속 진행해도 된다. 다만 "카메라가 squeeze를 판정한다"가 아니라 "마네킹 센서 GT가 squeeze/ventilation의 기준이고, 카메라는 자세/물체/시야/ROI를 설명한다"는 경계를 유지해야 한다. 이 경계는 최신 CPR/인공호흡 평가 연구와 더 잘 맞는다.

권장 방향은 다음이다.

1. 마네킹 `val1`/flow 계열 센서를 BVM ventilation의 source of truth로 둔다.
2. RGB/RTSP/프레임 timestamp는 GT를 비디오 시간축에 붙이는 관측 계층으로 둔다.
3. iPhone RGB-D/LiDAR는 Teacher/검증/metric 추출용으로 먼저 쓴다.
4. 최종 배포 모델은 RGB-only Student로 줄인다.
5. LoRA는 빠른 baseline으로 유지하되, 데이터가 늘면 full fine-tuning과 반드시 비교한다.

즉, 큰 방향은 맞다. 지금 부족한 것은 방법론 자체가 아니라 동기화 검증, 데이터 규모, holdout 설계, iPhone ingest 계약, 실제 Student 평가다.

## 2. 현재 우리 방법론을 외부 연구 언어로 바꾸면

우리 구조는 다음 연구 패턴과 같다.

- Instrumented ground truth: 마네킹/센서/flow 값을 행동 라벨의 기준으로 삼는다.
- Multi-view/multimodal acquisition: 여러 카메라와 RGB-D를 함께 저장한다.
- Teacher-to-student reduction: 학습/검증 때는 센서와 depth를 쓰되, 배포 때는 RGB-only 모델을 사용한다.
- Skill/action quality assessment: 행동 자체의 존재뿐 아니라 정확도, timing, volume, posture를 평가한다.
- Parameter-efficient adaptation: 작은 도메인 데이터에 pretrained detector를 효율적으로 적응시킨다.

이 조합은 응급처치에만 있는 것이 아니라 HAR, egocentric action recognition, RGB-D segmentation/depth completion, remote sensing object detection에서도 반복되는 패턴이다.

## 3. 유사/관련 최신 사례

### 3.1 CPR-Coach, CVPR 2024

링크: https://openaccess.thecvf.com/content/CVPR2024/papers/Wang_CPR-Coach_Recognizing_Composite_Error_Actions_based_on_Single-class_Training_CVPR_2024_paper.pdf

핵심:

- CPR skill assessment를 vision-based system으로 풀었다.
- 4개 시점, RGB/optical flow/pose modality를 포함한 CPR-Coach dataset을 만들었다.
- 단일 오류 행동 13종과 복합 오류 행동 74종을 정의했다.
- 의료 skill assessment는 데이터 부족과 전문 annotation 비용이 크기 때문에, restricted supervision과 multi-view capture를 사용했다.

우리와의 관련성:

- 3-camera/다시점 수집 방향은 타당하다.
- medical skill은 단순 classification보다 fine-grained error/action assessment로 가야 한다.
- 다만 CPR-Coach는 compression 중심이고, 우리는 ventilation/BVM squeeze와 mask/bag/head/abdomen을 본다. 그대로 복제할 사례는 아니지만, "다시점 비디오 + 전문/기준 라벨 + 제한된 데이터에서 일반화"라는 프레임은 거의 동일하다.

판단:

- 우리도 단일 bbox 학습에서 끝내면 약하다.
- 최종적으로는 action/event 단위 metric과 composite error 또는 scenario-level 평가로 확장해야 한다.

### 3.2 Deep-Learning-Based CPR Action Standardization, Sensors 2024

링크: https://www.mdpi.com/1424-8220/24/15/4813

핵심:

- OpenPose로 자세를 보고, wristband marker와 depth algorithm으로 compression depth/count/frequency를 계산했다.
- custom CPR dataset을 만들고 edge deployment까지 고려했다.
- reported mAP0.5 97.04%, depth/count/frequency 정확도도 제시했다.

우리와의 관련성:

- marker/object detection + depth/motion metric + edge deployment라는 구성이 우리 RGB-D Teacher -> RGB Student 방향과 비슷하다.
- 이 연구도 "영상만 예쁘게 보는 것"이 아니라, 실시간 metric과 deployment까지 고려한다.

판단:

- iPhone depth를 단순 시각화로 끝내지 말고, head extension, mask position, bag compression, abdomen/chest movement 같은 metric으로 정리해야 한다.
- YOLOX Student는 bbox만이 아니라 downstream metric 산출 가능성으로 평가해야 한다.

### 3.3 Automating the Evaluation of Artificial Respiration, Applied Sciences 2026

링크: https://www.mdpi.com/2076-3417/16/1/555

핵심:

- fixed monocular camera와 standardized resuscitation manikin을 사용했다.
- 40명 volunteer가 CPR 30:2 cycle을 수행했고, chest compression / airway opening / rescue breaths / non-CPR action으로 clip을 구성했다.
- correct/incorrect label은 certified first-aid instructor 두 명이 붙였고, manikin sensor feedback을 기준으로 삼았다.

우리와의 관련성:

- 우리와 가장 직접적으로 닮은 사례다.
- 인공호흡/기도개방/압박을 비디오로 평가하되, correct/incorrect 판정에 마네킹 센서 feedback을 사용한다.
- 이는 "CV가 squeeze를 추론하지 말고, 센서 GT를 기준으로 하자"는 우리 원칙을 지지한다.

판단:

- sensor GT 라벨과 영상 라벨을 분리해서 저장하는 현재 방식은 맞다.
- 다만 우리 bbox 라벨도 한 명이 단독으로 계속 만들기보다는, 최소한 일부 holdout에는 2인 검수 또는 revision annotation을 둬야 한다.

### 3.4 AHA 2025 CPR/ECC Guidelines Highlights

링크: https://cpr.heart.org/-/media/CPR-Files/2025-documents-for-cpr-heart-edits-posting/Resuscitation-Science/252500_Hghlghts_2025ECCGuidelines.pdf

핵심:

- 2025 guideline highlights에서 CPR training 중 feedback devices 사용을 healthcare professionals와 lay rescuers 모두에게 권장한다고 정리했다.
- feedback device가 CPR quality metrics 개선에 효과가 있었다고 요약한다.
- VR/AR/gamified learning도 언급하지만 evidence는 더 약하다고 본다.

우리와의 관련성:

- 센서/피드백 장치를 기준값으로 삼는 접근은 resuscitation training 쪽에서 주류 방향이다.
- 비전/AI는 센서 기준값을 대체하기보다, 더 싸고 넓게 배포 가능한 interface와 자동 분석층으로 두는 편이 보수적이다.

판단:

- 지금처럼 센서 GT를 "강한 기준"으로 두고, CV는 보조/Student로 두는 것이 clinical training 맥락에서 더 방어 가능하다.

### 3.5 BENGI BVM tidal volume feedback device, BMC Biomedical Engineering 2022

링크: https://link.springer.com/article/10.1186/s42490-022-00066-y

핵심:

- BVM ventilation은 user error에 취약하고 hyper/hypoventilation 위험이 있다.
- device가 flow sensor로 tidal volume을 계산하고, rate/inspiratory-expiratory timing에 대한 visual/audio feedback을 제공한다.
- validation은 ventilator, BVM, manikin test lung으로 했고 tidal volume measurement가 true delivered volume에 가깝게 맞았다.

우리와의 관련성:

- BVM에서 핵심 GT는 bag 모양이 아니라 실제 delivered tidal volume/flow/timing이다.
- 우리 `val1`, `vent_flow_rate_ml_s`, `inspiratory_time_sec` 저장 방향과 맞다.

판단:

- YOLOX가 bag bbox를 잘 잡아도 ventilation 성공을 바로 말하면 안 된다.
- bag/mask/head/abdomen detection은 volume GT와 결합해서 explanatory feature로 써야 한다.

### 3.6 Sensor-free camera-only CPR assessment, 2025

링크: https://link.springer.com/article/10.1007/s44443-025-00223-y

핵심:

- multi-angle video와 YOLO 계열 detection, matching algorithm으로 compression position/rate/depth를 평가했다.
- specialized sensor 없이 camera-only 평가를 목표로 했다.

우리와의 관련성:

- 이 연구는 센서를 쓰지 않는 반대 방향처럼 보이지만, 실제로는 "다시점 비디오로 skill metric을 자동화한다"는 점에서 우리와 닿아 있다.
- 우리는 센서를 버리는 대신, 학습/검증 단계에서 센서를 GT로 잡고 나중에 RGB-only Student로 줄이는 더 보수적인 경로다.

판단:

- 최종 배포가 sensor-free 또는 RGB-only가 되는 것은 타당하다.
- 하지만 지금 단계에서 센서를 제거하면 라벨 신뢰도와 검증력이 떨어진다.

### 3.7 Multimodal Distillation for Egocentric Action Recognition, ICCV 2023

링크: https://openaccess.thecvf.com/content/ICCV2023/papers/Radevski_Multimodal_Distillation_for_Egocentric_Action_Recognition_ICCV_2023_paper.pdf

핵심:

- training에는 RGB, optical flow, audio, object detection 같은 여러 modality teacher를 쓰고, inference는 RGB frame만 쓰는 Student를 학습했다.
- multimodal teacher ensemble에 접근하는 수준의 RGB-only Student 성능을 보였다.

우리와의 관련성:

- iPhone RGB-D, 센서 GT, multi-view camera를 Teacher/annotation/validation에 쓰고, 최종은 RGB-only YOLOX/TCN으로 줄이려는 방향과 정확히 같은 패턴이다.

판단:

- "학습 때 센서/depth를 쓰고 배포 때 RGB만 쓴다"는 전략은 연구적으로 자연스럽다.
- 단, teacher signal quality가 낮으면 Student도 오염된다. 그래서 sync와 GT artifact 품질이 먼저다.

### 3.8 HFD-Teacher, ICCV 2025

링크: https://openaccess.thecvf.com/content/ICCV2025/papers/Yang_HFD-Teacher_High-Frequency_Depth_Distillation_from_Depth_Foundation_Models_for_Enhanced_ICCV_2025_paper.pdf

핵심:

- RGB와 sparse depth를 입력으로 depth completion을 수행하고, frozen depth foundation model을 teacher로 활용했다.
- sparse depth는 metric scale anchor 역할을 하며, RGB만으로 생기는 scale unreliability를 보완한다.

우리와의 관련성:

- iPhone LiDAR/depth를 "metric anchor"로 쓰고 RGB Student를 만드는 발상과 가깝다.
- depth는 bag/head/abdomen의 실제 3D displacement나 거리/각도 metric을 만드는 데 강하다.

판단:

- iPhone LiDAR를 단순 카메라 하나 더로 취급하지 말고, metric scale anchor로 써야 한다.
- 다만 iPhone ARKit timestamp와 Windows epoch clock mapping을 명확히 해야 한다.

### 3.9 Human Activity Recognition survey, Sensors 2025

링크: https://www.mdpi.com/1424-8220/25/13/4028

핵심:

- 2014-2025 HAR 연구를 single-modality와 multi-modality, fusion/co-learning, human-object interaction, activity detection 관점으로 정리했다.

우리와의 관련성:

- 우리 문제는 medical HAR + object interaction + sensor-supervised labeling이다.
- 센서, vision, depth를 각각 따로 보는 것이 아니라, 역할을 나눠 co-learning/teacher-student로 가는 것이 최신 HAR 큰 흐름과 맞다.

판단:

- 데이터 스키마를 처음부터 multimodal/time-series로 설계한 것은 맞다.
- session contract와 annotation convention을 지금 문서화해야 나중에 모델 실험이 꼬이지 않는다.

### 3.10 Best-practice video annotation for wearable-sensor HAR, 2026/in press

링크: https://www.mhealthgroup.org/publications.html

핵심:

- wearable sensor HAR에서 ground-truth activity annotation을 만들기 위해 video를 annotation하는 관행을 다뤘다.
- gold-standard는 두 annotator가 독립 annotation하고 domain expert가 불일치를 해결하는 방식이다.
- 더 실용적인 silver-standard로 1차 annotator 결과를 2차 annotator가 revision하는 방식을 제안했고, 단일 annotator보다 agreement가 높았다.

우리와의 관련성:

- 현재 bbox 라벨과 future event label QA에 그대로 적용할 수 있다.
- 센서 GT가 있어도 bbox/pose/mask seal 같은 시각 라벨은 human QA가 필요하다.

판단:

- 전체 라벨을 2인 독립 annotation할 필요는 없더라도, validation set과 애매한 class에는 revision protocol을 둬야 한다.

### 3.11 LoRA-Det, 2024

링크: https://arxiv.org/html/2406.02385v1

핵심:

- object detection에서 PEFT/LoRA를 적용했다.
- 일부만 업데이트해 full fine-tuning에 가까운 성능을 얻었지만, normal-size detector에서는 rank 선택과 layer 배치가 중요하다고 강조했다.
- convolution/FPN/RPN/head 일부는 full fine-tuning과 섞는 hybrid strategy를 쓴다.

우리와의 관련성:

- YOLOX-tiny + LoRA 방향은 "안 되는 이상한 선택"은 아니다.
- 하지만 tiny CNN detector에서 LoRA가 항상 full fine-tuning을 대체한다고 보면 위험하다.

판단:

- 현재 LoRA는 빠른 실험/추적성/적은 데이터에서의 baseline으로 적절하다.
- 실제 성능 판단은 반드시 `LoRA vs full FT vs frozen backbone linear/head-only` ablation으로 해야 한다.

## 4. 현재 방향이 맞는 지점

1. 센서 GT를 먼저 고정한 점이 맞다.
   - BVM/ventilation 성공은 volume/flow/timing 문제라서 영상만으로 라벨을 만드는 것보다 센서 GT가 더 방어 가능하다.

2. 비디오를 동시 저장하고 timestamp artifact를 남기는 점이 맞다.
   - CPR/인공호흡 연구들도 video + manikin/sensor/instructor labels를 붙여 평가한다.

3. RGB-D를 Teacher로 두고 RGB-only Student로 줄이는 점이 맞다.
   - multimodal distillation과 depth teacher 계열 연구와 같은 방향이다.

4. YOLOX 같은 lightweight detector를 쓰는 점이 맞다.
   - 최종 실시간/edge deployment를 염두에 두면 큰 VLM보다 작고 통제 가능한 detector가 낫다.

5. `frame_ts_sync.json`, `session.json`, `labels.json`처럼 artifact를 명시한 점이 맞다.
   - 데이터셋 연구에서 재현성과 annotation provenance가 중요하다.

## 5. 현재 방향에서 보완해야 할 지점

### 5.1 동기화는 아직 약하다

현재 `frame_ts`는 PC arrival time이지 camera exposure time이 아니다. 이 제한을 이미 `frame_ts_sync.json`에 적어둔 것은 좋지만, iPhone RGB-D까지 붙이면 더 엄격한 sync 계약이 필요하다.

필요한 것:

- PC command sent epoch
- iPhone received wall time
- ARKit timestamp at receive
- first committed frame index
- 가능하면 LED/flash/audio sync event
- camera별 offset/drift report

### 5.2 167장 bbox 데이터로 방법론 성공을 말하면 안 된다

현재 학습 데이터는 detector scaffold 검증에는 충분하지만, 연구 주장에는 부족하다.

필요한 것:

- session/operator/scenario/view 기준 holdout
- 연속 프레임 랜덤 split 금지
- class histogram과 occlusion/lighting/glove/mask별 slice metric
- bbox AP뿐 아니라 downstream event metric

### 5.3 LoRA는 baseline이지 결론이 아니다

YOLOX-tiny는 5M급 작은 모델이라 LoRA의 이점이 LLM/ViT만큼 명확하지 않다. LoRA-Det 사례도 normal-size detector PEFT는 layer/rank 설계가 중요하다고 본다.

필요한 비교:

- LoRA rank 4/8/16
- head-only
- backbone frozen + full neck/head
- full fine-tuning
- 동일 split에서 AP, recall, false-positive, latency 비교

### 5.4 label taxonomy가 아직 안정되지 않았다

초기 가정은 `bvm_bag/bvm_mask/head/torso/hand`였고 실제 데이터는 `abdomen/face/bvm_mask/bvm_bag`였다. 데이터가 진실원인 것은 맞지만, taxonomy drift는 문서화해야 한다.

필요한 것:

- `data.yaml`을 source of truth로 유지
- 변경 시 migration note
- Teacher metric과 class 정의 연결

### 5.5 sensor GT와 CV output을 섞어 말하면 안 된다

좋은 bag bbox, 좋은 mask bbox가 곧 ventilation success는 아니다. 실제 ventilation label은 `val1`/flow/timing에서 나온다. CV는 원인/자세/상태를 설명하는 신호다.

## 6. 내가 보는 진행 판단

| 영역 | 판단 | 이유 |
|---|---|---|
| 마네킹 `val1` GT 중심 | 계속 진행 | BVM/ventilation에서 가장 방어 가능한 기준 |
| 3-camera RTSP 수집 | 계속 진행 | CPR-Coach 등 multi-view 사례와 맞음 |
| iPhone RGB-D Teacher | 계속 진행 | depth는 metric geometry anchor로 유효 |
| RGB-only Student | 계속 진행 | deployment 현실성과 multimodal distillation 흐름에 맞음 |
| YOLOX-tiny | 계속 진행 | 실시간/edge target에 실용적 |
| LoRA | baseline으로 진행 | 단독 결론은 금물, full FT 비교 필요 |
| 현재 데이터 규모 | 불충분 | scaffold 검증 수준, 연구 검증 수준 아님 |
| 현재 sync 수준 | 초기 단계 | iPhone/Windows clock alignment 필요 |

## 7. 추천 다음 순서

1. `recording_AB` session contract 문서 작성
   - artifact schema, timestamp 의미, GT boundary, privacy boundary를 고정한다.

2. stale frame readiness 수정
   - `has_frame`만 보지 말고 `opened`, `error`, `frame_age_seconds`를 preflight에 넣는다.

3. 실제 최신 schema 녹화 샘플 1개 생성
   - `sensor.csv`, `labels.json`, `frames_index.csv`, `frame_ts_sync.json`, `session.json`가 모두 실제로 생기는지 확인한다.

4. iPhone session ingest MVP
   - RGB/depth/confidence/pose/manifest를 읽고 기존 session artifact와 연결한다.

5. sync event 도입
   - 최소 LED/flash/audio 또는 command event로 PC camera/iPhone/센서 offset을 측정한다.

6. 데이터 split 정책 고정
   - session/operator/scenario 기준 holdout으로 leakage를 막는다.

7. Student detector ablation
   - LoRA, full fine-tuning, head-only, frozen backbone을 같은 split에서 비교한다.

8. event-level metric 정의
   - bbox AP만 보지 말고, squeeze event recall, video-time alignment error, effective ventilation classification, mask/bag/head/abdomen slice별 성능을 본다.

## 8. 최종 답

지금 방향은 잘 잡았다. 특히 "마네킹 센서 GT를 라벨 source of truth로 두고, RGB-D/다시점 영상은 설명과 Student 학습에 쓴다"는 구조는 최신 CPR/인공호흡 평가 연구와 broader multimodal distillation 연구 흐름에 맞다.

하지만 지금 단계에서 "우리는 이미 방법론을 검증했다"고 말하면 안 된다. 현재는 acquisition/scaffold/초기 데이터 세팅이 된 상태다. 방법론 검증으로 가려면 동기화 artifact, 실제 세션 샘플, iPhone ingest, session-level holdout, LoRA/full-FT 비교, event-level metric까지 닫아야 한다.

내 판단은 "방향 유지, 검증 체계 강화"다. 방향을 바꿀 필요는 없고, 오히려 지금 정한 GT/CV 경계를 더 엄격하게 문서화하고 실험 설계를 닫는 것이 맞다.

## 9. 참고한 주요 출처

- CPR-Coach, CVPR 2024: https://openaccess.thecvf.com/content/CVPR2024/papers/Wang_CPR-Coach_Recognizing_Composite_Error_Actions_based_on_Single-class_Training_CVPR_2024_paper.pdf
- Deep-Learning-Based CPR Action Standardization, Sensors 2024: https://www.mdpi.com/1424-8220/24/15/4813
- Automating the Evaluation of Artificial Respiration, Applied Sciences 2026: https://www.mdpi.com/2076-3417/16/1/555
- AHA 2025 Guidelines Highlights: https://cpr.heart.org/-/media/CPR-Files/2025-documents-for-cpr-heart-edits-posting/Resuscitation-Science/252500_Hghlghts_2025ECCGuidelines.pdf
- BENGI BVM feedback device, BMC Biomedical Engineering 2022: https://link.springer.com/article/10.1186/s42490-022-00066-y
- Sensor-free Camera-only CPR assessment, 2025: https://link.springer.com/article/10.1007/s44443-025-00223-y
- Multimodal Distillation for Egocentric Action Recognition, ICCV 2023: https://openaccess.thecvf.com/content/ICCV2023/papers/Radevski_Multimodal_Distillation_for_Egocentric_Action_Recognition_ICCV_2023_paper.pdf
- HFD-Teacher, ICCV 2025: https://openaccess.thecvf.com/content/ICCV2025/papers/Yang_HFD-Teacher_High-Frequency_Depth_Distillation_from_Depth_Foundation_Models_for_Enhanced_ICCV_2025_paper.pdf
- HAR modalities survey, Sensors 2025: https://www.mdpi.com/1424-8220/25/13/4028
- mHealth video annotation / HAR publication listing: https://www.mhealthgroup.org/publications.html
- LoRA-Det, 2024: https://arxiv.org/html/2406.02385v1
