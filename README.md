# 105 / 106 / 107 WebRTC Camera Recorder

105, 106, 107번 RTSP 카메라를 `go2rtc`로 WebRTC 변환해서 브라우저에서 라이브로 보고, 버튼 클릭 후 3초 뒤 30초 동안 자동 녹화하는 작은 로컬 앱입니다.

## 실행

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다.

앱 시작 시 `abc_collector_v3/tools/go2rtc/go2rtc.exe`를 자동 실행합니다. WebRTC 변환은 앱 전용 포트 `11984`, 로컬 RTSP 릴레이는 `18554`를 사용합니다.

## 설정

`.env` 파일에 실제 RTSP URL을 둡니다.

```env
RTSP_URL_105=rtsp://user:password@192.168.0.105:554/stream1
RTSP_URL_106=rtsp://user:password@192.168.0.106:554/stream1
RTSP_URL_107=rtsp://user:password@192.168.0.107:554/stream1
```

## 저장

녹화 파일은 실제 녹화 시작 시각 기준 `recordings/yyyyMMddHHmmss/` 폴더에 카메라별 MP4로 저장됩니다.

```text
recordings/
  20260616123456/
    105.mp4
    106.mp4
    107.mp4
```
