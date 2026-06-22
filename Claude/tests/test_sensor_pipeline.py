"""코드단 검증: 마네킹 센서 패킷 파이프라인 + 녹화 프리플라이트 로직.

실제 마네킹 하드웨어 없이 합성 패킷으로 다음을 검증한다.
- normalize_external_sensor_payload (외부 API 패킷 정규화)
- MannequinSensorBuffer (latest_sensor 선별)
- sensor_api_status gt_ready 공식 (연결 + val1 최신성)
- recording_preflight_status (카메라/마네킹 차단 조건)
- validate_experiment_meta (세션 메타 검증 + operator 해시)
- frames_index.csv / frame_ts_sync.json 산출

실행: python Claude/tests/test_sensor_pipeline.py
import만으로 COM 포트/스레드/go2rtc를 건드리지 않는다(전부 .start()에서만 열림).
"""
import json
import os
import sys
import time
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra: object = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   -> {extra}")


print("== 1. normalize_external_sensor_payload ==")
e = app.normalize_external_sensor_payload({"val1": 500})
check("val1 -> float 500.0", e.get("val1") == 500.0, e)
check("ts is float", isinstance(e.get("ts"), float))
check("category == sensor_stream", e.get("category") == "sensor_stream")
check("source == external_api", e.get("source") == "external_api")
e2 = app.normalize_external_sensor_payload({"volume_ml": 480})
check("volume_ml alias -> val1 480.0", e2.get("val1") == 480.0, e2)
e3 = app.normalize_external_sensor_payload({"foo": 1})
check("val1/volume_ml 없으면 val1 키 없음(엔드포인트가 거절)", "val1" not in e3, e3)

print("== 2. MannequinSensorBuffer latest_sensor 선별 ==")
buf = app.SENSOR_BUFFER
buf.add({"ts": time.time(), "val1": 512.0, "category": "sensor_stream", "type_byte": "0xd0"})
st = buf.status()
ls = st.get("latest_sensor") or {}
check("latest_sensor.val1 == 512.0", ls.get("val1") == 512.0, ls)
check("latest_sensor.ts 보존(숫자)", isinstance(ls.get("ts"), (int, float)), ls)
check("sensor_packets >= 1", st.get("sensor_packets", 0) >= 1, st.get("sensor_packets"))
# val1 없는 비-센서 패킷은 latest_sensor 후보가 아님
buf.add({"ts": time.time(), "category": "raw", "type_byte": "0xa6"})
st2 = buf.status()
check("non-sensor 패킷은 latest_sensor 안 바꿈", (st2.get("latest_sensor") or {}).get("val1") == 512.0, st2.get("latest_sensor"))

print("== 3. sensor_api_status gt_ready 공식 (연결 + 최신성) ==")
max_age = app.env_float("MANNEQUIN_GT_READY_MAX_AGE_SEC", 10.0)
# 코드단에서 연결 상태를 강제(실제 브리지/직렬 없이 공식만 검증)
forced = None
if app.ABC_SENSOR_BRIDGE is not None:
    app.ABC_SENSOR_BRIDGE.connected = True
    forced = "bridge"
elif app.SENSOR_READER is not None:
    app.SENSOR_READER.connected = True
    forced = "serial"
check("센서 리더 객체 존재(import-safe 생성)", forced is not None, "bridge/serial 모두 None")

buf.add({"ts": time.time(), "val1": 505.0, "category": "sensor_stream", "type_byte": "0xd0"})
s_fresh = app.sensor_api_status()
check("연결 + 최신 val1 -> gt_ready True", s_fresh["gt_ready"] is True,
      {"gt_ready": s_fresh["gt_ready"], "age": s_fresh["latest_sensor_age_sec"], "connected": s_fresh["connected"]})
_age = s_fresh["latest_sensor_age_sec"]
check("latest_sensor_age_sec < 2s", _age is not None and _age < 2.0, _age)

buf.add({"ts": time.time() - (max_age + 20), "val1": 99.0, "category": "sensor_stream", "type_byte": "0xd0"})
s_stale = app.sensor_api_status()
check("연결됐어도 오래된 val1 -> gt_ready False", s_stale["gt_ready"] is False,
      {"gt_ready": s_stale["gt_ready"], "age": s_stale["latest_sensor_age_sec"]})

# 연결 끊김이면 최신 val1이어도 gt_ready False
if app.ABC_SENSOR_BRIDGE is not None:
    app.ABC_SENSOR_BRIDGE.connected = False
if app.SENSOR_READER is not None:
    app.SENSOR_READER.connected = False
