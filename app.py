from __future__ import annotations

import csv
import hashlib
import html
import hmac
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generator


os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "stimeout;5000000|rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;500000|threads;1",
)
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

import cv2
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, StreamingResponse

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

try:
    import websockets
except ImportError:
    websockets = None


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
RECORDINGS_DIR = ROOT / "recordings"
RUNTIME_DIR = ROOT / "runtime"
WEB_DIR = ROOT / "web"
GO2RTC_BINARY = Path(r"C:\Users\USER\Documents\01_Git\abc_collector_v3\tools\go2rtc\go2rtc.exe")
GO2RTC_API_PORT = 11984
GO2RTC_RTSP_PORT = 18554
GO2RTC_WEBRTC_PORT = 18555
COUNTDOWN_SECONDS = 3.0
RECORD_SECONDS = 30.0
DEFAULT_FPS = 30.0
CAMERA_IDS = ("105", "106", "107")
MANNEQUIN_BAUD_RATE = 115200
MANNEQUIN_SERIAL_FALLBACK_PORT = "COM4"
MANNEQUIN_SENSOR_BUFFER_SIZE = 5000
VOLUME_MIN_ML = 400.0
VOLUME_MAX_ML = 600.0
VOLUME_DEAD_SPACE_ML = 150.0
PROTOCOL_TYPES = frozenset({"cpr_30_2", "advanced_airway", "respiratory_arrest"})
GLOVE_CONDITIONS = frozenset({"bare", "blue_latex"})
TORSO_CLOTHING = frozenset({"bare", "clothed"})
MASK_SIZES = frozenset({"infant", "child", "adult_s", "adult_m", "adult_l"})
ADJUNCT_USES = frozenset({"none", "opa", "npa"})
LIGHTING_VALUES = frozenset({"normal", "dim", "bright"})
OCCLUSION_VALUES = frozenset({"none", "partial", "heavy"})
SCENARIO_MIN = 1
SCENARIO_MAX = 23


@dataclass(frozen=True)
class CameraConfig:
    camera_id: str
    label: str
    source_url: str
    capture_url: str


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_bool(names: tuple[str, ...], default: bool) -> bool:
    for name in names:
        value = os.environ.get(name)
        if value is None:
            continue
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return default


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_operator_id(raw: str) -> str:
    return re.sub(r"[\s_-]+", "", raw.strip().lower())


def operator_id_hash(raw: str, salt: str) -> str:
    normalized = normalize_operator_id(raw)
    return hmac.new(salt.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_experiment_meta(payload: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, str]]:
    meta_required = env_bool(("MANNEQUIN_META_REQUIRED",), True)
    experiment = payload.get("experiment") if isinstance(payload, dict) else None
    if experiment is None:
        experiment = {}
    if not isinstance(experiment, dict):
        return None, {"experiment": "실험 조건은 JSON 객체여야 합니다"}

    errors: dict[str, str] = {}
    required_fields = ("protocol_type", "scenario_id", "glove_condition", "torso_clothing", "operator_id_raw")
    for field in required_fields:
        if field not in experiment or experiment.get(field) in (None, ""):
            if meta_required and field != "scenario_id":
                errors[field] = "필수 입력입니다"
            elif meta_required and field == "scenario_id" and field not in experiment:
                errors[field] = "필수 입력입니다(null은 허용)"

    protocol_type = clean_optional_string(experiment.get("protocol_type"))
    glove_condition = clean_optional_string(experiment.get("glove_condition"))
    torso_clothing = clean_optional_string(experiment.get("torso_clothing"))
    mask_size = clean_optional_string(experiment.get("mask_size"))
    bag_type = clean_optional_string(experiment.get("bag_type"))
    adjunct_use = clean_optional_string(experiment.get("adjunct_use"))
    lighting = clean_optional_string(experiment.get("lighting"))
    occlusion_severity = clean_optional_string(experiment.get("occlusion_severity"))
    manikin_type = clean_optional_string(experiment.get("manikin_type"))
    calib_version = clean_optional_string(experiment.get("calib_version")) or "none"
    notes = clean_optional_string(experiment.get("notes")) or ""

    enum_checks = (
        ("protocol_type", protocol_type, PROTOCOL_TYPES),
        ("glove_condition", glove_condition, GLOVE_CONDITIONS),
        ("torso_clothing", torso_clothing, TORSO_CLOTHING),
        ("mask_size", mask_size, MASK_SIZES),
        ("adjunct_use", adjunct_use, ADJUNCT_USES),
        ("lighting", lighting, LIGHTING_VALUES),
        ("occlusion_severity", occlusion_severity, OCCLUSION_VALUES),
    )
    for field, value, allowed in enum_checks:
        if value is not None and value not in allowed:
            errors[field] = f"허용값: {', '.join(sorted(allowed))}"

    scenario_id: int | None = None
    if "scenario_id" in experiment and experiment.get("scenario_id") not in (None, ""):
        try:
            scenario_id = int(experiment["scenario_id"])
        except (TypeError, ValueError):
            errors["scenario_id"] = "null 또는 1-23 정수여야 합니다"
        else:
            if not SCENARIO_MIN <= scenario_id <= SCENARIO_MAX:
                errors["scenario_id"] = "null 또는 1-23 정수여야 합니다"
    elif meta_required and "scenario_id" not in experiment:
        errors["scenario_id"] = "필수 입력입니다(null은 허용)"

    operator_raw = experiment.get("operator_id_raw")
    operator_text = operator_raw.strip() if isinstance(operator_raw, str) else ""
    operator_hash = None
    if operator_text:
        salt = os.environ.get("MANNEQUIN_OPERATOR_SALT")
        if not salt:
            errors["operator_id_raw"] = "MANNEQUIN_OPERATOR_SALT가 없어 operator 해시를 만들 수 없습니다"
        else:
            normalized = normalize_operator_id(operator_text)
            if not normalized:
                errors["operator_id_raw"] = "유효한 operator 식별자를 입력하세요"
            else:
                operator_hash = operator_id_hash(operator_text, salt)
    elif meta_required:
        errors["operator_id_raw"] = "필수 입력입니다"

    if errors:
        return None, errors

    return {
        "protocol_type": protocol_type,
        "scenario_id": scenario_id,
        "scenario_unspecified": scenario_id is None,
        "glove_condition": glove_condition,
        "torso_clothing": torso_clothing,
        "operator_id_hash": operator_hash,
        "mask_size": mask_size,
        "bag_type": bag_type,
        "adjunct_use": adjunct_use,
        "lighting": lighting,
        "occlusion_severity": occlusion_severity,
        "manikin_type": manikin_type,
        "calib_version": calib_version,
        "sync_method": "frame_ts",
        "notes": notes,
    }, {}


def redact_url(url: str) -> str:
    return re.sub(r"(rtsp[s]?://[^:]+:)([^@]+)(@)", r"\1***\3", url)


def url_with_last_octet(url: str, octet: str) -> str:
    return re.sub(r"(\d+\.\d+\.\d+\.)(\d+)", rf"\g<1>{octet}", url, count=1)


def yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def camera_configs() -> list[CameraConfig]:
    base_url = os.environ.get("RTSP_URL", "").strip()
    configs: list[CameraConfig] = []
    for camera_id in CAMERA_IDS:
        source_url = os.environ.get(f"RTSP_URL_{camera_id}", "").strip()
        if not source_url and base_url:
            source_url = base_url if camera_id == "105" else url_with_last_octet(base_url, camera_id)
        if source_url:
            configs.append(
                CameraConfig(
                    camera_id=camera_id,
                    label=f"카메라 {camera_id}",
                    source_url=source_url,
                    capture_url=f"rtsp://127.0.0.1:{GO2RTC_RTSP_PORT}/{camera_id}",
                )
            )
    return configs


