# Standards Checker MCP (TCVN và các tiêu chuẩn khác)

Kiểm tra bản vẽ AutoCAD (DXF) theo một hoặc nhiều tiêu chuẩn: TCVN, QCVN, NFPA, ASHRAE, JIS, ISO, v.v.

| Thành phần | Trong gói này |
|---|---|
| Kho tri thức | PDF theo từng tiêu chuẩn, OCR, SQLite FTS5 và embedding, cache theo SHA256 |
| Trang ghi chú | `knowledge/wiki/*.md`: tổng hợp có dẫn nguồn, được kiểm chứng với trang gốc |
| Gói luật | `knowledge/rules/<TIÊU_CHUẨN>.json`, mỗi tiêu chuẩn một file |
| Đọc bản vẽ | `dxf_reader.py` |
| Quy trình | `skill/SKILL.md` (check-standards) |

## Cài đặt
```
pip install -r requirements.txt          # bắt buộc: mcp, ezdxf, pypdf
pip install -r requirements-extra.txt    # tùy chọn: tìm ngữ nghĩa + OCR
```
Embedding mặc định `BAAI/bge-m3` (đa ngôn ngữ, nặng, cần mạng khi tải lần đầu). Máy yếu: `STD_EMBED_MODEL=intfloat/multilingual-e5-small`. Đổi mô hình rồi chạy `build_embeddings`.
OCR cần cài chương trình **Tesseract** và gói ngôn ngữ phù hợp. Đặt ngôn ngữ bằng `STD_OCR_LANG` (mặc định `vie+eng`; thêm `jpn` nếu có tài liệu tiếng Nhật).
(Biến cũ `TCVN_EMBED_MODEL`, `TCVN_OCR_LANG` vẫn được chấp nhận.)

## Kết nối Claude Desktop (claude_desktop_config.json)
```json
{ "mcpServers": { "standards-checker": { "command": "python", "args": ["C:/duong-dan/standards-mcp/server.py"] } } }
```
Claude Code: `claude mcp add standards-checker -- python /duong-dan/standards-mcp/server.py`
Skill: chép thư mục `skill/` thành `check-standards/` vào thư mục skills.

## Cache theo SHA256
- File PDF không đổi nội dung thì bị bỏ qua, kể cả khi bị đổi ngày giờ (`touched_only` trong báo cáo). Tên thư mục cha đổi thì coi là file khác; nếu hai file trùng nội dung sẽ có cảnh báo.
- Kết quả **OCR** được lưu theo (SHA256 của file, số trang, ngôn ngữ) và **embedding** được lưu theo (SHA256 của đoạn, mô hình). Nạp lại bằng `force=True` hoặc sửa một phần tài liệu thì chỉ phần mới bị tính lại. Đổi mô hình rồi đổi lại cũng không phải tính lại.
- Báo cáo `ingest_standards` có `ocr_run`, `ocr_cache_hits`, `embedded_chunks`, `embed_cache_hits`. CSDL cũ được tự nâng cấp (thêm cột `sha256`). Cache chỉ tăng thêm; muốn dọn, xóa file `knowledge/standards_index.db` rồi nạp lại.

## Trang ghi chú tổng hợp (có dẫn về điều khoản gốc)
Mục đích: có lớp tóm tắt và so sánh giữa các tiêu chuẩn mà không mất khả năng truy về nguyên văn. Claude viết, hệ thống kiểm chứng.
- `write_note(slug, title, body, citations)`: mỗi đoạn văn trong `body` kết thúc bằng mã trích dẫn như `[c1]`. `citations` gồm `id`, `source` (đường dẫn như `TCVN/file.pdf`), `page` và `quote` (nguyên văn, 8–400 ký tự, có thể nối bằng `...`).
- **Hệ thống không lưu** nếu: file hoặc trang không có trong kho, đoạn trích không nằm trong trang đó, body dẫn id chưa khai báo, hoặc không có trích dẫn nào.
- **Lưu nhưng đánh dấu `needs_review`** nếu: có số trong đoạn văn không xuất hiện trong trích dẫn đã dẫn (kể cả khi chỉ khác đơn vị), có đoạn không dẫn nguồn, hoặc trích từ OCR.
- `check_notes`: kiểm tra lại với kho hiện tại. `stale` khi file nguồn mất hoặc đoạn trích không còn khớp (cần viết lại); `needs_review` khi nguồn đã đổi (SHA256 khác) nhưng đoạn trích còn khớp, sau khi xem lại bạn có thể xác nhận bằng `accept_changed=True`. `ingest_standards` tự chạy kiểm tra này khi có file mới hoặc đổi.
- `search_standards` trả thêm `wiki_notes` (đoạn tóm tắt kèm trích dẫn gốc). Số liệu dùng để kết luận vẫn phải lấy từ `passages` hoặc từ `quote` trong ghi chú.
- Kiểm chứng là so khớp văn bản, không phải hiểu nghĩa: nó bảo đảm trích dẫn có thật và bắt lệch số, nhưng không bảo đảm phần diễn giải của câu văn đúng nghĩa. Với nội dung quan trọng, vẫn cần người đọc `quote`.
- Trang là Markdown thường, mở được bằng trình soạn thảo bất kỳ; có thể liên kết bằng `[[slug]]`.

