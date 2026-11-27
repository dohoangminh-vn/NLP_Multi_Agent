# Quy tắc phân hóa Bloom — TV3 v0.1
Nguồn: S3 (trang 1–2), S6. Ánh xạ nhóm là lựa chọn thiết kế dự án.

| Nhóm | Mức tư duy ưu tiên | Thiết kế nhiệm vụ |
|---|---|---|
| Cơ bản | Nhớ, Hiểu | Nhận diện cú pháp, dự đoán đầu ra, bảng trace, điền 1–2 dòng có gợi ý. |
| Đạt chuẩn | Vận dụng | Tự triển khai bài toán quen thuộc, với yêu cầu đầu vào/đầu ra rõ ràng. |
| Nâng cao | Phân tích, Đánh giá, Sáng tạo | Phân tích lỗi, so sánh phương án theo tiêu chí, xử lý biên hoặc thiết kế bài toán mới trong phạm vi đã học. |

## Nguyên tắc
- Mức Bloom dựa trên thao tác nhận thức thực tế, không chỉ động từ trong câu hỏi.
- Tất cả nhóm hướng tới YCCĐ cốt lõi; nhóm cơ bản được hỗ trợ thêm, không mặc định miễn yêu cầu cần đạt.
- Nhóm là cấu hình hỗ trợ linh hoạt, không phải nhãn năng lực cố định của cá nhân.
- Tỷ lệ nhóm ảnh hưởng mức hỗ trợ và tổ chức lớp, không mặc định bằng tỷ lệ phút. Ba phiếu vẫn được tạo theo yêu cầu MVP; phiếu của nhóm 0% là tài liệu tùy chọn.
- Không tăng độ khó bằng cú pháp chưa học. Đề nâng cao phải dùng kiến thức trong phạm vi bài/tiết.
- Mỗi phiếu có nhiệm vụ rõ, sản phẩm dự kiến và tiêu chí thành công; code lời giải và test case tách riêng.

## Ví dụ tự thiết kế — Bài 20 (for)
- Cơ bản: giải thích số lần lặp của range(1, 5), điền cận vòng lặp.
- Đạt chuẩn: nhập n nguyên dương, tính tổng 1 đến n bằng for.
- Nâng cao: phân tích lỗi cận trên, sửa chương trình và kiểm tra n=1; nếu bài toán cho n=0, quy định rõ tổng rỗng bằng 0.
- Không dùng list comprehension, sum(), append() hoặc hàm tự định nghĩa để thay thế kiến thức đang học; danh sách chính thức do TV4 quản lý.