class Go2RtcRelay:
    def __init__(self, configs: list[CameraConfig]):
        self.configs = configs
        self.yaml_path = RUNTIME_DIR / "go2rtc.yaml"
        self.log_path = RUNTIME_DIR / "go2rtc.log"
        self._proc: subprocess.Popen | None = None
        self._log_file = None
        self.ready = False
        self.error: str | None = None

    def start(self) -> bool:
        if not self.configs:
            self.error = "설정된 카메라가 없습니다"
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True
        if not GO2RTC_BINARY.exists():
            self.error = f"go2rtc.exe를 찾을 수 없습니다: {GO2RTC_BINARY}"
            print(f"[go2rtc] {self.error}")
            return False

        self._write_config()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.log_path, "a", encoding="utf-8", errors="replace")
        try:
            self._proc = subprocess.Popen(
                [str(GO2RTC_BINARY), "-config", str(self.yaml_path)],
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                cwd=str(GO2RTC_BINARY.parent),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            self.error = str(exc)
            self._close_log()
            print(f"[go2rtc] start failed: {exc}")
            return False

        print(f"[go2rtc] spawned pid={self._proc.pid}")
        self.ready = self._wait_ready(timeout=10.0)
        return self.ready

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2.0)
            except Exception as exc:
                print(f"[go2rtc] stop failed: {exc}")
        self._proc = None
        self.ready = False
        self._close_log()

    def status(self) -> dict:
        return {
            "alive": self._proc is not None and self._proc.poll() is None,
            "ready": self.ready,
            "api_port": GO2RTC_API_PORT,
            "rtsp_port": GO2RTC_RTSP_PORT,
            "webrtc_port": GO2RTC_WEBRTC_PORT,
            "error": self.error,
        }

    def _write_config(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        stream_lines = "\n".join(
            f"  {config.camera_id}: {yaml_quote(config.source_url)}"
            for config in self.configs
        )
        body = f"""api:
  listen: ":{GO2RTC_API_PORT}"
  origin: "*"

rtsp:
  listen: ":{GO2RTC_RTSP_PORT}"

webrtc:
  listen: ":{GO2RTC_WEBRTC_PORT}"
  candidates:
    - localhost
    - 127.0.0.1

log:
  level: info

streams:
{stream_lines}
"""
        self.yaml_path.write_text(body, encoding="utf-8")

    def _wait_ready(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{GO2RTC_API_PORT}/api/streams"
        while time.time() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                self.error = "go2rtc가 준비되기 전에 종료되었습니다"
                return False
            try:
                with urllib.request.urlopen(url, timeout=1.0) as resp:
                    if resp.status == 200:
                        self.error = None
                        print(f"[go2rtc] ready {url}")
                        return True
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)
        self.error = "go2rtc 준비 시간 초과"
        print(f"[go2rtc] {self.error}")
        return False

    def _close_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None


class CameraReader:
    def __init__(self, config: CameraConfig):
        self.config = config
        self.lock = threading.Lock()
        self.frame = None
        self.frame_ts: float | None = None
        self.fps = DEFAULT_FPS
        self.error: str | None = None
        self.opened = False
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._cap: cv2.VideoCapture | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"rtsp-reader-{self.config.camera_id}",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=6.0)
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def snapshot(self):
        with self.lock:
            if self.frame is None:
                return None, None
            return self.frame.copy(), self.frame_ts

    def status(self) -> dict:
        with self.lock:
            age = None if self.frame_ts is None else max(0.0, time.time() - self.frame_ts)
            shape = None if self.frame is None else list(self.frame.shape)
            return {
                "camera_id": self.config.camera_id,
                "label": self.config.label,
                "opened": self.opened,
                "has_frame": self.frame is not None,
                "frame_age_seconds": age,
                "fps": self.fps,
                "shape": shape,
                "error": self.error,
            }

    def _loop(self) -> None:
        while not self._stopped.is_set():
            cap = cv2.VideoCapture(self.config.capture_url, cv2.CAP_FFMPEG)
            self._cap = cap
            if not cap.isOpened():
                with self.lock:
                    self.opened = False
                    self.error = f"RTSP 열기 실패: {redact_url(self.config.capture_url)}"
                cap.release()
                time.sleep(1.0)
                continue

            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass

            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps and 1.0 <= fps <= 120.0:
                self.fps = float(fps)
            with self.lock:
                self.opened = True
                self.error = None

            while not self._stopped.is_set():
                ok, frame = cap.read()
                if ok and frame is not None:
                    with self.lock:
                        self.frame = frame
                        self.frame_ts = time.time()
                        self.opened = True
                        self.error = None
                else:
                    with self.lock:
                        self.opened = False
                        self.error = "RTSP 프레임 읽기 실패"
                    time.sleep(0.05)
                    break

            cap.release()
            self._cap = None