buf.add({"ts": time.time(), "val1": 500.0, "category": "sensor_stream", "type_byte": "0xd0"})
s_disc = app.sensor_api_status()
check("연결 끊김 -> gt_ready False", s_disc["gt_ready"] is False, {"connected": s_disc["connected"]})

print("== 4. recording_preflight_status 차단 조건 ==")
cams_ok = [{"camera_id": c, "has_frame": True} for c in ("105", "106", "107")]
cams_missing = [
    {"camera_id": "105", "has_frame": True},
    {"camera_id": "106", "has_frame": False},
    {"camera_id": "107", "has_frame": True},
]
sensor_ready = {"enabled": True, "connected": True, "gt_ready": True, "gt_ready_max_age_sec": max_age,
                "latest_sensor_age_sec": 0.5, "buffer": {"packets": 100, "sensor_packets": 10}}
sensor_no_val1 = {"enabled": True, "connected": True, "gt_ready": False, "gt_ready_max_age_sec": max_age,
                  "latest_sensor_age_sec": None, "buffer": {"packets": 100, "sensor_packets": 0}}
sensor_disconn = {"enabled": True, "connected": False, "gt_ready": False, "gt_ready_max_age_sec": max_age,
                  "latest_sensor_age_sec": None, "buffer": {"packets": 0, "sensor_packets": 0}}
sensor_off = {"enabled": False, "connected": False, "gt_ready": False, "gt_ready_max_age_sec": max_age, "buffer": {}}

pf = app.recording_preflight_status(cams_ok, sensor_ready)
check("카메라3/3 + 마네킹 정상 -> ready_for_recording True", pf["ready_for_recording"] is True, pf["blockers"])
check("정상이면 blockers 비어있음", pf["blockers"] == [], pf["blockers"])
check("checks.ready_cameras == 3", pf["checks"]["ready_cameras"] == 3, pf["checks"])

pf2 = app.recording_preflight_status(cams_ok, sensor_no_val1)
check("val1 패킷 없음 -> not ready", pf2["ready_for_recording"] is False)
check("val1 차단 사유 포함", any("val1" in b for b in pf2["blockers"]), pf2["blockers"])

pf3 = app.recording_preflight_status(cams_ok, sensor_disconn)
check("마네킹 연결 안됨 -> not ready", pf3["ready_for_recording"] is False)
check("연결 차단 사유 포함", any("연결" in b for b in pf3["blockers"]), pf3["blockers"])

pf4 = app.recording_preflight_status(cams_missing, sensor_ready)
check("카메라 1대 누락 -> not ready", pf4["ready_for_recording"] is False)
check("누락 카메라(106) 차단 사유에 명시", any("106" in b for b in pf4["blockers"]), pf4["blockers"])

pf5 = app.recording_preflight_status(cams_ok, sensor_off)
check("센서 비활성화 -> 센서 검사 건너뛰고 ready True", pf5["ready_for_recording"] is True, pf5["blockers"])

print("== 5. experiment meta 검증 + operator 해시 ==")
old_salt = os.environ.get("MANNEQUIN_OPERATOR_SALT")
old_meta_required = os.environ.get("MANNEQUIN_META_REQUIRED")
os.environ["MANNEQUIN_OPERATOR_SALT"] = "unit-test-salt"
os.environ["MANNEQUIN_META_REQUIRED"] = "true"
base_experiment = {
    "protocol_type": "respiratory_arrest",
    "scenario_id": 3,
    "glove_condition": "bare",
    "torso_clothing": "bare",
    "operator_id_raw": "operator-a",
    "mask_size": "adult_m",
    "bag_type": "adult",
    "adjunct_use": "none",
    "lighting": "normal",
    "occlusion_severity": "none",
    "manikin_type": "resusci_anne",
    "calib_version": "none",
    "notes": "unit test",
}
meta, errors = app.validate_experiment_meta({"experiment": base_experiment})
check("정상 메타 -> errors 없음", errors == {}, errors)
check("operator_id_hash 64자 hex", isinstance(meta.get("operator_id_hash"), str) and len(meta["operator_id_hash"]) == 64, meta)
check("operator_id_raw 미저장", "operator_id_raw" not in json.dumps(meta), meta)
hash_a = app.operator_id_hash("operator-a", "unit-test-salt")
hash_b = app.operator_id_hash(" Operator-A ", "unit-test-salt")
hash_c = app.operator_id_hash("operatora", "unit-test-salt")
check("대소문자/공백/하이픈 변형 -> 동일 해시", hash_a == hash_b == hash_c, (hash_a, hash_b, hash_c))

