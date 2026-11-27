# Hợp đồng API & SSE (TV1 ↔ TV5)

Base URL: `http://localhost:8000`. Mọi payload là JSON UTF-8, theo đúng model trong `src/schemas.py`.

## Endpoint

| Method | Path | Body | Trả về |
|---|---|---|---|
| GET | `/api/health` | – | `{"status": "ok", "llm": "<provider:model hoặc offline>"}` |
| GET | `/api/meta` | – | `{"lessons": [{"bai", "title", "sgk_pages"}], "scope_modes": [{"value", "label", "minutes"}]}` |
| POST | `/api/lesson/start` | `LessonRequest` | **SSE stream** |
| POST | `/api/lesson/resume` | `{"thread_id": str, "outline": LessonOutline}` | **SSE stream** (chạy tiếp từ HITL) |
| GET | `/api/lesson/{thread_id}` | – | `{"status", "request", "outline", "plan", "critic_report", "verification", "iteration"}` |
| GET | `/api/lesson/{thread_id}/export` | – | File `.docx` (CV 5512) |
| GET | `/api/demo/stream?speed=1` | – | **SSE** phát lại `tests/mocks/sse_stream_bai20.json` (mock cho UI / demo offline) |

`status` ∈ `running | waiting_hitl | done | error`.

## Sự kiện SSE

Định dạng chuẩn: `event: <type>\ndata: <json>\n\n`. Client dùng `fetch()` + `ReadableStream` (vì là POST).

| event | data |
|---|---|
| `session` | `{"thread_id"}` |
| `node_start` | `{"node", "label"}` |
| `node_end` | `{"node", "label", "summary": {...}}` — xem bảng summary dưới |
| `critic` | `{"iteration", "status": "red"|"green", "ast_violations": [str], "sandbox_failures": [str], "ast_pass", "sandbox_pass", "scores": {"faithfulness", "citation_precision", "yccd_coverage", "differentiation", "total_minutes"}, "gate_failures": [str], "warnings": [str]}` |
| `hitl_required` | `{"thread_id", "outline": LessonOutline, "target_minutes", "profile"}` |
| `final` | `{"thread_id", "plan": LessonPlan5512, "critic_report": CriticReport, "verification": [Verification], "iterations"}` |
| `error` | `{"message"}` |
| `done` | `{}` |

`node` ∈ `retriever | profiler | outline_planner | human_review | detail_designer | critic | export`.

Summary của `node_end`:
- `retriever`: `{"n_chunks", "chunks": [SourceCitation], "rewrites": int}`
- `profiler`: `{"profile": {...}}` (xem `src/agents/profiler.py`)
- `outline_planner`: `{"outline": LessonOutline}`
- `detail_designer`: `{"iteration", "n_activities", "n_worksheets", "revision": bool}`
- `critic`: giống event `critic`
- `export`: `{"ready": true}`

`Verification` (1 phần tử / phiếu):
```json
{"tier": "Nâng cao", "worksheet": "Phiếu Nâng cao", "ast_pass": true, "ast_messages": [], "ast_warnings": [],
 "sandbox_pass": true, "n_passed": 3, "total": 3,
 "cases": [{"index": 0, "status": "PASS", "stdin": "...", "expected": "...", "actual": "...", "is_edge_case": false}]}
```

## Luồng UI

1. Cấu hình → `POST /start` → nhận `session`, các `node_*`, rồi `hitl_required` (nếu `bypass_hitl=false`) → stream đóng.
2. GV sửa khung → `POST /resume` → `node_*` của designer/critic (lặp tối đa 3 vòng, mỗi vòng 1 event `critic`) → `final` → `done`.
3. Nút tải Word → `GET /export`.
