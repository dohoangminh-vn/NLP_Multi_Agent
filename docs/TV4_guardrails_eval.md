# TV4 – Guardrails, Critic & Evaluation: hướng dẫn tích hợp

## 1. Bàn giao

| File | Vai trò |
|---|---|
| `data/syllabus_rules.json` | Quy ước 3 tầng (AST nodes / built-ins / methods + imports) Bài 16–29, có số trang SGK |
| `src/tools/ast_guard.py` | AST Syllabus Guard (chỉ quét `solution_code`) |
| `src/tools/code_sandbox.py` | Chạy `solution_code` với `test_cases` trong subprocess cô lập |
| `src/tools/pedagogy_checks.py` | Kiểm tra tất định: tổng phút, trích dẫn, cấu trúc 3 phiếu Bloom, nạp YCCĐ |
| `src/agents/critic.py` | Node Critic: AST + Sandbox + kiểm tra tất định + 1 lượt LLM chấm 5 trục |
| `src/utils/llm.py` | Lớp LLM trung lập (LangChain `init_chat_model`), đếm token, LLM giả lập cho test |
| `eval/test_dataset.json` | 28 ca = 14 bài × 2 hồ sơ lớp (45p lý thuyết/thực hành + 90p) |
| `eval/baselines.py` | B1, B2, B3, B4 |
| `eval/evaluator.py` | Bộ chấm thống nhất cho cả 4 cấu hình |
| `eval/run_benchmark.py` | Chạy Ablation, ghi JSON + MLflow, sinh bảng |
| `eval/human_eval.py` | Xuất gói chấm mù, tính Cohen's κ / Spearman ρ |
| `eval/error_analysis.py` | Sinh phần Error Analysis |
| `eval/verify_report_metrics.py` | Kiểm toán: số liệu trong báo cáo/slide phải khớp 100% file log |
| `tests/test_ast_guard.py`, `tests/test_sandbox.py`, `tests/test_critic.py`, `tests/test_eval.py` | unit test của TV4 (toàn repo: 399 test) |

`src/schemas.py` được dựng đúng theo sơ đồ 2.3 để các module chạy được ngay. **TV1 là chủ sở hữu**: nếu TV1 chốt bản khác thì chỉ cần giữ nguyên tên trường.

## 2. Quy ước phạm vi: có trong SGK thì cho phép, không có thì cấm

Tập được phép của Bài *k* = hợp các `unlock` của mọi bài ≤ *k*. Những điểm dưới đây đã đối chiếu bằng cách quét text của `sgk-tin-hoc-10-kntt.pdf`:

* SGK không dạy: `try/except`, `lambda`, `pass`, `is`, phép cắt `[a:b]` (chỉ xuất hiện ở Bài 31), f-string, `sum/max/min/sorted/abs/round/enumerate/zip`.
* Mở khóa theo bài: Bài 22 `list`, `len()`, `del`, `.append()` · Bài 23 `in`, `.insert/.remove/.clear` · Bài 25 `.find/.split/.join` · Bài 26 `def/return` · Bài 28 `global`.
* **Cảnh báo mềm** (không tính lỗi, `strict=True` thì thành lỗi): `elif` (SGK dùng từ Bài 30), `+=` (SGK không dùng), `pass`.
* `continue` chỉ có trong bảng từ khóa của SGK, nhưng vẫn mở ở Bài 21 theo kế hoạch nhóm.
* `sgk_pages` là **số trang in** (= trang PDF − 1). Ví dụ Bài 20: trang 105–107.

→ **TV3 cần xác nhận lại** các mục trên với SGV trước Gate 2. Muốn đổi thì chỉ sửa JSON, không phải sửa code.

## 3. Hợp đồng với các thành viên

**TV1 – `src/graph/workflow.py`**
```python
from src.agents.critic import CriticAgent, CriticConfig
critic = CriticAgent(llm)                       # B4; B3: CriticAgent(llm, CriticConfig.llm_only())
graph.add_node("critic", critic)                # nhận state (dict | AgentState)
# critic trả về {"critic_report": CriticReport, "iteration": iteration + 1}
# Conditional edge: not report.is_passed and iteration < 3 → detail_designer (Revision Mode)
```
`run_benchmark` dựng đồ thị qua `build_graph(llm=..., critic_node=..., retriever=..., checkpointer=None)`. Hàm chỉ cần nhận `critic_node`, các tham số khác tự được lọc theo chữ ký hàm. Ngoài ra, `graph.invoke({"request": req, "iteration": 0})` phải trả về state có `draft_plan` và `retrieved_chunks`.