missing_meta, missing_errors = app.validate_experiment_meta({"experiment": {}})
check("필수 누락 -> error", "protocol_type" in missing_errors and "operator_id_raw" in missing_errors, missing_errors)
bad_meta, bad_errors = app.validate_experiment_meta({"experiment": {**base_experiment, "protocol_type": "bad"}})
check("enum 오값 -> error", "protocol_type" in bad_errors, bad_errors)
null_meta, null_errors = app.validate_experiment_meta({"experiment": {**base_experiment, "scenario_id": None}})
check("scenario_id null 허용", null_errors == {} and null_meta["scenario_id"] is None, (null_meta, null_errors))
check("scenario_id null -> scenario_unspecified", null_meta["scenario_unspecified"] is True, null_meta)

os.environ.pop("MANNEQUIN_OPERATOR_SALT", None)
os.environ["MANNEQUIN_META_REQUIRED"] = "false"
no_salt_meta, no_salt_errors = app.validate_experiment_meta({"experiment": base_experiment})
check("salt 없음 + operator 입력 -> META_REQUIRED=false여도 error", "operator_id_raw" in no_salt_errors, no_salt_errors)

if old_salt is None:
    os.environ.pop("MANNEQUIN_OPERATOR_SALT", None)
else:
    os.environ["MANNEQUIN_OPERATOR_SALT"] = old_salt
if old_meta_required is None:
    os.environ.pop("MANNEQUIN_META_REQUIRED", None)
else:
    os.environ["MANNEQUIN_META_REQUIRED"] = old_meta_required

print("== 6. frames_index.csv / frame_ts_sync.json 산출 ==")
with tempfile.TemporaryDirectory() as tmp:
    session_dir = Path(tmp) / "20260617150000"
    session_dir.mkdir()
    frame_rows = [
        {"write_index": 0, "camera_id": "105", "source_frame_ts": 1000.000, "dup": False},
        {"write_index": 0, "camera_id": "106", "source_frame_ts": 1000.010, "dup": False},
        {"write_index": 0, "camera_id": "107", "source_frame_ts": 999.990, "dup": False},
        {"write_index": 1, "camera_id": "105", "source_frame_ts": 1000.033, "dup": False},
        {"write_index": 1, "camera_id": "106", "source_frame_ts": 1000.043, "dup": False},
        {"write_index": 1, "camera_id": "107", "source_frame_ts": 999.990, "dup": True},
    ]
    written = app.write_sensor_artifacts(
        session_dir,
        [{"ts": 1000.5, "category": "sensor_stream", "type_byte": "0xd0", "val1": 500.0}],
        record_start_ts=1000.0,
        record_end_ts=1002.0,
        record_fps=30.0,
        camera_paths={
            "105": str(session_dir / "105.mp4"),
            "106": str(session_dir / "106.mp4"),
            "107": str(session_dir / "107.mp4"),
        },
        frame_rows=frame_rows,
        experiment_meta=meta,
    )
    session_data = json.loads(Path(written["session_json"]).read_text(encoding="utf-8"))
    frame_sync = json.loads(Path(written["frame_sync_json"]).read_text(encoding="utf-8"))
    frames_index = Path(written["frames_index_csv"]).read_text(encoding="utf-8")
    check("frames_index.csv 생성", "write_index,camera_id,source_frame_ts,dup" in frames_index, written)
    check("frame_ts_sync.json 생성(type 확인)", frame_sync.get("type") == "recording_ab_frame_ts_sync", frame_sync)
    check("sync_report.json 미생성", not (session_dir / "sync_report.json").exists(), list(session_dir.iterdir()))
    check("session.json frame_sync_json 키", "frame_sync_json" in session_data, session_data)
    check("session.json experiment.date 파생", session_data["experiment"]["date"] == "2026-06-17", session_data["experiment"])
    check("session 산출물에 operator_id_raw 없음", "operator_id_raw" not in json.dumps(session_data), session_data.get("experiment"))
    off_106 = frame_sync["pairwise_offsets_relative_to_reference"]["106"]["offset_ms"]
    check("106 기준대비 offset_ms 보고", off_106 == 10.0, off_106)
    check("107 dup ratio 보고", frame_sync["cameras"]["107"]["duplicate_ratio"] == 0.5, frame_sync["cameras"]["107"])

print("== 7. experiment 블록 없는 기존 session.json 하위호환 ==")
old_session_paths = sorted((ROOT / "recordings").glob("*/session.json"))
old_without_experiment = None
for candidate in old_session_paths:
    data = json.loads(candidate.read_text(encoding="utf-8"))
    if "experiment" not in data:
        old_without_experiment = data
        break
if old_without_experiment is None:
    old_without_experiment = {"session_id": "legacy", "sensor_csv": "sensor.csv"}
check("experiment 없는 세션은 .get('experiment')로 안전하게 읽힘", old_without_experiment.get("experiment") is None, old_without_experiment)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