class MannequinSensorBuffer:
    def __init__(self, maxlen: int = MANNEQUIN_SENSOR_BUFFER_SIZE):
        self.lock = threading.Lock()
        self.events: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def add(self, event: dict[str, Any]) -> None:
        normalized = dict(event)
        ts = float(normalized.get("ts") or time.time())
        normalized["ts"] = ts
        normalized.setdefault("recv_at", datetime.fromtimestamp(ts).isoformat(timespec="microseconds"))
        with self.lock:
            self.events.append(normalized)

    def sensor_events_between(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
        with self.lock:
            return [
                dict(event)
                for event in self.events
                if (
                    event.get("category") == "sensor_stream"
                    and isinstance(event.get("val1"), (int, float))
                    and start_ts <= float(event.get("ts", 0.0)) <= end_ts
                )
            ]

    def events_between(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
        with self.lock:
            return [
                dict(event)
                for event in self.events
                if start_ts <= float(event.get("ts", 0.0)) <= end_ts
            ]

    def status(self) -> dict:
        with self.lock:
            events = list(self.events)
        sensor_events = [
            event
            for event in events
            if event.get("category") == "sensor_stream" and isinstance(event.get("val1"), (int, float))
        ]
        latest = dict(events[-1]) if events else None
        latest_sensor = dict(sensor_events[-1]) if sensor_events else None
        return {
            "packets": len(events),
            "sensor_packets": len(sensor_events),
            "latest": compact_sensor_event(latest),
            "latest_sensor": compact_sensor_event(latest_sensor),
        }


class MannequinSerialReader:
    PACKET_LENGTHS = {
        0xa6: 12,
        0xcf: 26,
        0xd0: 20,
        0xd1: 11,
        0xd7: 11,
        0xd8: 10,
        0xe0: 11,
        0xe7: 9,
        0xe8: 11,
        0xe9: 9,
        0xe2: 12,
    }
    LAERDAL_HEADERS = frozenset(PACKET_LENGTHS)
    USB_SERIAL_VIDS = frozenset({0x0403, 0x10C4, 0x067B, 0x1A86, 0x0483, 0x2341, 0x16C0})

    def __init__(self, sensor_buffer: MannequinSensorBuffer):
        self.sensor_buffer = sensor_buffer
        self.port = os.environ.get("MANNEQUIN_SERIAL_PORT") or os.environ.get("MANIKIN_SERIAL_PORT")
        self.baud = int(os.environ.get("MANNEQUIN_SERIAL_BAUD") or os.environ.get("MANIKIN_SERIAL_BAUD") or MANNEQUIN_BAUD_RATE)
        self.lock = threading.Lock()
        self.running = False
        self.connected = False
        self.last_error: str | None = None
        self._serial = None
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats: dict[str, Any] = {
            "packets_total": 0,
            "sensor_packets": 0,
            "by_type": {},
            "started_at": None,
            "reconnects": 0,
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped.clear()
        with self.lock:
            self.running = True
            self.stats["started_at"] = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mannequin-serial")
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._close_serial()
        with self.lock:
            self.running = False
            self.connected = False

    def status(self) -> dict:
        with self.lock:
            return {
                "enabled": True,
                "running": self.running,
                "connected": self.connected,
                "port": self.port,
                "baud": self.baud,
                "last_error": self.last_error,
                "stats": dict(self.stats),
                "buffer": self.sensor_buffer.status(),
            }

    def _loop(self) -> None:
        if serial is None:
            with self.lock:
                self.running = False
                self.connected = False
                self.last_error = "pyserial이 설치되어 있지 않습니다"
            return

        buffer = bytearray()
        while not self._stopped.is_set():
            if self._serial is None:
                if not self._open_serial():
                    time.sleep(2.0)
                    continue
                buffer.clear()

            try:
                chunk = self._serial.read(512)
            except Exception as exc:
                with self.lock:
                    self.connected = False
                    self.last_error = str(exc)
                self._close_serial()
                buffer.clear()
                time.sleep(2.0)
                continue

            if chunk:
                buffer.extend(chunk)
                packets, remainder = self._split_packets(buffer)
                buffer = bytearray(remainder)
                for packet in packets:
                    event = self._parse_packet(packet)
                    self._record_event(event)
            else:
                time.sleep(0.005)

    def _open_serial(self) -> bool:
        if serial is None:
            return False
        if not self.port:
            self.port = find_mannequin_port() or MANNEQUIN_SERIAL_FALLBACK_PORT
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=0.05)
        except Exception as exc:
            with self.lock:
                self.connected = False
                self.last_error = str(exc)
            if not os.environ.get("MANNEQUIN_SERIAL_PORT") and not os.environ.get("MANIKIN_SERIAL_PORT"):
                self.port = None
            return False
        with self.lock:
            self.connected = True
            self.last_error = None
            self.stats["reconnects"] += 1
        print(f"[mannequin] serial connected {self.port} @ {self.baud}")
        return True

    def _close_serial(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def _split_packets(self, buffer: bytearray) -> tuple[list[bytes], bytearray]:
        packets: list[bytes] = []
        i = 0
        while i <= len(buffer) - 3:
            if buffer[i] == 0x00 and buffer[i + 1] == 0x02:
                type_byte = buffer[i + 2]
                length = self.PACKET_LENGTHS.get(type_byte)
                if length is None:
                    i += 1
                    continue
                if i + length > len(buffer):
                    break
                packets.append(bytes(buffer[i:i + length]))
                i += length
            else:
                i += 1
        return packets, buffer[i:]

    def _parse_packet(self, packet: bytes) -> dict[str, Any]:
        ts = time.time()
        event: dict[str, Any] = {
            "ts": ts,
            "recv_at": datetime.fromtimestamp(ts).isoformat(timespec="microseconds"),
            "length": len(packet),
            "hex": packet.hex(),
            "type_byte": f"0x{packet[2]:02x}" if len(packet) >= 3 else "0x??",
            "category": "unknown",
        }
        if len(packet) < 3:
            return event

        type_byte = packet[2]
        if type_byte == 0xd0:
            event["category"] = "sensor_stream"
            if len(packet) >= 8:
                event["val1"] = int.from_bytes(packet[6:8], "little")
            if len(packet) >= 13:
                quality_raw = packet[12]
                event["vent_quality_raw"] = quality_raw
                event["vent_quality"] = "normal" if quality_raw == 0x07 else "too_fast" if quality_raw == 0x04 else "unknown"
            if len(packet) >= 15:
                event["vent_interval_ms"] = int.from_bytes(packet[13:15], "little")
            if len(packet) >= 17:
                event["vent_flow_rate_ml_s"] = int.from_bytes(packet[15:17], "little")
        elif type_byte == 0xa6:
            event["category"] = "heartbeat"
        elif type_byte in {0xcf, 0xd7, 0xe0, 0xe7, 0xe8}:
            event["category"] = "cpr"
        elif type_byte == 0xd1:
            event["category"] = "timer"
        elif type_byte == 0xd8:
            event["category"] = "event"
        elif type_byte == 0xe9:
            event["category"] = "status"
        return event

    def _record_event(self, event: dict[str, Any]) -> None:
        type_byte = event.get("type_byte", "?")
        with self.lock:
            self.stats["packets_total"] += 1
            self.stats["by_type"][type_byte] = self.stats["by_type"].get(type_byte, 0) + 1
            if event.get("category") == "sensor_stream":
                self.stats["sensor_packets"] += 1
        self.sensor_buffer.add(event)


class AbcSensorBridge:
    def __init__(self, sensor_buffer: MannequinSensorBuffer):
        self.sensor_buffer = sensor_buffer
        self.url = os.environ.get("ABC_SENSOR_BRIDGE_URL", "ws://127.0.0.1:8010/ws")
        self.lock = threading.Lock()
        self.running = False
        self.connected = False
        self.last_error: str | None = None
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats: dict[str, Any] = {
            "messages_total": 0,
            "serial_packets": 0,
            "sensor_packets": 0,
            "started_at": None,
            "reconnects": 0,
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped.clear()
        with self.lock:
            self.running = True
            self.stats["started_at"] = time.time()
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="abc-sensor-bridge")
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        with self.lock:
            self.running = False
            self.connected = False

    def status(self) -> dict:
        with self.lock:
            return {
                "enabled": True,
                "running": self.running,
                "connected": self.connected,
                "url": self.url,
                "last_error": self.last_error,
                "stats": dict(self.stats),
            }

    def _thread_main(self) -> None:
        if websockets is None:
            with self.lock:
                self.running = False
                self.connected = False
                self.last_error = "websockets가 설치되어 있지 않습니다"
            return
        try:
            import asyncio

            asyncio.run(self._run())
        except Exception as exc:
            with self.lock:
                self.running = False
                self.connected = False
                self.last_error = str(exc)

    async def _run(self) -> None:
        import asyncio

        while not self._stopped.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as websocket:
                    with self.lock:
                        self.connected = True
                        self.last_error = None
                        self.stats["reconnects"] += 1
                    print(f"[mannequin] abc sensor bridge connected {self.url}")
                    while not self._stopped.is_set():
                        try:
                            raw_message = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        self._handle_message(raw_message)
            except Exception as exc:
                with self.lock:
                    self.connected = False
                    self.last_error = str(exc)
                await asyncio.sleep(2.0)

    def _handle_message(self, raw_message: str | bytes) -> None:
        try:
            message = json.loads(raw_message)
        except Exception:
            return
        with self.lock:
            self.stats["messages_total"] += 1

        if message.get("type") == "batch" and isinstance(message.get("data"), list):
            payloads = message["data"]
        else:
            payloads = [message]

        for payload in payloads:
            if not isinstance(payload, dict) or payload.get("type") != "serial":
                continue
            data = payload.get("data")
            if isinstance(data, dict):
                self._handle_serial(data)

    def _handle_serial(self, data: dict[str, Any]) -> None:
        event = dict(data)
        event.setdefault("ts", time.time())
        event.setdefault("recv_at", datetime.fromtimestamp(float(event["ts"])).isoformat(timespec="microseconds"))
        event.setdefault("type_byte", event.get("type_byte", "0xd0"))
        event.setdefault("category", "sensor_stream" if "val1" in event else "serial")
        event["source"] = "abc_collector_v3_ws"
        with self.lock:
            self.stats["serial_packets"] += 1
            if event.get("category") == "sensor_stream" and isinstance(event.get("val1"), (int, float)):
                self.stats["sensor_packets"] += 1
        self.sensor_buffer.add(event)


def find_mannequin_port(timeout_s: float = 1.5) -> str | None:
    if serial is None or list_ports is None:
        return None

    candidates: list[str] = []
    for port in list_ports.comports():
        manufacturer = (port.manufacturer or "").lower()
        description = (port.description or "").lower()
        if "laerdal" in manufacturer or "resusci" in manufacturer or "laerdal" in description or "resusci" in description:
            return port.device
        if port.vid in MannequinSerialReader.USB_SERIAL_VIDS:
            candidates.append(port.device)

    for device in candidates:
        try:
            with serial.Serial(device, MANNEQUIN_BAUD_RATE, timeout=0.3) as probe:
                deadline = time.time() + timeout_s
                data = b""
                while time.time() < deadline:
                    chunk = probe.read(64)
                    if not chunk:
                        continue
                    data += chunk
                    if any(bytes((0x00, 0x02, header)) in data for header in MannequinSerialReader.LAERDAL_HEADERS):
                        return device
        except Exception:
            continue
    return None


def compact_sensor_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    keys = (
        "ts",
        "recv_at",
        "type_byte",
        "category",
        "val1",
        "vent_interval_ms",
        "vent_flow_rate_ml_s",
        "vent_quality",
    )
    compact = {key: event[key] for key in keys if key in event}
    if event.get("category") == "sensor_stream" and isinstance(event.get("val1"), (int, float)):
        t_insp = inspiratory_time_sec(event)
        output = ventilation_output(event)
        compact["inspiratory_time_sec"] = round(t_insp, 6) if t_insp is not None else None
        compact["ventilation_output_value"] = (
            round(output["value"], 3) if isinstance(output["value"], (int, float)) else None
        )
        compact["ventilation_output_unit"] = output["unit"]
        compact["ventilation_output_source"] = output["source"]
        compact["ventilation_output_rule"] = output["rule"]
    return compact


def volume_quality(volume_ml: float) -> tuple[str, bool]:
    min_ml = env_float("MANNEQUIN_VOLUME_MIN_ML", VOLUME_MIN_ML)
    max_ml = env_float("MANNEQUIN_VOLUME_MAX_ML", VOLUME_MAX_ML)
    if volume_ml < min_ml:
        return "TOO_LOW", False
    if volume_ml > max_ml:
        return "EXCESSIVE", False
    return "GOOD", True


def inspiratory_time_sec(event: dict[str, Any]) -> float | None:
    val1 = event.get("val1")
    flow_rate = event.get("vent_flow_rate_ml_s")
    if not isinstance(val1, (int, float)) or not isinstance(flow_rate, (int, float)) or flow_rate <= 0:
        return None
    return max(0.05, float(val1) / float(flow_rate))


def ventilation_output(event: dict[str, Any]) -> dict[str, Any]:
    val1 = event.get("val1")
    flow_rate = event.get("vent_flow_rate_ml_s")
    t_insp = inspiratory_time_sec(event)
    if t_insp is not None and t_insp >= 1.0 and isinstance(flow_rate, (int, float)):
        return {
            "value": float(flow_rate),
            "unit": "mL/s",
            "source": "vent_flow_rate_ml_s",
            "rule": "inspiratory_time_sec >= 1.0",
        }
    if isinstance(val1, (int, float)):
        return {
            "value": float(val1),
            "unit": "mL",
            "source": "val1",
            "rule": "inspiratory_time_sec < 1.0 or flow_rate_missing",
        }
    return {
        "value": None,
        "unit": None,
        "source": None,
        "rule": "no_ventilation_value",
    }


def packet_video_time(event: dict[str, Any], record_start_ts: float, duration: float) -> float:
    return max(0.0, min(duration, float(event["ts"]) - record_start_ts))


def frame_count_for_duration(duration: float, record_fps: float) -> int:
    if duration <= 0 or record_fps <= 0:
        return 0
    return max(1, int(round(duration * record_fps)))


def frame_index_for_video_time(video_time_sec: float, record_fps: float, duration: float) -> int:
    frame_count = frame_count_for_duration(duration, record_fps)
    if frame_count <= 0:
        return 0
    return max(0, min(frame_count - 1, int(round(video_time_sec * record_fps))))


def median_value(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def session_date(session_id: str, fallback_ts: float) -> str:
    try:
        return datetime.strptime(session_id, "%Y%m%d%H%M%S").date().isoformat()
    except ValueError:
        return datetime.fromtimestamp(fallback_ts).date().isoformat()


def camera_frame_summary(rows: list[dict[str, Any]], record_fps: float) -> dict[str, Any]:
    timestamps = [
        float(row["source_frame_ts"])
        for row in rows
        if isinstance(row.get("source_frame_ts"), (int, float))
    ]
    frame_count = len(rows)
    duplicate_count = sum(1 for row in rows if row.get("dup"))
    return {
        "fps": record_fps,
        "frame_count": frame_count,
        "duration_sec": round(frame_count / record_fps, 6) if record_fps > 0 else None,
        "first_source_frame_ts": round(timestamps[0], 6) if timestamps else None,
        "last_source_frame_ts": round(timestamps[-1], 6) if timestamps else None,
        "duplicate_frames": duplicate_count,
        "duplicate_ratio": round(duplicate_count / frame_count, 6) if frame_count else None,
        "flash_frame_index": None,
        "flash_offset_ms": None,
    }


def build_frame_ts_sync_report(
    frame_rows: list[dict[str, Any]],
    *,
    record_start_ts: float,
    record_end_ts: float,
    record_fps: float,
    camera_ids: list[str],
    reference_camera_id: str = "105",
) -> dict[str, Any]:
    by_camera = {
        camera_id: [row for row in frame_rows if row.get("camera_id") == camera_id]
        for camera_id in camera_ids
    }
    cameras = {
        camera_id: camera_frame_summary(rows, record_fps)
        for camera_id, rows in by_camera.items()
    }

    ref_rows = {
        int(row["write_index"]): row
        for row in by_camera.get(reference_camera_id, [])
        if isinstance(row.get("source_frame_ts"), (int, float)) and not row.get("dup")
    }
    pairwise: dict[str, Any] = {}
    max_abs_drift_ms: float | None = None
    for camera_id, rows in by_camera.items():
        if camera_id == reference_camera_id:
            continue
        pairs: list[tuple[int, float]] = []
        for row in rows:
            if row.get("dup") or not isinstance(row.get("source_frame_ts"), (int, float)):
                continue
            ref = ref_rows.get(int(row["write_index"]))
            if ref is None:
                continue
            delta = float(row["source_frame_ts"]) - float(ref["source_frame_ts"])
            pairs.append((int(row["write_index"]), delta))
        deltas = [delta for _, delta in pairs]
        median_offset = median_value(deltas)
        drift = (pairs[-1][1] - pairs[0][1]) if len(pairs) >= 2 else None
        if drift is not None:
            drift_ms_abs = abs(drift * 1000.0)
            max_abs_drift_ms = drift_ms_abs if max_abs_drift_ms is None else max(max_abs_drift_ms, drift_ms_abs)
        pairwise[camera_id] = {
            "reference_camera_id": reference_camera_id,
            "paired_samples": len(pairs),
            "offset_ms": round(median_offset * 1000.0, 3) if median_offset is not None else None,
            "offset_frames": round(median_offset * record_fps, 3) if median_offset is not None else None,
            "drift_ms": round(drift * 1000.0, 3) if drift is not None else None,
        }

    return {
        "type": "recording_ab_frame_ts_sync",
        "sync_method": "frame_ts",
        "reference_camera_id": reference_camera_id,
        "record_start_epoch_ts": round(record_start_ts, 6),
        "record_end_epoch_ts": round(record_end_ts, 6),
        "duration_sec": round(record_end_ts - record_start_ts, 6),
        "fps": record_fps,
        "cameras": cameras,
        "pairwise_offsets_relative_to_reference": pairwise,
        "max_abs_drift_ms": round(max_abs_drift_ms, 3) if max_abs_drift_ms is not None else None,
        "note": "source_frame_ts is RTSP arrival time on this PC, not camera exposure time.",
    }


def write_frame_ts_artifacts(
    session_dir: Path,
    frame_rows: list[dict[str, Any]],
    *,
    record_start_ts: float,
    record_end_ts: float,
    record_fps: float,
    camera_ids: list[str],
) -> tuple[Path, Path, dict[str, Any]]:
    frames_index_csv = session_dir / "frames_index.csv"
    frame_sync_json = session_dir / "frame_ts_sync.json"
    with frames_index_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["write_index", "camera_id", "source_frame_ts", "dup"])
        writer.writeheader()
        for row in frame_rows:
            writer.writerow(
                {
                    "write_index": row.get("write_index", ""),
                    "camera_id": row.get("camera_id", ""),
                    "source_frame_ts": (
                        round(float(row["source_frame_ts"]), 6)
                        if isinstance(row.get("source_frame_ts"), (int, float))
                        else ""
                    ),
                    "dup": bool(row.get("dup")),
                }
            )
    report = build_frame_ts_sync_report(
        frame_rows,
        record_start_ts=record_start_ts,
        record_end_ts=record_end_ts,
        record_fps=record_fps,
        camera_ids=camera_ids,
    )
    frame_sync_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return frames_index_csv, frame_sync_json, report


def build_sensor_labels(
    sensor_events: list[dict[str, Any]],
    *,
    record_start_ts: float,
    record_end_ts: float,
    record_fps: float,
) -> dict[str, Any]:
    duration = max(0.0, record_end_ts - record_start_ts)
    labels = []
    volumes: list[float] = []
    passed_count = 0

    for index, event in enumerate(sensor_events, start=1):
        volume_ml = float(event["val1"])
        event_time_sec = max(0.0, min(duration, float(event["ts"]) - record_start_ts))
        t_insp = inspiratory_time_sec(event)
        if t_insp is None:
            start_sec = event_time_sec
            duration_sec = 0.0
            duration_source = "packet_time"
        else:
            start_sec = max(0.0, event_time_sec - t_insp)
            duration_sec = event_time_sec - start_sec
            duration_source = "val1_div_flow_rate"
        quality, passed = volume_quality(volume_ml)
        if passed:
            passed_count += 1
        volumes.append(volume_ml)
        output = ventilation_output(event)

        labels.append(
            {
                "event_index": index,
                "label": "bvm_squeeze",
                "source": "mannequin_sensor_gt",
                "video_time_sec": round(event_time_sec, 6),
                "start_sec": round(start_sec, 6),
                "end_sec": round(event_time_sec, 6),
                "duration_sec": round(duration_sec, 6),
                "duration_source": duration_source,
                "frame_index": frame_index_for_video_time(event_time_sec, record_fps, duration),
                "peak_volume_ml": round(volume_ml, 1),
                "ventilation_output_value": round(output["value"], 3) if isinstance(output["value"], (int, float)) else None,
                "ventilation_output_unit": output["unit"],
                "ventilation_output_source": output["source"],
                "ventilation_output_rule": output["rule"],
                "volume_quality": quality,
                "passed": passed,
                "vent_interval_sec": (
                    round(float(event["vent_interval_ms"]) / 1000.0, 6)
                    if isinstance(event.get("vent_interval_ms"), (int, float))
                    else None
                ),
                "inspiratory_time_sec": round(t_insp, 6) if t_insp is not None else None,
                "vent_quality": event.get("vent_quality"),
                "sensor_epoch_ts": round(float(event["ts"]), 6),
            }
        )

    return {
        "version": "mannequin_gt_labels_v1",
        "source": "mannequin_sensor_gt",
        "timeline": {
            "reference": "video_time_sec",
            "record_start_epoch_ts": round(record_start_ts, 6),
            "record_end_epoch_ts": round(record_end_ts, 6),
            "duration_sec": round(duration, 6),
            "fps": record_fps,
            "frame_count": frame_count_for_duration(duration, record_fps),
            "alignment": "sensor_epoch_ts - record_start_epoch_ts",
            "max_frame_quantization_error_sec": round(0.5 / record_fps, 6) if record_fps > 0 else None,
        },
        "thresholds": {
            "volume_min_ml": env_float("MANNEQUIN_VOLUME_MIN_ML", VOLUME_MIN_ML),
            "volume_max_ml": env_float("MANNEQUIN_VOLUME_MAX_ML", VOLUME_MAX_ML),
            "volume_dead_space_ml": env_float("MANNEQUIN_VOLUME_DEAD_SPACE_ML", VOLUME_DEAD_SPACE_ML),
        },
        "summary": {
            "total_squeezes": len(labels),
            "effective_squeezes": passed_count,
            "avg_volume_ml": round(sum(volumes) / len(volumes), 1) if volumes else None,
            "cycle_rate_per_min": round(len(labels) / (duration / 60.0), 2) if duration > 0 else 0.0,
        },
        "events": labels,
    }


def write_sensor_artifacts(
    session_dir: Path,
    packet_events: list[dict[str, Any]],
    *,
    record_start_ts: float,
    record_end_ts: float,
    record_fps: float,
    camera_paths: dict[str, str],
    frame_rows: list[dict[str, Any]] | None = None,
    experiment_meta: dict[str, Any] | None = None,
) -> dict[str, str]:
    duration = max(0.0, record_end_ts - record_start_ts)
    sensor_events = [
        event
        for event in packet_events
        if event.get("category") == "sensor_stream" and isinstance(event.get("val1"), (int, float))
    ]
    sensor_csv = session_dir / "sensor.csv"
    packets_csv = session_dir / "mannequin_packets.csv"
    packets_jsonl = session_dir / "mannequin_packets.jsonl"
    labels_json = session_dir / "labels.json"
    session_json = session_dir / "session.json"
    frames_index_csv, frame_sync_json, frame_sync = write_frame_ts_artifacts(
        session_dir,
        frame_rows or [],
        record_start_ts=record_start_ts,
        record_end_ts=record_end_ts,
        record_fps=record_fps,
        camera_ids=list(camera_paths.keys()),
    )

    csv_fields = [
        "video_time_sec",
        "epoch_ts",
        "recv_at",
        "val1_ml",
        "vent_interval_ms",
        "vent_flow_rate_ml_s",
        "vent_quality",
        "inspiratory_time_sec",
        "ventilation_output_value",
        "ventilation_output_unit",
        "ventilation_output_source",
        "type_byte",
        "hex",
    ]
    with sensor_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=csv_fields)
        writer.writeheader()
        for event in sensor_events:
            output = ventilation_output(event)
            t_insp = inspiratory_time_sec(event)
            video_time_sec = packet_video_time(event, record_start_ts, duration)
            writer.writerow(
                {
                    "video_time_sec": round(video_time_sec, 6),
                    "epoch_ts": round(float(event["ts"]), 6),
                    "recv_at": event.get("recv_at", ""),
                    "val1_ml": event.get("val1", ""),
                    "vent_interval_ms": event.get("vent_interval_ms", ""),
                    "vent_flow_rate_ml_s": event.get("vent_flow_rate_ml_s", ""),
                    "vent_quality": event.get("vent_quality", ""),
                    "inspiratory_time_sec": round(t_insp, 6) if t_insp is not None else "",
                    "ventilation_output_value": output["value"] if output["value"] is not None else "",
                    "ventilation_output_unit": output["unit"] or "",
                    "ventilation_output_source": output["source"] or "",
                    "type_byte": event.get("type_byte", ""),
                    "hex": event.get("hex", ""),
                }
            )

    packet_fields = [
        "video_time_sec",
        "frame_index",
        "epoch_ts",
        "recv_at",
        "source",
        "type_byte",
        "category",
        "label",
        "val1",
        "vent_interval_ms",
        "vent_flow_rate_ml_s",
        "vent_quality",
        "length",
        "hex",
        "payload_json",
    ]
    with packets_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=packet_fields)
        writer.writeheader()
        for event in packet_events:
            video_time_sec = packet_video_time(event, record_start_ts, duration)
            writer.writerow(
                {
                    "video_time_sec": round(video_time_sec, 6),
                    "frame_index": frame_index_for_video_time(video_time_sec, record_fps, duration),
                    "epoch_ts": round(float(event["ts"]), 6),
                    "recv_at": event.get("recv_at", ""),
                    "source": event.get("source", ""),
                    "type_byte": event.get("type_byte", ""),
                    "category": event.get("category", ""),
                    "label": event.get("label", ""),
                    "val1": event.get("val1", ""),
                    "vent_interval_ms": event.get("vent_interval_ms", ""),
                    "vent_flow_rate_ml_s": event.get("vent_flow_rate_ml_s", ""),
                    "vent_quality": event.get("vent_quality", ""),
                    "length": event.get("length", ""),
                    "hex": event.get("hex", ""),
                    "payload_json": json.dumps(event, ensure_ascii=False, sort_keys=True),
                }
            )

    with packets_jsonl.open("w", encoding="utf-8") as file:
        for event in packet_events:
            video_time_sec = packet_video_time(event, record_start_ts, duration)
            packet = dict(event)
            packet["video_time_sec"] = round(video_time_sec, 6)
            packet["frame_index"] = frame_index_for_video_time(video_time_sec, record_fps, duration)
            file.write(json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n")

    labels = build_sensor_labels(
        sensor_events,
        record_start_ts=record_start_ts,
        record_end_ts=record_end_ts,
        record_fps=record_fps,
    )
    labels_json.write_text(json.dumps(labels, indent=2), encoding="utf-8")

    experiment = None
    if experiment_meta is not None:
        experiment = dict(experiment_meta)
        experiment["date"] = session_date(session_dir.name, record_start_ts)
        experiment["sync_method"] = "frame_ts"

    session_meta = {
        "session_id": session_dir.name,
        "record_start_epoch_ts": round(record_start_ts, 6),
        "record_end_epoch_ts": round(record_end_ts, 6),
        "duration_sec": round(record_end_ts - record_start_ts, 6),
        "fps": record_fps,
        "frame_count": frame_count_for_duration(record_end_ts - record_start_ts, record_fps),
        "camera_paths": camera_paths,
        "sensor_csv": str(sensor_csv),
        "mannequin_packets_csv": str(packets_csv),
        "mannequin_packets_jsonl": str(packets_jsonl),
        "labels_json": str(labels_json),
        "frames_index_csv": str(frames_index_csv),
        "frame_sync_json": str(frame_sync_json),
        "label_source": "mannequin_sensor_gt",
        "packet_count": len(packet_events),
        "sensor_packet_count": len(sensor_events),
        "sync_method": "frame_ts",
        "frame_sync": frame_sync,
        "timestamp_alignment": {
            "packet_video_time_sec": "packet_epoch_ts - record_start_epoch_ts",
            "video_time_sec": "frame_index / fps",
            "frame_ts_sync": "source_frame_ts is camera frame arrival time on this PC",
            "max_frame_quantization_error_sec": round(0.5 / record_fps, 6) if record_fps > 0 else None,
            "clock_source": "same Windows system time.time() epoch; abc ws packets keep original 8010 packet ts",
        },
    }
    if experiment is not None:
        session_meta["experiment"] = experiment
    session_json.write_text(json.dumps(session_meta, indent=2), encoding="utf-8")
    return {
        "sensor_csv": str(sensor_csv),
        "mannequin_packets_csv": str(packets_csv),
        "mannequin_packets_jsonl": str(packets_jsonl),
        "labels_json": str(labels_json),
        "frames_index_csv": str(frames_index_csv),
        "frame_sync_json": str(frame_sync_json),
        "session_json": str(session_json),
    }


class RecordingController:
    def __init__(self, cameras: dict[str, CameraReader], sensor_buffer: MannequinSensorBuffer | None = None):
        self.cameras = cameras
        self.sensor_buffer = sensor_buffer
        self.lock = threading.Lock()
        self.state = "idle"
        self.countdown_until: float | None = None
        self.recording_until: float | None = None
        self.output_paths: dict[str, str] = {}
        self.error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self, experiment_meta: dict[str, Any] | None = None) -> dict:
        with self.lock:
            if self.state in {"countdown", "recording"}:
                return self.status_locked()
            self.state = "countdown"
            self.countdown_until = time.time() + COUNTDOWN_SECONDS
            self.recording_until = None
            self.output_paths = {}
            self.error = None
            self._thread = threading.Thread(
                target=self._record_once,
                args=(experiment_meta,),
                daemon=True,
                name="recorder",
            )
            self._thread.start()
            return self.status_locked()

    def status(self) -> dict:
        with self.lock:
            return self.status_locked()

    def status_locked(self) -> dict:
        now = time.time()
        remaining = 0.0
        if self.state == "countdown" and self.countdown_until is not None:
            remaining = max(0.0, self.countdown_until - now)
        elif self.state == "recording" and self.recording_until is not None:
            remaining = max(0.0, self.recording_until - now)
        return {
            "state": self.state,
            "remaining_seconds": remaining,
            "output_paths": self.output_paths,
            "error": self.error,
        }

    def _record_once(self, experiment_meta: dict[str, Any] | None = None) -> None:
        deadline = time.time() + COUNTDOWN_SECONDS
        while time.time() < deadline:
            time.sleep(min(0.05, deadline - time.time()))

        initial_snapshots = {camera_id: cam.snapshot() for camera_id, cam in self.cameras.items()}
        missing = [camera_id for camera_id, (frame, _) in initial_snapshots.items() if frame is None]
        if missing:
            self._set_error(f"녹화 시작 시점에 카메라 프레임이 없습니다: {', '.join(missing)}")
            return

        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        session_dir = RECORDINGS_DIR / stamp
        session_dir.mkdir(parents=True, exist_ok=True)

        fps_values = [cam.fps for cam in self.cameras.values() if 1.0 <= cam.fps <= 120.0]
        record_fps = fps_values[0] if fps_values else DEFAULT_FPS
        writers: dict[str, cv2.VideoWriter] = {}
        sizes: dict[str, tuple[int, int]] = {}
        output_paths: dict[str, str] = {}
        last_frames = {camera_id: frame.copy() for camera_id, (frame, _) in initial_snapshots.items() if frame is not None}
        last_frame_ts = {camera_id: frame_ts for camera_id, (_, frame_ts) in initial_snapshots.items()}
        last_written_ts: dict[str, float | None] = {}
        frame_rows: list[dict[str, Any]] = []

        try:
            for camera_id, (frame, _) in initial_snapshots.items():
                if frame is None:
                    continue
                height, width = frame.shape[:2]
                out_path = session_dir / f"{camera_id}.mp4"
                writer = cv2.VideoWriter(
                    str(out_path),
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    record_fps,
                    (width, height),
                )
                if not writer.isOpened():
                    self._set_error(f"영상 저장기를 열 수 없습니다: {out_path}")
                    return
                writers[camera_id] = writer
                sizes[camera_id] = (width, height)
                output_paths[camera_id] = str(out_path)

            frame_count = max(1, int(round(RECORD_SECONDS * record_fps)))
            record_duration = frame_count / record_fps
            record_start_ts = time.time()
            end_time = record_start_ts + record_duration
            with self.lock:
                self.state = "recording"
                self.countdown_until = None
                self.recording_until = end_time
                self.output_paths = output_paths
                self.error = None

            frame_interval = 1.0 / record_fps
            next_write = record_start_ts
            for write_index in range(frame_count):
                for camera_id, writer in writers.items():
                    frame, frame_ts = self.cameras[camera_id].snapshot()
                    source_frame_ts = frame_ts
                    if frame is None:
                        frame = last_frames[camera_id]
                        source_frame_ts = last_frame_ts.get(camera_id)
                        dup = True
                    else:
                        last_frames[camera_id] = frame
                        last_frame_ts[camera_id] = frame_ts
                        dup = (
                            frame_ts is None
                            or (
                                camera_id in last_written_ts
                                and last_written_ts[camera_id] == frame_ts
                            )
                        )
                    last_written_ts[camera_id] = source_frame_ts
                    frame_rows.append(
                        {
                            "write_index": write_index,
                            "camera_id": camera_id,
                            "source_frame_ts": source_frame_ts,
                            "dup": dup,
                        }
                    )
                    width, height = sizes[camera_id]
                    if frame.shape[1] != width or frame.shape[0] != height:
                        frame = cv2.resize(frame, (width, height))
                    writer.write(frame)
                next_write += frame_interval
                time.sleep(max(0.001, next_write - time.time()))
        except Exception as exc:
            self._set_error(str(exc))
            return
        finally:
            for writer in writers.values():
                writer.release()

        try:
            packet_events = (
                self.sensor_buffer.events_between(record_start_ts, end_time)
                if self.sensor_buffer is not None
                else []
            )
            output_paths.update(
                write_sensor_artifacts(
                    session_dir,
                    packet_events,
                    record_start_ts=record_start_ts,
                    record_end_ts=end_time,
                    record_fps=record_fps,
                    camera_paths=output_paths,
                    frame_rows=frame_rows,
                    experiment_meta=experiment_meta,
                )
            )
        except Exception as exc:
            self._set_error(f"영상은 저장했지만 센서 라벨 내보내기에 실패했습니다: {exc}")
            return

        with self.lock:
            if self.state != "error":
                self.state = "done"
                self.recording_until = None
                self.output_paths = output_paths
                self.error = None

    def _set_error(self, message: str) -> None:
        with self.lock:
            self.state = "error"
            self.countdown_until = None
            self.recording_until = None
            self.error = message


load_env(ENV_PATH)
CONFIGS = camera_configs()
GO2RTC = Go2RtcRelay(CONFIGS)
CAMERAS = {config.camera_id: CameraReader(config) for config in CONFIGS}
SENSOR_BUFFER = MannequinSensorBuffer()
SENSOR_READER = (
    MannequinSerialReader(SENSOR_BUFFER)
    if env_bool(("MANNEQUIN_SERIAL_ENABLED", "MANIKIN_SERIAL_ENABLED"), True)
    else None
)
ABC_SENSOR_BRIDGE = (
    AbcSensorBridge(SENSOR_BUFFER)
    if env_bool(("ABC_SENSOR_BRIDGE_ENABLED",), True)
    else None
)
recorder = RecordingController(CAMERAS, SENSOR_BUFFER) if CAMERAS else None
app = FastAPI(title="105 106 107 WebRTC Recorder")


@app.on_event("startup")
def startup() -> None:
    if SENSOR_READER is not None:
        SENSOR_READER.start()
    if ABC_SENSOR_BRIDGE is not None:
        ABC_SENSOR_BRIDGE.start()
    if not CAMERAS:
        print("[camera] RTSP_URL_105, RTSP_URL_106, RTSP_URL_107 are missing.")
        return
    GO2RTC.start()
    for camera in CAMERAS.values():
        print(f"[camera/{camera.config.camera_id}] starting {redact_url(camera.config.capture_url)}")
        camera.start()


@app.on_event("shutdown")
def shutdown() -> None:
    for camera in CAMERAS.values():
        camera.stop()
    if SENSOR_READER is not None:
        SENSOR_READER.stop()
    if ABC_SENSOR_BRIDGE is not None:
        ABC_SENSOR_BRIDGE.stop()
    GO2RTC.stop()


@app.get("/")
def index() -> Response:
    return _serve_web("index.html", "text/html; charset=utf-8")


@app.get("/styles.css")
def styles() -> Response:
    return _serve_web("styles.css", "text/css; charset=utf-8")


@app.get("/app.js")
def app_js() -> Response:
    return _serve_web("app.js", "application/javascript; charset=utf-8")


def _serve_web(name: str, media_type: str) -> Response:
    path = WEB_DIR / name
    if not path.exists():
        return Response(f"{name} not found", status_code=404, media_type="text/plain")
    return FileResponse(path, media_type=media_type)


@app.get("/api/config")
def config() -> dict:
    return {
        "go2rtc_api_port": GO2RTC_API_PORT,
        "record_seconds": RECORD_SECONDS,
        "countdown_seconds": COUNTDOWN_SECONDS,
        "cameras": [{"id": c.camera_id, "label": c.label} for c in CONFIGS],
    }


@app.get("/stream.mjpeg")
def stream_first_mjpeg() -> StreamingResponse:
    return stream_mjpeg(CAMERA_IDS[0])


@app.get("/stream/{camera_id}.mjpeg")
def stream_mjpeg(camera_id: str) -> StreamingResponse:
    camera = CAMERAS.get(camera_id)
    if camera is None:
        return empty_mjpeg()
    return StreamingResponse(
        mjpeg_frames(camera),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


def empty_mjpeg() -> StreamingResponse:
    return StreamingResponse(iter(()), media_type="multipart/x-mixed-replace; boundary=frame")


def mjpeg_frames(camera: CameraReader) -> Generator[bytes, None, None]:
    while True:
        frame, _ = camera.snapshot()
        if frame is None:
            time.sleep(0.1)
            continue
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            time.sleep(0.05)
            continue
        jpg = encoded.tobytes()
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        time.sleep(1.0 / 30.0)


def sensor_api_status() -> dict:
    direct = (
        SENSOR_READER.status()
        if SENSOR_READER is not None
        else {
            "enabled": False,
            "running": False,
            "connected": False,
            "port": None,
            "baud": None,
            "last_error": None,
            "stats": {},
        }
    )
    bridge = (
        ABC_SENSOR_BRIDGE.status()
        if ABC_SENSOR_BRIDGE is not None
        else {
            "enabled": False,
            "running": False,
            "connected": False,
            "url": None,
            "last_error": None,
            "stats": {},
        }
    )
    connected = bool(direct["connected"] or bridge["connected"])
    buffer_status = SENSOR_BUFFER.status()
    max_age_sec = env_float("MANNEQUIN_GT_READY_MAX_AGE_SEC", 10.0)
    latest_sensor = buffer_status.get("latest_sensor")
    latest_age_sec = (
        max(0.0, time.time() - float(latest_sensor["ts"]))
        if latest_sensor and isinstance(latest_sensor.get("ts"), (int, float))
        else None
    )
    gt_ready = bool(connected and latest_age_sec is not None and latest_age_sec <= max_age_sec)
    return {
        "enabled": bool(direct["enabled"] or bridge["enabled"]),
        "running": bool(direct["running"] or bridge["running"]),
        "connected": connected,
        "gt_ready": gt_ready,
        "latest_sensor_age_sec": latest_age_sec,
        "gt_ready_max_age_sec": max_age_sec,
        "port": direct["port"],
        "baud": direct["baud"],
        "bridge_url": bridge["url"],
        "last_error": None if connected else direct["last_error"] or bridge["last_error"],
        "sources": {
            "direct_serial": direct,
            "abc_ws_bridge": bridge,
        },
        "buffer": buffer_status,
    }


def recording_preflight_status(cam_statuses: list[dict[str, Any]], sensor_status: dict[str, Any]) -> dict[str, Any]:
    total_cameras = len(cam_statuses)
    ready_cameras = sum(1 for cam in cam_statuses if cam.get("has_frame"))
    cameras_ready = bool(total_cameras) and ready_cameras == total_cameras
    blockers: list[str] = []

    if not cameras_ready:
        missing = [str(cam.get("camera_id")) for cam in cam_statuses if not cam.get("has_frame")]
        detail = ", ".join(missing) if missing else "설정된 카메라 없음"
        blockers.append(f"카메라 프레임 준비 안 됨: {detail}")

    sensor_enabled = bool(sensor_status.get("enabled"))
    mannequin_connected = (not sensor_enabled) or bool(sensor_status.get("connected"))
    mannequin_gt_ready = (not sensor_enabled) or bool(sensor_status.get("gt_ready"))
    if sensor_enabled and not mannequin_connected:
        blockers.append("마네킹 기준값 센서가 연결되지 않았습니다")
    elif sensor_enabled and not mannequin_gt_ready:
        max_age = float(sensor_status.get("gt_ready_max_age_sec") or 0.0)
        blockers.append(f"최근 {max_age:.1f}초 안에 마네킹 기준값(val1) 패킷이 없습니다")

    return {
        "ready_for_recording": cameras_ready and mannequin_connected and mannequin_gt_ready,
        "blockers": blockers,
        "checks": {
            "cameras_ready": cameras_ready,
            "ready_cameras": ready_cameras,
            "total_cameras": total_cameras,
            "sensor_enabled": sensor_enabled,
            "mannequin_connected": mannequin_connected,
            "mannequin_gt_ready": mannequin_gt_ready,
            "latest_sensor_age_sec": sensor_status.get("latest_sensor_age_sec"),
            "gt_ready_max_age_sec": sensor_status.get("gt_ready_max_age_sec"),
            "packet_count": (sensor_status.get("buffer") or {}).get("packets"),
            "sensor_packet_count": (sensor_status.get("buffer") or {}).get("sensor_packets"),
        },
    }


def normalize_external_sensor_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ts = payload.get("ts") or payload.get("epoch_ts") or time.time()
    val1 = payload.get("val1", payload.get("volume_ml"))
    event: dict[str, Any] = {
        "ts": float(ts),
        "recv_at": payload.get("recv_at") or datetime.fromtimestamp(float(ts)).isoformat(timespec="microseconds"),
        "type_byte": payload.get("type_byte", "external"),
        "category": "sensor_stream",
        "source": "external_api",
    }
    if isinstance(val1, (int, float)):
        event["val1"] = float(val1)
    for key in ("vent_interval_ms", "vent_flow_rate_ml_s", "vent_quality"):
        if key in payload:
            event[key] = payload[key]
    return event


@app.post("/api/record/start")
def start_recording(payload: dict[str, Any] | None = None) -> dict:
    experiment_meta, meta_errors = validate_experiment_meta(payload)
    if meta_errors:
        return {
            "state": "error",
            "error": "실험 메타데이터 오류: 입력값을 확인하세요",
            "meta_errors": meta_errors,
        }
    if recorder is None:
        return {"state": "error", "error": "설정된 카메라가 없습니다"}
    sensor_status = sensor_api_status()
    preflight = recording_preflight_status(
        [camera.status() for camera in CAMERAS.values()],
        sensor_status,
    )
    if sensor_status["enabled"] and not sensor_status["connected"]:
        return {"state": "error", "error": "마네킹 기준값 센서가 연결되지 않았습니다", "preflight": preflight}
    if sensor_status["enabled"] and not sensor_status["gt_ready"]:
        max_age = sensor_status["gt_ready_max_age_sec"]
        return {
            "state": "error",
            "error": f"최근 {max_age:.1f}초 안에 마네킹 기준값(val1) 패킷이 없습니다",
            "preflight": preflight,
        }
    return recorder.start(experiment_meta)


@app.get("/api/sensor/status")
def sensor_status() -> dict:
    return sensor_api_status()


@app.post("/api/sensor/event")
def ingest_sensor_event(payload: dict[str, Any]) -> dict:
    event = normalize_external_sensor_payload(payload)
    if "val1" not in event:
        return {"ok": False, "error": "payload에는 val1 또는 volume_ml이 필요합니다"}
    SENSOR_BUFFER.add(event)
    return {"ok": True, "event": compact_sensor_event(event)}


@app.get("/api/status")
def status() -> dict:
    cam_statuses = [camera.status() for camera in CAMERAS.values()]
    sensor_status = sensor_api_status()
    rec_status = (
        recorder.status()
        if recorder is not None
        else {"state": "error", "remaining_seconds": 0, "output_paths": {}, "error": "설정된 카메라가 없습니다"}
    )
    return {
        "ready": bool(cam_statuses) and all(cam["has_frame"] for cam in cam_statuses),
        "go2rtc": GO2RTC.status(),
        "sensor": sensor_status,
        "preflight": recording_preflight_status(cam_statuses, sensor_status),
        "rtsp_urls": {config.camera_id: html.escape(redact_url(config.source_url)) for config in CONFIGS},
        "cameras": cam_statuses,
        "recording": rec_status,
    }


@app.get("/health")
def health() -> Response:
    return Response("ok", media_type="text/plain")
