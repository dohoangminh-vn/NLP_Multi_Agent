# Multi-Agent soạn giáo án Tin học 10 (Chủ đề F, Bài 16–29)

Hệ thống LangGraph gồm các bước: truy hồi SGK (Hybrid RAG) → phân tích lớp → khung 4 hoạt động → **giáo viên duyệt khung (HITL)** → sinh giáo án theo CV 5512 kèm 3 phiếu phân hóa → kiểm định (AST Guard, Sandbox, Critic), lặp tối đa 3 vòng → xuất file Word.

Kế hoạch chi tiết: [ke_hoach_multi_agent_giao_vien.md](ke_hoach_multi_agent_giao_vien.md) · Hợp đồng API/SSE: [docs/api_contract.md](docs/api_contract.md) · Guardrails & đánh giá: [docs/TV4_guardrails_eval.md](docs/TV4_guardrails_eval.md)

## Chạy nhanh

```bash
pip install -r requirements.txt
python -m src.rag.ingest                      # PDF SGK → data/processed + data/yccd_taxonomy.json
uvicorn src.api.main:app --port 8000          # mở http://localhost:8000
pytest -q
```

* Chưa đặt `LLM_MODEL` thì hệ thống chạy **chế độ offline**: khung và giáo án được dựng tất định từ SGK cùng ngân hàng phiếu đã kiểm định. Chế độ này dùng để dựng UI và demo, **không** dùng để báo cáo số liệu.
* Gọi LLM thật: `LLM_PROVIDER=google_genai LLM_MODEL=gemini-2.0-flash` (hoặc `groq`, `ollama`…), cài thêm gói `langchain-<provider>` tương ứng.
* Chạy bằng Docker: `docker compose up --build`. MLflow chạy riêng bằng `docker compose --profile eval up mlflow`.
* Nút "Demo offline (mock)" trên giao diện phát lại kịch bản Bài 20 (thẻ đỏ → xanh). Muốn phát lại một phiên chạy thật, ghi trước bằng `python scripts/record_demo.py --bai 20` rồi phát qua `/api/demo/stream?scenario=…`.

## Cấu trúc theo người phụ trách

| TV | Thành phần | File chính |
|---|---|---|
| TV1 | Hợp đồng dữ liệu, đồ thị 6 node, HITL, checkpointer, mock, CI | `src/schemas.py`, `src/graph/workflow.py`, `tests/mocks/` (`scripts/build_mocks.py`), `.github/workflows/ci.yml` |
| TV2 | Sửa font PDF, ingest, YCCĐ, Hybrid RAG, Retriever, đánh giá truy hồi | `src/rag/pdf_unicode_fix.py`, `src/rag/ingest.py`, `src/rag/hybrid_search.py`, `src/agents/retriever.py`, `eval/retrieval_eval.py`, `eval/retrieval_queries.json` |
| TV3 | Profiler, Outline, Designer (Revision Mode), prompt, ngân hàng phiếu, xuất DOCX | `src/agents/{profiler,outline_planner,detail_designer}.py`, `src/prompts/*.md`, `data/worksheet_bank.json`, `src/utils/docx_exporter.py` |
| TV4 | Quy tắc 3 tầng, AST Guard, Sandbox, Critic, Ablation, Human Eval, kiểm toán số liệu | `data/syllabus_rules.json`, `src/tools/`, `src/agents/critic.py`, `eval/` |
| TV5 | FastAPI SSE, giao diện giáo viên, Docker, cache demo | `src/api/`, `frontend/`, `Dockerfile`, `docker-compose.yml`, `scripts/record_demo.py` |

## Ghi chú dữ liệu quan trọng

* **PDF SGK bị lỗi lớp text.** Font Arial/Consolas nhúng thiếu `/ToUnicode`, nên mọi công cụ đọc PDF (kể cả Docling) đều ra chữ rác. `pdf_unicode_fix.py` khôi phục bằng cách so khớp hình dạng glyph với font gốc rồi chèn ToUnicode; bảng ánh xạ đã lưu sẵn ở `data/font_maps/`, nên máy khác không cần cài font. Font còn dùng ký tự ghép `ﬂ`/`ﬁ`, đã được tách lại trong bước làm sạch (nếu không, `float()` sẽ thành `ﬂoat()`).
* Số trang trong mọi metadata là **trang in** của SGK.
* `data/yccd_taxonomy.json` trích nguyên văn mục "Sau bài này em sẽ" của SGK. Chưa có PDF Chương trình GDPT 2018 (TT32) để đối chiếu với YCCĐ chính thức.

## Trạng thái hiện tại (đã chạy kiểm chứng)

* 399 test `pytest` đều đạt, gồm: chạy offline trọn luồng cả 14 bài, HITL dừng/tiếp tục, vòng sửa lỗi, API SSE, xuất DOCX, B3/B4 trên đồ thị thật.
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

1. **Chạy Ablation B1–B4 bằng LLM thật** (`python -m eval.run_benchmark …`, xem docs TV4). Chưa có số liệu Ablation nào.
2. TV3 đối chiếu `data/syllabus_rules.json` (v1.1, đã kiểm bằng code thật trong SGK) và ngân hàng phiếu với SGV.
3. Mở file `.docx` xuất ra bằng Microsoft Word để duyệt định dạng; hiện mới kiểm qua XML và QuickLook.
4. Thử giao diện trên trình duyệt thật; hiện mới kiểm bằng jsdom.
5. Human Eval (5–6 giáo án B2 vs B4) và kiểm toán số liệu trước Gate 4.
