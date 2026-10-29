---
name: check-standards
description: Kiểm tra bản vẽ AutoCAD (DXF) theo một hoặc nhiều tiêu chuẩn (TCVN, QCVN, NFPA, ASHRAE, JIS, ISO...) qua MCP standards-checker: chọn tiêu chuẩn áp dụng, tra điều khoản, đo dữ liệu thật trong bản vẽ, so sánh, đánh dấu lỗi, đề xuất sửa và kiểm tra lại. Dùng khi người dùng yêu cầu "check theo tiêu chuẩn", "kiểm tra bản vẽ", "check TCVN/QCVN/NFPA...".
---

# Quy trình kiểm tra bản vẽ theo tiêu chuẩn (tra trước, đo, kết luận)

1. **Xác định tiêu chuẩn áp dụng**: gọi `list_standards`. Nếu người dùng chưa nói rõ tiêu chuẩn nào áp dụng cho dự án, hỏi một lần rồi lưu bằng `set_applicable_standards`. Không tự giả định.
2. **Tra cứu**: gọi `search_standards` (lọc theo các tiêu chuẩn áp dụng) và `list_rules`. Kết quả `wiki_notes` chỉ là tóm tắt: số liệu kết luận lấy từ `passages` hoặc từ `quote` đi kèm. Chỉ dùng điều khoản lấy từ công cụ, không tự nhớ số liệu. Luôn ghi rõ tiêu chuẩn, file, trang.
3. **Khi các tiêu chuẩn mâu thuẫn**: nêu từng yêu cầu kèm nguồn, không gộp ngầm. Chỉ chọn "yêu cầu chặt hơn" nếu người dùng đồng ý hoặc dự án đã quy định.
4. **Trích xuất rule**: điều kiện gồm đại lượng, toán tử, ngưỡng, đơn vị. Nếu điều khoản chưa có luật có cấu trúc, đề xuất thêm vào `knowledge/rules/<TIÊU_CHUẨN>.json` (theo `_TEMPLATE.json`) và để người dùng xác nhận số liệu trước khi dùng.
5. **Đọc bản vẽ**: gọi `inspect_drawing` để biết layer, block và đơn vị. Nếu đơn vị không phải mm, truyền `unit_to_mm`.
6. **Đo và so sánh**: gọi `check_drawing`. Với `NO_DATA`, báo tên layer/block thực tế để chỉnh rule, không kết luận PASS.
7. **Đánh dấu lỗi**: liệt kê từng vi phạm (handle, layer, giá trị đo, yêu cầu, tiêu chuẩn và điều khoản).
8. **Tổng hợp thành ghi chú (khi được yêu cầu tóm tắt, so sánh tiêu chuẩn, hoặc chốt một quy ước dùng lại)**: dùng `write_note`.
   Mỗi đoạn phải kết thúc bằng mã trích dẫn `[c1]`; `quote` chép nguyên văn ngắn từ `search_standards`; không viết con số
   nào mà đoạn trích đã dẫn không chứa; tách riêng từng tiêu chuẩn khi chúng khác nhau. Nếu kết quả có `errors`, sửa theo
   thông báo rồi lưu lại, không bỏ trích dẫn để lưu cho được. Trang `needs_review` hoặc `stale` không dùng để kết luận.
   Sau khi nạp tài liệu mới, xem `notes_check` hoặc gọi `check_notes`; trang `stale` phải viết lại từ nguồn mới.
9. **Sửa và kiểm tra lại**: chỉ khi người dùng cho phép. Đề xuất phương án, sau khi bản vẽ cập nhật thì gọi lại `check_drawing`.
10. **Báo cáo**: tổng PASS/FAIL/NO_DATA theo từng tiêu chuẩn, bảng lỗi, các giả định, và nhắc đối chiếu bản gốc với đoạn trích từ OCR.
