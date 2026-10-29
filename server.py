"""Standards Checker MCP: kho tiêu chuẩn đa nguồn (PDF + OCR + embedding) + gói luật + đọc bản vẽ DXF."""
import json
from mcp.server.fastmcp import FastMCP
import core
import kb
import notes

mcp = FastMCP("standards-checker")


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


@mcp.tool()
def list_standards() -> str:
    """Liệt kê các tiêu chuẩn đã nạp (theo thư mục con của knowledge/pdf), các gói luật có cấu trúc
    và tiêu chuẩn đang được chọn áp dụng cho dự án. Gọi đầu tiên để biết có những nguồn nào."""
    rules: dict[str, int] = {}
    for r in core.load_rules():
        rules[r["standard"].upper()] = rules.get(r["standard"].upper(), 0) + 1
    return _j({"loaded_documents": kb.list_standards(), "rule_packs": rules,
               "applicable_for_project": core.get_applicable()})


@mcp.tool()
def set_applicable_standards(standards: list[str]) -> str:
    """Chọn các tiêu chuẩn áp dụng cho dự án (ví dụ ["TCVN", "QCVN"]). Truyền danh sách rỗng để bỏ chọn.
    check_drawing sẽ mặc định chỉ chạy các luật thuộc các tiêu chuẩn này."""
    return _j({"applicable_standards": core.set_applicable(standards)})


@mcp.tool()
def ingest_standards(folder: str | None = None, force: bool = False, ocr: str = "auto", embed: bool = True) -> str:
    """Nạp/cập nhật PDF tiêu chuẩn (mặc định thư mục knowledge/pdf; mỗi thư mục con là một tiêu chuẩn, ví dụ pdf/TCVN/).
    Cache theo SHA256: file không đổi nội dung sẽ bỏ qua; OCR và embedding đã tính được dùng lại.
    ocr: 'auto' (OCR trang không có chữ), 'force' (OCR mọi trang), 'off'.
    embed: tạo embedding cho tìm kiếm ngữ nghĩa (lần đầu có thể tải mô hình lớn).
    Nếu có file mới/đổi, tự kiểm tra lại các trang ghi chú (notes_check)."""
    rep = kb.ingest(folder, force, ocr, embed)
    if rep["indexed"]:
        rep["notes_check"] = notes.check_notes()["summary"]
    return _j(rep)


@mcp.tool()
def build_embeddings() -> str:
    """Tạo/làm lại embedding cho toàn bộ kho (sau khi đổi STD_EMBED_MODEL hoặc khi lần ingest trước lỗi)."""
    return _j(kb.build_embeddings())


@mcp.tool()
def search_standards(query: str, limit: int = 5, mode: str = "hybrid", standards: list[str] | None = None,
                     include_notes: bool = True) -> str:
    """Tra cứu điều khoản trong mọi tiêu chuẩn đã nạp (hoặc lọc theo standards), kèm tiêu chuẩn + file + trang,
    các luật có cấu trúc và (include_notes) các đoạn trong trang ghi chú tổng hợp kèm trích dẫn gốc.
    mode: 'hybrid' | 'keyword' | 'semantic'. Gọi TRƯỚC khi đo bản vẽ và luôn dẫn nguồn. Nếu các tiêu chuẩn đưa ra yêu cầu
    khác nhau, nêu rõ từng yêu cầu, không tự gộp. Đoạn gắn '(OCR)' có thể sai chữ, cần đối chiếu bản gốc.
    Trang ghi chú chỉ là lớp tóm tắt: số liệu dùng để kết luận phải lấy từ passages hoặc từ trích dẫn gốc của ghi chú."""
    res = kb.search(query, limit, mode, standards)
    hits = [r for r in core.search(query, limit) if r["source"].startswith("rule:")]
    if standards:
        ids = {r["id"] for r in core.load_rules(standards)}
        hits = [r for r in hits if r["source"][5:] in ids]
    res["rules"] = hits
    if include_notes:
        res["wiki_notes"] = notes.search_notes(query, 3, standards)
    return _j(res)


