# TV3 – Bàn giao tích hợp (bản ứng viên)

## Phạm vi và nguồn
- Code TV3 tái dựng từ các ZIP đã bàn giao đến commit `cb2f364` (người dùng xác nhận push).
- Đối chiếu `khanh_review.zip` (a120dc4) và `viet_review.zip` (1407ab2); không tuyên bố đây là các commit mới nhất trên máy nhóm.
- 42 phiếu được kế thừa từ `khanh/src/prompts/worksheet_bank.json`, giữ nguyên nội dung, thêm metadata nguồn ở bản local `src/prompts/few_shot/tv3_worksheet_bank.json`. Ưu tiên bank dùng chung nếu đã tồn tại. Không coi các phiếu này là trích SGK.
- Kế hoạch nhóm mục 3 (TV3), mục 5.3–5.4: yêu cầu 4 hoạt động, 4 bước tổ chức, 3 phiếu, HITL, 45/90 phút và 5–6 cặp giáo án chấm mù. Các thiết kế kỹ thuật không đồng nghĩa yêu cầu pháp lý.

## Cách chạy
1. Giải nén ZIP vào gốc repo trên branch `hoa`. Ba file hiện có được cập nhật: `prompt_builder.py`, `test_prompt_builder.py`, `detail_designer.py`; các file khác là mới.
2. `python -m pytest tests/tv3 -q -rs` để chạy TV3 độc lập. Các test phụ thuộc nhóm sẽ được skip nếu thiếu module.
3. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/tv3_integration.ps1 -InstallGraphDeps -RunDemo` tạo thư mục TEMP từ remote refs đã fetch, overlay file TV3, dùng Taxonomy Việt và chạy test. Không merge, không push, không sửa source branch khác. Cờ InstallGraphDeps chủ động cài phụ thuộc vào .venv; bỏ cờ nếu đã cài. Cờ RunDemo yêu cầu corpus thực tế ở branch Khánh; bỏ cờ nếu chưa có corpus. `-LiveRetriever` dùng Retriever của Khánh thay vì chọn chunk từ JSONL. Không phải đánh giá RAG của Việt.
4. Trong thư mục isolated `combined` được in ra, có thể chạy `python -m scripts.tv3_demo --help`. Nếu muốn thử LLM, cấu hình provider và model theo `src/utils/llm.py`, cài provider phù hợp, dùng `--llm --output <folder>`. Không commit API key. CLI yêu cầu duyệt khung nếu không dùng cờ demo-bypass; không chạy code LLM sinh và xuất bản nháp chưa kiểm định. Không gửi tài liệu cá nhân qua API nếu chưa có quyền.

## Kết quả thực nghiệm tại thời điểm đóng gói
- 221 test TV3 độc lập (không tính 18 test tích hợp phụ thuộc nhóm).
- 238 passed, 1 skipped trên bản phối hợp snapshot Khánh + Taxonomy và DTO Việt. Test LangGraph skip vì môi trường kiểm tra không có langgraph. Các bài test dùng chunk giả lập được ghi nhãn FIXTURE, không phải truy hồi SGK thật.
- 14 bài × 3 mode được kiểm thử hợp đồng trên fixture; 42 phiếu đi qua AST Guard strict và Sandbox của Khánh trong 14 lần chạy Critic offline.
- Kiểm thử vòng sửa lỗi thật bằng Critic + LLM giả lập: vòng 1 sai output, vòng 2 sửa đúng solution, giữ nguyên test case.
- Khi chạy thêm bộ test cũ của Khánh (Schema/Exporter/Worksheet Bank), kết quả 447 passed, 2 failed, 1 skipped. Hai test cũ thất bại có chủ đích theo contract mới: `test_outline_llm_bad_minutes_falls_back` mong chấp nhận tên A/B/C/D; `test_designer_llm_and_revision_mode` yêu cầu sinh khi thiếu mục tiêu/nguồn/duyệt và sửa bằng văn bản tự do. Không sửa test của thành viên khác để tạo PASS giả. Cần TV1/TV4 cập nhật kỳ vọng khi merge.
- Không có điểm đánh giá LLM thật, Recall@5, MRR, faithfulness hoặc Human Eval được xác nhận từ các test này.

## Các contract cần chốt với nhóm
- TV1: Schema hiện vẫn là bản tạm của Khánh. Graph hiện có thể xuất cảnh báo sau 3 vòng; nếu feedback không có targets, phải dừng để GV/TV4 xác định thay vì gọi Designer vô hạn. Đề nghị tích hợp sequential driver hoặc thống nhất route riêng. Không ghi đè workflow của TV1.
- TV2: sử dụng một taxonomy chính thức và đối chiếu nội dung 28 YCCĐ với CT/SGK. Adapter hiểu DTO DocumentChunk và citation; chưa nối Hybrid Search mới của Việt do snapshot chưa có lớp truy hồi hoàn chỉnh.
- TV4: CriticBridge chỉ ánh xạ lỗi AST/Sandbox có vị trí phiếu xác định từ history. Không tự suy đoán target cho lỗi nội dung/trích dẫn/thời lượng/YCCĐ; không cấp quyền sửa test để che lỗi. Sau sửa phải chạy lại Critic. Không coi Sandbox hiện tại là ranh giới cách ly bảo mật đủ mạnh cho mã không tin cậy; dùng môi trường cô lập phù hợp trước khi chạy mã LLM thật.
- TV5: nối UI HITL và API export riêng phiếu HS; module này không thay đổi API của TV5.

## Điểm còn tồn tại
- Sáu phiếu B16–17 trong bank Khánh có các test case lặp lại cùng input/output; có nhãn edge nhưng không phải ca biên độc lập. Giữ nguyên để không tự sửa học liệu của nhóm; cần review lại theo bài toán không có input.
- Offline Designer chỉ là bản nháp minh họa; mục tiêu/sản phẩm và hoạt động còn chung chung. Khớp schema, AST và test không chứng minh chất lượng sư phạm.
- YCCĐ, nguồn trang SGK, giới hạn cú pháp và quy tắc CV 5512 cần giáo viên/TV2/TV4 xác nhận trước khi chốt. Chưa kiểm định toàn bộ graph/HITL thật, RAG thật, API, LLM thật hoặc render Word cho mọi giáo án.
- Bank fallback là bản snapshot, không nên duy trì song song lâu dài. Sau khi merge bank dùng chung, loại bỏ bản sao nếu nhóm thống nhất.

## Kế hoạch Human Evaluation
Chọn 6 bài đại diện 16, 19, 20, 21, 25, 29; dùng cùng request và cùng điều kiện lớp cho cả hai cấu hình so sánh do TV4 thống nhất. Sinh giáo án bằng LLM thật, chạy kiểm định trên hệ thống, ẩn tên cấu hình, tên model và metadata tiết lộ nguồn phương pháp trước khi gửi chấm. Không dùng bản nháp fixture làm dữ liệu đánh giá chất lượng. Tạo mã ngẫu nhiên cho 12 tài liệu, lưu bảng ánh xạ riêng. Giáo viên chấm độc lập 1–5 theo các tiêu chí bên dưới, ghi minh chứng/lỗi cụ thể. Đây là rubric đề xuất, không phải thang chấm chính thức của Bộ GDĐT.

| Tiêu chí | 1 điểm | 3 điểm | 5 điểm |
|---|---|---|---|
| YCCĐ và kiến thức | Sai/thiếu cốt lõi | Đúng phần lớn, còn thiếu | Bao phủ đúng, mục tiêu quan sát được |
| Cấu trúc 5512 | Thiếu thành phần chính | Đủ khung nhưng chưa cụ thể | Đủ, mạch lạc, 4 bước rõ GV/HS |
| Tính khả thi | Không phù hợp thời lượng/phòng máy | Dùng được khi chỉnh sửa | Tổ chức khả thi, phút và học liệu phù hợp |
| Phân hóa Bloom | Ba nhóm không khác nhau | Có khác độ khó | Khác thao tác tư duy, có hỗ trợ phù hợp |
| Bài tập và lời giải | Sai/vượt phạm vi | Đúng phần lớn | Đúng đặc tả, test có ý nghĩa, phù hợp phạm vi |
| Nguồn và trình bày | Nguồn không kiểm chứng | Có nguồn nhưng còn chung | Nguồn xác thực, Word dễ sử dụng |

Mức 2/4 là trung gian giữa các mô tả. Thu điểm và nhận xét, bàn giao dữ liệu ẩn danh cho TV4 tính đồng thuận Cohen's kappa/Spearman phù hợp thang đo. Không tự tạo điểm số giáo viên.
