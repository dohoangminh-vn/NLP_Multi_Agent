# Multi-Agent soạn giáo án Tin học 10 (Chủ đề F, Bài 16–29)

Hệ thống LangGraph gồm các bước: truy hồi SGK (Hybrid RAG) → phân tích lớp → khung 4 hoạt động → **giáo viên duyệt khung (HITL)** → sinh giáo án theo CV 5512 kèm 3 phiếu phân hóa → kiểm định (AST Guard, Sandbox, Critic), lặp tối đa 3 vòng → xuất file Word.

Kế hoạch chi tiết: [ke_hoach_multi_agent_giao_vien.md](ke_hoach_multi_agent_giao_vien.md).

## Chạy nhanh

```bash
pip install -r requirements.txt
python -m src.rag.ingest                      # PDF SGK → data/processed (chunk + index) + data/yccd_taxonomy.json
uvicorn src.api.main:app --port 8000          # mở http://localhost:8000
pytest -q
```

* Chưa đặt `LLM_MODEL` thì hệ thống chạy **chế độ offline**: khung và giáo án được dựng tất định từ SGK cùng ngân hàng phiếu đã kiểm định. Chế độ này dùng để dựng UI và demo, **không** dùng để báo cáo số liệu.
* Gọi LLM thật: `LLM_PROVIDER=google_genai LLM_MODEL=gemini-2.0-flash` (hoặc `groq`, `ollama`…), cài thêm gói `langchain-<provider>` tương ứng.
* Chạy bằng Docker: `docker compose up --build`. MLflow chạy riêng bằng `docker compose --profile eval up mlflow`.
* Nút "Demo offline (mock)" trên giao diện phát lại kịch bản Bài 20 (thẻ đỏ → xanh). Muốn phát lại một phiên chạy thật, ghi trước bằng `python -m src.api.main record --bai 20 --name bai20_real` (lưu vào `tests/mocks/demo_cache/`) rồi phát qua `/api/demo/stream?scenario=bai20_real`.

## Cấu trúc mã nguồn

```text
multi-agent-cs-lesson-planner/
├── .github/workflows/ci.yml       # (TV1) pytest Schema, AST Guard... trên mọi PR
├── data/
│   ├── raw_pdfs/                  # (TV2) PDF SGK Tin học 10 KNTT (bản *_unicode.pdf sinh tự động, không commit)
│   ├── processed/                 # (TV2) sgk_chunks.jsonl, sgk/bai_XX.md, index/ (BM25 + vector), font_maps/
│   ├── yccd_taxonomy.json         # (TV2 + TV3) YCCĐ theo Bài 16–29
│   └── syllabus_rules.json        # (TV4) Quy ước 3 tầng (AST, Built-ins, Methods) Bài 16–29
├── src/
│   ├── schemas.py                 # (TV1) Pydantic models chung
│   ├── rag/
│   │   ├── ingest.py              # (TV2) Sửa font PDF, parser (pypdf/Docling), tách từ VN, dựng index BM25 + Vector
│   │   └── hybrid_search.py       # (TV2) Hybrid Search (bai_so <= k) + RRF + Reranker
│   ├── tools/
│   │   ├── ast_guard.py           # (TV4) Kiểm định cú pháp 3 tầng trên solution_code
│   │   └── code_sandbox.py        # (TV4) Chạy solution_code với test_cases (UTF-8, 2s) + tổng hợp kiểm định theo phiếu
│   ├── prompts/                   # (TV3) Prompt CV 5512 / Bloom / Revision Mode + worksheet_bank.json (few-shot)
│   ├── agents/
│   │   ├── retriever.py           # (TV2) Node truy xuất + Grader + Query Rewriter
│   │   ├── profiler.py            # (TV3) Node phân tích lớp (45p/90p) & ma trận Bloom
│   │   ├── outline_planner.py     # (TV3) Node sinh khung 4 hoạt động phục vụ HITL
│   │   ├── detail_designer.py     # (TV3) Node sinh chi tiết 5512 + 3 phiếu + Revision Mode
│   │   └── critic.py              # (TV4) Node kiểm định tổng hợp (AST + Sandbox + kiểm tra tất định + LLM Critic)
│   ├── graph/workflow.py          # (TV1) StateGraph 6 node, HITL Checkpointer, Reflection Loop
│   ├── utils/
│   │   ├── docx_exporter.py       # (TV3) Xuất file Word theo Phụ lục IV – CV 5512
│   │   └── llm.py                 # Lớp LLM trung lập provider + đếm token (dùng chung)
│   └── api/main.py                # (TV5) FastAPI SSE, HITL resume, xuất DOCX, ghi/phát cache demo
├── frontend/                      # (TV5) Web UI (Slider 100%, Live Trace, Citations, HITL)
├── eval/
│   ├── retrieval_queries.json     # (TV2) 45 truy vấn chuẩn (nhãn trang suy từ cụm từ SGK)
│   ├── retrieval_eval.py          # (TV2) Recall@5, Hit@5, MRR; --build-queries
│   ├── test_dataset.json          # (TV4 + TV2) 14 bài × 2 hồ sơ lớp = 28 ca
│   ├── baselines.py               # (TV4) B1, B2, B3, B4
│   ├── run_benchmark.py           # (TV4) Bộ chấm thống nhất + Ablation + MLflow; human-eval; error-analysis
│   └── verify_report_metrics.py   # (TV4) Kiểm toán số liệu báo cáo khớp 100% log
├── tests/
│   ├── mocks/                     # (TV1) Mock từng node + luồng SSE mẫu (build_mocks.py), giáo án mẫu Bài 20
│   ├── test_schemas.py            # (TV1) Pydantic contract + Profiler/Outline/Designer
│   ├── test_ast_guard.py          # (TV4) Bắt lỗi 3 tầng vượt chương trình
│   ├── test_sandbox.py            # (TV4) Cô lập, timeout, UTF-8 I/O
│   └── test_*.py                  # critic, rag, workflow, api, docx_exporter, eval, worksheet_bank
├── Dockerfile, docker-compose.yml # (TV5)
└── README.md
```