**TV2 – retriever**: cần một hàm factory không tham số, trả về object có `.retrieve(request) -> list[SourceCitation]` (hoặc trả về hàm `fn(request)`), xếp theo thứ hạng. `doc_name` của SGK nên chứa chữ "SGK". File `data/yccd_taxonomy.json` có thể ở một trong các dạng `{"F10.B20.YC1": {"bai_so": 20, "text": ...}}`, `[{code, bai_so, text}]` hoặc `{"20": [...]}`.

**TV3 – Designer (Revision Mode)**: đọc `critic_report.revision_instructions`. Nội dung chia 4 mục: [1] vượt phạm vi (có số dòng và gợi ý thay thế đúng theo bài), [2] Sandbox, [3] cấu trúc/trích dẫn/phân hóa, [4] nhận xét LLM. `solution_code` dùng `input()` **không prompt**. Tier đặt là `Cơ bản` / `Đạt chuẩn` / `Nâng cao`; `bloom_level` là `Nhớ/Hiểu` / `Vận dụng` / `Phân tích/Sáng tạo`.

**TV5 – Live Trace**: dùng `CriticAgent.history[-1].to_trace_event()`, trả về `{"status": "red"|"green", "ast_violations", "sandbox_failures", "scores", ...}`. Ngoài ra `tests/fixtures/plan_bai20_violations.json` là sẵn tình huống "thẻ đỏ", `plan_bai20_clean.json` là "thẻ xanh".

## 4. Thiết kế Ablation

| | Sinh giáo án | RAG | Critic trong vòng lặp |
|---|---|---|---|
| B1 | 1 lượt LLM | ✗ | ✗ |
| B2 | 1 lượt LLM | ✓ | ✗ |
| B3 | Đồ thị Multi-Agent | ✓ | LLM (tự soát code) + kiểm tra tất định phút/trích dẫn/Bloom |
| B4 | Đồ thị Multi-Agent | ✓ | **AST + Sandbox** + LLM + kiểm tra tất định |

* B3 và B4 **chỉ khác nhau ở AST + Sandbox**, nên phần chênh lệch đo được là đóng góp của Neuro-Symbolic.
* Cả 4 cấu hình dùng **cùng prompt sinh, cùng mô tả phạm vi cú pháp**, cùng LLM, `temperature=0`, `bypass_hitl=True`.
* **Khi đánh giá**, mọi cấu hình đều được đo bằng cùng AST + Sandbox + Judge (xem `eval/evaluator.py`).
* `critic_miss_rate` = tỉ lệ ca mà Critic báo ĐẠT nhưng vẫn còn lỗi code. Đây là số liệu trả lời trực tiếp câu phản biện số 2.

Định nghĩa đầy đủ của từng metric nằm trong docstring của `eval/evaluator.py`; dùng nguyên văn trong báo cáo.

## 5. Lệnh chạy

```bash
pip install -r requirements.txt
pytest -q

# Chạy thử trước tích hợp (B3/B4 dùng vòng Designer↔Critic mô phỏng)
LLM_PROVIDER=groq LLM_MODEL=llama-3.3-70b-versatile \
python -m eval.run_benchmark --fallback-loop --limit 3 --retriever <module_TV2>:<factory>

# Chính thức (Tuần 3): cùng 1 model cho cả 4 cấu hình
python -m eval.run_benchmark --configs B1,B2,B3,B4 \
  --retriever <module_TV2>:<factory> --graph src.graph.workflow:build_graph \
  --judge-llm <module>:<factory_model_khác> --price-in 0.10 --price-out 0.40

python -m eval.error_analysis                                 # → eval/results/error_analysis.md
python -m eval.human_eval export --n 6 --configs B2,B4        # gửi pack/ + RUBRIC.md cho người chấm, giữ kín key.json
python -m eval.human_eval analyze --ratings eval/human_eval/ratings_*.csv
python -m eval.verify_report_metrics --render-table           # bảng chuẩn để dán vào báo cáo
python -m eval.verify_report_metrics --doc report/bao_cao.md --doc slides/slide.pptx
```

Trong báo cáo, số liệu nằm ngoài bảng thì đánh dấu theo dạng `[[B4.out_of_scope_rate=0%]]` hoặc `[[human_eval.overall.kappa=0.71]]` để script kiểm toán bắt được.

Kiểm tra nhanh một đoạn code: `python -m src.tools.ast_guard file.py 20`.
