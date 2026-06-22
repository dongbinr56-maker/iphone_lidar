# Agent Instructions

## Package Manager
- No package manager manifest is configured; use the active Python environment.
- Frontend is plain static HTML/CSS/JS in `web/`; do not add Node/build tooling unless asked.

## Commands
| Task | Command |
|---|---|
| Run app | `python -m uvicorn app:app --host 127.0.0.1 --port 8000` |
| Sensor/recording unit check | `python Claude/tests/test_sensor_pipeline.py` |
| Local health check | `Invoke-RestMethod http://127.0.0.1:8000/health` |
| Local status check | `Invoke-RestMethod http://127.0.0.1:8000/api/status` |

## Commit Attribution
AI commits MUST include:
```text
Co-Authored-By: (the agent model's name and attribution byline)
```

## Project Shape
- `app.py` owns FastAPI routes, go2rtc lifecycle, camera readers, mannequin sensor ingestion, recording, labels, and session exports.
- `web/index.html`, `web/styles.css`, and `web/app.js` are the current frontend surface.
- `CLAUDE.md` is a Windows hardlink to this file for Claude-compatible tools.
- `Claude/work-log.md` is the older implementation log; `RESEARCH_NOTES.md` is the ongoing canonical research note.
- `.env` holds local RTSP/sensor configuration. Do not expose secrets; keep RTSP passwords redacted.

## Data And Runtime Files
- Treat `recordings/` as research data. Do not delete, rename, regenerate, or compress it unless explicitly asked.
- Treat `runtime/`, `_uvicorn.*.log`, `.server.pid`, `__pycache__/`, and `*.pyc` as runtime artifacts.
- Existing `.gitignore` excludes runtime/data artifacts; keep code/docs changes separate from generated files.

## Research Notes
- After every project-affecting task, append an entry to `RESEARCH_NOTES.md`.
- Each entry should include: date/time, user ask, assumptions, files changed, verification, generated data/artifacts, and open questions.
- If changing recording, sensor parsing, labels, metadata, or frame timing, record the validation result from `python Claude/tests/test_sensor_pipeline.py`.
- Write notes in natural Korean, like a researcher leaving context for the next reader.
- Avoid generic AI/report phrasing; keep the tone concrete, plain, and easy to follow.
- Add a diagram or generated image only when it makes the note easier to understand than text alone.

## Work Standard
- Bring maximum relevant effort to each task: use available tools, skills, research, subagents, and verification when they improve the result.
- Stay goal-driven and surgical: avoid speculative features, unsafe shortcuts, destructive edits, and unnecessary complexity.
- For ambiguous or high-risk work, surface assumptions and tradeoffs before committing to an approach.

## Known Cautions
- Current readiness logic can report `ready=true` from stale camera frames; check `opened`, `error`, and `frame_age_seconds` before trusting live readiness.
- Future recordings from current code include frame timestamp artifacts; older sample sessions may not contain them.