## Thêm một tiêu chuẩn mới
1. **Tài liệu**: tạo thư mục `knowledge/pdf/<TÊN>/` (ví dụ `NFPA`, `ASHRAE`, `JIS`) rồi chép PDF vào. Tên thư mục chính là tên tiêu chuẩn. Nhờ Claude gọi `ingest_standards`.
2. **Luật có cấu trúc** (cho phần đo bản vẽ): sao chép `knowledge/rules/_TEMPLATE.json` thành `knowledge/rules/<TÊN>.json`, điền `standard`, `ref` (số hiệu, mục), ngưỡng và đối tượng cần đo. `id` phải là duy nhất.
3. **Chọn tiêu chuẩn áp dụng** cho dự án bằng `set_applicable_standards`. `check_drawing` mặc định chỉ chạy luật của các tiêu chuẩn này. Tra cứu `search_standards` thì mặc định tìm tất cả, có thể lọc bằng `standards`.

## Các kiểu đo trong luật
`dimension` (kích thước, mm), `line_length`, `block_width`, `area` (polyline kín, đơn vị `m2`), `block_count` (đếm block, đơn vị `count`). `unit` có thể là `mm`, `m`, `m2`, `count`.

## Việc bạn cần làm
- Hai luật TCVN-001, TCVN-002 là ví dụ minh họa (ngưỡng theo ví dụ trong video), chưa phải điều khoản thật. Hãy thay bằng số liệu từ tài liệu của bạn.
- Sửa `layer`/`block` trong rule cho khớp bản vẽ (dùng `inspect_drawing`).

## Giới hạn
- Đọc **DXF**; DWG cần convert trước. `area` bỏ qua cung tròn (bulge); `block_count` chỉ đếm trong modelspace. Chưa tự sửa bản vẽ.
- Tìm từ khóa dùng tokenizer Unicode cơ bản, kém với tiếng Nhật/Trung (không tách từ). Với các ngôn ngữ này nên dựa vào tìm ngữ nghĩa (embedding).
- Đã test: nạp PDF theo tiêu chuẩn, lọc theo tiêu chuẩn, OCR tiếng Anh, lưu và hợp nhất vector bằng embedding giả lập, gói luật, so sánh (mm, m2, count), cache SHA256 (bỏ qua khi chỉ đổi ngày giờ, dùng lại OCR và embedding, nâng cấp CSDL cũ), vòng đời trang ghi chú (lưu, từ chối trích dẫn sai, cảnh báo số liệu, stale, xác nhận nguồn đổi). **Chưa test:** các tool MCP của ghi chú qua server thật (chỉ test hàm bên dưới), mô hình embedding thật, OCR tiếng Việt/Nhật, đọc DXF thật, chạy server MCP.
- `kb.search` đổi khóa thông báo từ `notes` sang `messages` (dành `wiki_notes` cho trang ghi chú).
- Phiên bản trước dùng `tcvn_index_v2.db`, `rules.json`, tool `search_tcvn`/`ingest_tcvn`: nay đổi sang `standards_index.db`, `knowledge/rules/*.json`, `search_standards`/`ingest_standards`. Cần nạp lại PDF.