Ngoài cây trong kế hoạch, repo chỉ có thêm: file cấu hình (`requirements.txt`, `pyproject.toml`, `.gitignore`, `.dockerignore`), `src/utils/llm.py` (lớp gọi LLM mà mọi agent cùng dùng), và các file test bổ sung trong `tests/`. Khi đẩy lên GitHub, có thể đặt tên repo là `multi-agent-cs-lesson-planner`.

## Ghi chú dữ liệu quan trọng

* **PDF SGK bị lỗi lớp text.** Font Arial/Consolas nhúng thiếu `/ToUnicode`, nên mọi công cụ đọc PDF (kể cả Docling) đều ra chữ rác. Bước 1 của `src/rag/ingest.py` khôi phục bằng cách so khớp hình dạng glyph với font gốc rồi chèn ToUnicode. Bảng ánh xạ đã lưu sẵn ở `data/processed/font_maps/`, nên máy khác không cần cài font. Font còn dùng ký tự ghép `ﬂ`/`ﬁ`, đã được tách lại trong bước làm sạch (nếu không, `float()` sẽ thành `ﬂoat()`).
* Số trang trong mọi metadata là **trang in** của SGK.
* `data/yccd_taxonomy.json` trích nguyên văn mục "Sau bài này em sẽ" của SGK. Chưa có PDF Chương trình GDPT 2018 (TT32) để đối chiếu với YCCĐ chính thức.

## Trạng thái hiện tại (đã chạy kiểm chứng)

* Toàn bộ `pytest` đều đạt, gồm: chạy offline trọn luồng cả 14 bài, HITL dừng/tiếp tục, vòng sửa lỗi, API SSE, xuất DOCX, B3/B4 trên đồ thị thật.
* Truy hồi trên 45 truy vấn chuẩn ([eval/results/retrieval_metrics.json](eval/results/retrieval_metrics.json)):

| Cấu hình | Recall@5 | Hit@5 | MRR | Lọt bài sau |
|---|---|---|---|---|
| BM25 (không tách từ) | 0.902 | 0.978 | 0.857 | 0 |
| BM25 + pyvi | 0.913 | 0.978 | 0.857 | 0 |
| Dense (n-gram ký tự, *không phải mô hình nơ-ron*) | 0.869 | 0.933 | 0.848 | 0 |
| Hybrid | 0.891 | 0.956 | 0.854 | 0 |
| Hybrid + reranker từ vựng | 0.913 | 0.978 | 0.830 | 0 |

  Chưa chạy dense/cross-encoder nơ-ron (`--neural`). Với bộ truy vấn hiện tại, hybrid **chưa vượt** BM25. Bộ truy vấn khá thiên về từ vựng, nên cần bổ sung câu hỏi diễn đạt khác hẳn SGK trước khi kết luận.

## Việc còn lại cần cả nhóm

1. **Chạy Ablation B1–B4 bằng LLM thật** (xem mục "Lệnh chạy" bên dưới). Chưa có số liệu Ablation nào.
2. TV3 đối chiếu `data/syllabus_rules.json` (v1.1, đã kiểm bằng code thật trong SGK) và ngân hàng phiếu với SGV.
3. Mở file `.docx` xuất ra bằng Microsoft Word để duyệt định dạng; hiện mới kiểm qua XML và QuickLook.
4. Thử giao diện trên trình duyệt thật; hiện mới kiểm bằng jsdom.
5. Human Eval (5–6 giáo án B2 vs B4) và kiểm toán số liệu trước Gate 4.