@mcp.tool()
def kb_status() -> str:
    """Xem kho: từng file (tiêu chuẩn, số trang, số trang OCR), số đoạn, số embedding, mô hình đang dùng."""
    return _j(kb.status())


@mcp.tool()
def list_rules(standards: list[str] | None = None) -> str:
    """Liệt kê luật kiểm tra có cấu trúc (id, tiêu chuẩn, điều khoản, điều kiện, đối tượng cần đo); lọc theo tiêu chuẩn nếu có."""
    return _j(core.load_rules(standards))


@mcp.tool()
def inspect_drawing(dxf_path: str) -> str:
    """Xem tổng quan bản vẽ DXF: phiên bản, đơn vị, loại đối tượng, danh sách layer và block."""
    import dxf_reader
    return _j(dxf_reader.inspect(dxf_path))


@mcp.tool()
def check_drawing(dxf_path: str, standards: list[str] | None = None, rule_ids: list[str] | None = None,
                  unit_to_mm: float = 1.0) -> str:
    """Đo dữ liệu thật trong bản vẽ và so với luật. Mặc định dùng tiêu chuẩn đã chọn bằng set_applicable_standards
    (nếu chưa chọn thì chạy mọi gói luật). unit_to_mm: số mm trên 1 đơn vị bản vẽ (mm = 1, m = 1000).
    Trả về PASS/FAIL/NO_DATA theo từng luật, kèm handle đối tượng vi phạm."""
    import dxf_reader
    used = [s.upper() for s in standards] if standards else core.get_applicable()
    results = []
    for rule in core.load_rules(used):
        if rule_ids and rule["id"] not in rule_ids:
            continue
        results.append(core.evaluate(rule, dxf_reader.measure(dxf_path, rule["target"]), unit_to_mm))
    summary = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "NO_DATA")}
    return _j({"standards_checked": used or "ALL", "summary": summary, "results": results})


@mcp.tool()
def write_note(slug: str, title: str, body: str, citations: list[dict], standards: list[str] | None = None,
               overwrite: bool = False) -> str:
    """Tạo/cập nhật trang ghi chú tổng hợp có dẫn nguồn. Quy tắc: (1) mọi đoạn văn phải kết thúc bằng mã trích dẫn như [c1];
    (2) citations = [{"id": "c1", "source": "TCVN/file.pdf", "page": 12, "quote": "đoạn NGUYÊN VĂN ngắn"}], chép từ kết quả
    search_standards; (3) không viết con số nào mà đoạn trích đã dẫn không chứa. Hệ thống kiểm tra đoạn trích có thật trong
    trang đó; sai thì không lưu. slug: chữ thường không dấu, số, '-' hoặc '_'. Có thể liên kết trang khác bằng [[slug]]."""
    return _j(notes.write_note(slug, title, body, citations, standards, overwrite))


@mcp.tool()
def read_note(slug: str) -> str:
    """Đọc một trang ghi chú (metadata, trạng thái, nội dung, danh sách trích dẫn)."""
    return _j(notes.read_note(slug))


@mcp.tool()
def list_notes(standards: list[str] | None = None) -> str:
    """Liệt kê các trang ghi chú kèm trạng thái: verified | needs_review | stale."""
    return _j(notes.list_notes(standards))


@mcp.tool()
def check_notes(slugs: list[str] | None = None, accept_changed: bool = False) -> str:
    """Kiểm tra lại trích dẫn của các trang ghi chú với kho hiện tại. stale: nguồn mất hoặc đoạn trích không còn khớp
    (phải viết lại); needs_review: nguồn đã đổi nhưng đoạn trích còn khớp. accept_changed=True để xác nhận trường hợp sau,
    chỉ dùng sau khi đã xem lại nội dung nguồn mới."""
    return _j(notes.check_notes(slugs, accept_changed))


if __name__ == "__main__":
    mcp.run()