## Hợp đồng API & SSE (TV1 ↔ TV5)

Base URL: `http://localhost:8000`. Mọi payload là JSON UTF-8, theo đúng model trong `src/schemas.py`.

### Endpoint

| Method | Path | Body | Trả về |
|---|---|---|---|
| GET | `/api/health` | – | `{"status": "ok", "llm": "<provider:model hoặc offline>"}` |
| GET | `/api/meta` | – | `{"lessons": [{"bai", "title", "sgk_pages"}], "scope_modes": [{"value", "label", "minutes"}]}` |
| POST | `/api/lesson/start` | `LessonRequest` | **SSE stream** |
| POST | `/api/lesson/resume` | `{"thread_id": str, "outline": LessonOutline}` | **SSE stream** (chạy tiếp từ HITL) |
| GET | `/api/lesson/{thread_id}` | – | `{"status", "request", "outline", "plan", "critic_report", "verification", "iteration"}` |
| GET | `/api/lesson/{thread_id}/export` | – | File `.docx` (CV 5512); `thread_id=mock-bai20` xuất giáo án của demo mock |
| GET | `/api/demo/stream?speed=1&scenario=` | – | **SSE** phát lại mock Bài 20, hoặc cache `tests/mocks/demo_cache/<scenario>.json` |
| GET | `/api/demo/list` | – | Danh sách cache demo đã ghi |

`status` ∈ `running | waiting_hitl | done | error`.

### Sự kiện SSE

Định dạng chuẩn: `event: <type>\ndata: <json>\n\n`. Client dùng `fetch()` + `ReadableStream` (vì là POST). Mỗi node có đúng một cặp `node_start`/`node_end`.

| event | data |
|---|---|
| `session` | `{"thread_id", "llm"}` |
| `node_start` | `{"node", "label"}` |
| `node_end` | `{"node", "label", "summary": {...}}` — xem summary dưới |
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

`Verification` (1 phần tử / phiếu, do `verify_plan()` trong `src/tools/code_sandbox.py` sinh ra):
```json
{"tier": "Nâng cao", "worksheet": "Phiếu Nâng cao", "ast_pass": true, "ast_messages": [], "ast_warnings": [],
 "sandbox_pass": true, "n_passed": 3, "total": 3,
 "cases": [{"index": 0, "status": "PASS", "stdin": "...", "expected": "...", "actual": "...", "is_edge_case": false}]}
```

### Luồng UI

1. Cấu hình → `POST /start` → nhận `session`, các `node_*`, rồi `hitl_required` (nếu `bypass_hitl=false`) → stream đóng.
2. GV sửa khung → `POST /resume` → `node_*` của designer/critic (lặp tối đa 3 vòng, mỗi vòng 1 event `critic`) → `final` → `done`. Tổng phút khác 45/90 → 422.
3. Nút tải Word → `GET /export`.

## Guardrails, Critic & Đánh giá (TV4)

### Quy ước phạm vi (`data/syllabus_rules.json` v1.1): có trong SGK thì cho phép

Tập được phép của Bài *k* = hợp các `unlock` của mọi bài ≤ *k*. Quy ước đã đối chiếu với **code thật trong SGK** (sau khi sửa lớp text PDF):

* Mở khóa theo bài:
  * Bài 19 `if`/so sánh/`and-or-not`, `round()` · Bài 20 `for`, `range()` · Bài 21 `while`, `break`, `continue`, `chr()`
  * Bài 22 `list`, `len()`, `del`, `.append()` · Bài 23 `in`, `.insert/.remove/.clear` · Bài 25 `.find/.split/.join`
  * Bài 26 `def/return` + `abs/list/ord/divmod` (Bảng 26.1) · Bài 27 `min()`, `.upper/.lower` · Bài 28 `global`, `.title` · Bài 29 `sum()`
* Không xuất hiện trong code SGK Bài 16–29, nên bị cấm: `try/except`, `lambda`, f-string, phép cắt `[a:b]` (chỉ có từ Bài 31), `max/sorted/enumerate/zip`, mọi `import`.
* **Cảnh báo mềm** (không tính lỗi, `strict=True` thì thành lỗi): `elif` (SGK dùng từ Bài 30), `+=` (SGK không dùng), `pass`.
* `continue` chỉ có trong bảng từ khóa của SGK, nhưng vẫn mở ở Bài 21 theo kế hoạch nhóm.
* `sgk_pages` là **số trang in**. Ví dụ Bài 20: trang 105–107.

→ **TV3 cần xác nhận lại** với SGV. Muốn đổi thì chỉ sửa JSON, không phải sửa code. Kiểm tra nhanh một đoạn code: `python -m src.tools.ast_guard file.py 20`.

### Hợp đồng với các thành viên

**TV1 – `src/graph/workflow.py`**
```python
from src.agents.critic import CriticAgent, CriticConfig
critic = CriticAgent(llm)                       # B4; B3: CriticAgent(llm, CriticConfig.llm_only())
graph.add_node("critic", critic)                # nhận state (dict | AgentState)
# critic trả về {"critic_report": CriticReport, "iteration": iteration + 1}
# Conditional edge: not report.is_passed and iteration < 3 → detail_designer (Revision Mode)
```
`run_benchmark` dựng đồ thị qua `build_graph(llm=..., critic_node=..., retriever=..., checkpointer=None)`. Các tham số được lọc theo chữ ký hàm.

**TV2 – retriever**: factory không tham số `src.agents.retriever:build_benchmark_retriever` trả về object có `.retrieve(request) -> list[SourceCitation]` theo thứ hạng.

**TV3 – Designer (Revision Mode)**: đọc `critic_report.revision_instructions`. Nội dung chia 4 mục: [1] vượt phạm vi (có số dòng và gợi ý thay thế đúng theo bài), [2] Sandbox, [3] cấu trúc/trích dẫn/phân hóa, [4] nhận xét LLM. `solution_code` dùng `input()` **không prompt**. Tier đặt là `Cơ bản` / `Đạt chuẩn` / `Nâng cao`; `bloom_level` là `Nhớ/Hiểu` / `Vận dụng` / `Phân tích/Sáng tạo`.

**TV5 – Live Trace**: `CriticAgent.history[-1].to_trace_event()` trả về `{"status": "red"|"green", ...}`. Tình huống "thẻ đỏ" có sẵn ở `tests/mocks/plan_bai20_violations.json`, "thẻ xanh" ở `tests/mocks/plan_bai20_clean.json`.

### Thiết kế Ablation

| | Sinh giáo án | RAG | Critic trong vòng lặp |
|---|---|---|---|
| B1 | 1 lượt LLM | ✗ | ✗ |
| B2 | 1 lượt LLM | ✓ | ✗ |
| B3 | Đồ thị Multi-Agent | ✓ | LLM (tự soát code) + kiểm tra tất định phút/trích dẫn/Bloom |
| B4 | Đồ thị Multi-Agent | ✓ | **AST + Sandbox** + LLM + kiểm tra tất định |

* B3 và B4 **chỉ khác nhau ở AST + Sandbox**, nên phần chênh lệch đo được là đóng góp của Neuro-Symbolic.
* Cả 4 cấu hình dùng **cùng prompt sinh, cùng mô tả phạm vi cú pháp**, cùng LLM, `temperature=0`, `bypass_hitl=True`.
* **Khi đánh giá**, mọi cấu hình đều được đo bằng cùng AST + Sandbox + Judge (lớp `Evaluator` trong `eval/run_benchmark.py`; định nghĩa từng metric nằm ở comment mục B của file đó, dùng nguyên văn trong báo cáo).
* `critic_miss_rate` = tỉ lệ ca mà Critic báo ĐẠT nhưng vẫn còn lỗi code. Đây là số liệu trả lời trực tiếp câu phản biện số 2.

### Lệnh chạy

```bash
# Chạy thử trước tích hợp (B3/B4 dùng vòng Designer↔Critic mô phỏng)
LLM_PROVIDER=groq LLM_MODEL=llama-3.3-70b-versatile \
python -m eval.run_benchmark --fallback-loop --limit 3 --retriever src.agents.retriever:build_benchmark_retriever

# Chính thức: cùng 1 model cho cả 4 cấu hình
python -m eval.run_benchmark --configs B1,B2,B3,B4 \
  --retriever src.agents.retriever:build_benchmark_retriever --graph src.graph.workflow:build_graph \
  --judge-llm <module>:<factory_model_khác> --price-in 0.10 --price-out 0.40

python -m eval.run_benchmark error-analysis                       # → eval/results/error_analysis.md
python -m eval.run_benchmark human-eval export --n 6 --configs B2,B4   # gửi pack/ + RUBRIC.md cho người chấm, giữ kín key.json
python -m eval.run_benchmark human-eval analyze --ratings eval/human_eval/ratings_*.csv
python -m eval.retrieval_eval                                     # → eval/results/retrieval_metrics.json
python -m eval.verify_report_metrics --render-table               # bảng chuẩn để dán vào báo cáo
python -m eval.verify_report_metrics --doc report/bao_cao.md --doc slides/slide.pptx
```

Trong báo cáo, số liệu nằm ngoài bảng thì đánh dấu theo dạng `[[B4.out_of_scope_rate=0%]]` hoặc `[[human_eval.overall.kappa=0.71]]` để script kiểm toán bắt được.
