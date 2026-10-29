"""Trang ghi chú tổng hợp (kiểu wiki) có dẫn về điều khoản gốc và được kiểm chứng.

Mỗi trang là một file Markdown trong knowledge/wiki/. Mọi khẳng định phải gắn mã trích dẫn [c1], [c2]...; mỗi trích dẫn
gồm file, số trang và đoạn trích NGUYÊN VĂN. Khi lưu, hệ thống kiểm tra đoạn trích có thật sự nằm trong trang đó của
chỉ mục; sai thì không lưu. Số liệu trong đoạn văn mà không xuất hiện trong trích dẫn sẽ bị cảnh báo. Khi tài liệu gốc đổi
(SHA256 khác) hoặc trích dẫn không còn khớp, check_notes đánh dấu trang là needs_review hoặc stale."""
import datetime
import json
import re
from pathlib import Path
from core import norm
import kb

NOTES_DIR = kb.BASE / "wiki"
AUTO_MARK = "<!-- citations:auto -->"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
ID_RE = re.compile(r"^c\d+$")
CITE_RE = re.compile(r"(?<!\[)\[(c\d+)\](?!\()")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _n(s: str) -> str:
    return re.sub(r"\s+", " ", norm(s)).strip()


def _quote_found(quote: str, page_norm: str) -> bool:
    """Đoạn trích (không phân biệt dấu/hoa thường/khoảng trắng) phải nằm trong trang. '...' nối các đoạn theo thứ tự."""
    pos, any_seg = 0, False
    for seg in re.split(r"\.\.\.|\u2026", quote):
        s = _n(seg)
        if not s:
            continue
        any_seg = True
        i = page_norm.find(s, pos)
        if i < 0:
            return False
        pos = i + len(s)
    return any_seg


def _numbers(text: str) -> set[str]:
    text = CITE_RE.sub(" ", re.sub(r"\[\[.*?\]\]", " ", text))
    return {m.replace(",", ".") for m in NUM_RE.findall(text)}


def _path(slug: str) -> Path:
    return NOTES_DIR / f"{slug}.md"


def _render(meta: dict, body: str) -> str:
    fm = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
    refs = "\n".join(f"- [{c['id']}] {c['source']}, trang {c['page']}: \u201c{c['quote']}\u201d" for c in meta["citations"])
    return f"---\n{fm}\n---\n{body.strip()}\n\n{AUTO_MARK}\n## Nguồn trích dẫn\n{refs}\n"


def _parse(text: str) -> tuple[dict, str]:
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        raise ValueError("thiếu frontmatter")
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(": ")
        meta[k] = json.loads(v)
    return meta, m.group(2).split(AUTO_MARK)[0].strip()


def _load(path: Path):
    try:
        return _parse(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_note(slug: str, title: str, body: str, citations: list[dict],
               standards: list[str] | None = None, overwrite: bool = False) -> dict:
    """citations: [{'id': 'c1', 'source': 'TCVN/a.pdf', 'page': 12, 'quote': 'đoạn nguyên văn ngắn'}]. Trong body dẫn bằng [c1]."""
    errors: list[str] = []
    warnings: list[str] = []
    if not SLUG_RE.match(slug or ""):
        errors.append("slug chỉ gồm chữ thường không dấu, số, '-' hoặc '_' (tối đa 80 ký tự).")
    if not (title or "").strip() or not (body or "").strip():
        errors.append("cần có title và body.")
    cites, seen = [], set()
    for ct in citations or []:
        cid, src, quote = str(ct.get("id", "")).strip(), str(ct.get("source", "")).strip(), str(ct.get("quote", "")).strip()
        if not ID_RE.match(cid):
            errors.append(f"id trích dẫn '{cid}' không hợp lệ (dùng c1, c2, ...).")
            continue
        seen.add(cid)
        try:
            page = int(ct.get("page"))
        except (TypeError, ValueError):
            errors.append(f"[{cid}] thiếu hoặc sai 'page'.")
            continue
        if len(quote) > 400 or len(_n(quote)) < 8:
            errors.append(f"[{cid}] đoạn trích phải dài từ 8 đến 400 ký tự.")
            continue
        info = kb.file_info(src)
        if not info:
            errors.append(f"[{cid}] không có file '{src}' trong kho (dùng kb_status để lấy đúng tên).")
            continue
        pt = kb.page_text(src, page)
        if not pt:
            errors.append(f"[{cid}] trang {page} của '{src}' không có chữ trong chỉ mục.")
            continue
        if not _quote_found(quote, _n(pt[0])):
            errors.append(f"[{cid}] không tìm thấy đoạn trích trong {src} trang {page}. Hãy chép nguyên văn từ kết quả "
                          "search_standards (dùng '...' để nối các đoạn).")
            continue
        if pt[1]:
            warnings.append(f"[{cid}] trích từ OCR, cần đối chiếu bản gốc.")
        cites.append({"id": cid, "source": src, "page": page, "standard": info["standard"],
                      "sha256": info["sha256"], "quote": quote, "ocr": bool(pt[1])})
    if not citations:
        errors.append("cần ít nhất một trích dẫn.")
    used = set(CITE_RE.findall(body or ""))
    for cid in sorted(used - seen):
        errors.append(f"body dẫn [{cid}] nhưng chưa khai báo trong citations.")
    for cid in sorted({c["id"] for c in cites} - used):
        warnings.append(f"[{cid}] được khai báo nhưng không được dùng trong body.")
    by_id = {c["id"]: c for c in cites}
    for k, para in enumerate(re.split(r"\n\s*\n", (body or "").strip()), start=1):
        para = para.strip()
        if not para or para.startswith("#"):
            continue
        ids = CITE_RE.findall(para)
        if not ids:
            warnings.append(f"Đoạn {k} không có dẫn nguồn: \u201c{para[:60]}\u2026\u201d")
            continue
        clean = re.sub(r"(?m)^\s*(?:[-*]|\d+[.)])\s+", "", para)
        quoted = set().union(*[_numbers(by_id[i]["quote"]) for i in ids if i in by_id])
        miss = sorted(_numbers(clean) - quoted)
        if miss:
            warnings.append(f"Đoạn {k}: số {miss} không có trong trích dẫn đã dẫn; kiểm tra đơn vị hoặc quy đổi.")
    if _path(slug).exists() and not overwrite and SLUG_RE.match(slug or ""):
        errors.append(f"trang '{slug}' đã tồn tại; đặt overwrite=true để ghi đè.")
    if errors:
        return {"saved": False, "errors": errors, "warnings": warnings}
    created = today = datetime.date.today().isoformat()
    old = _load(_path(slug)) if _path(slug).exists() else None
    if old:
        created = old[0].get("created", today)
    meta = {"title": title.strip(), "standards": sorted({c["standard"] for c in cites} | {s.upper() for s in standards or []}),
            "status": "verified" if not warnings else "needs_review", "created": created, "updated": today,
            "warnings": warnings, "citations": cites}
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    _path(slug).write_text(_render(meta, body), encoding="utf-8")
    return {"saved": True, "path": str(_path(slug)), "status": meta["status"], "warnings": warnings}


def read_note(slug: str) -> dict:
    if not SLUG_RE.match(slug or "") or not _path(slug).exists():
        return {"error": f"không có trang '{slug}'."}
    loaded = _load(_path(slug))
    return {"meta": loaded[0], "body": loaded[1]} if loaded else {"error": "file hỏng hoặc sai định dạng."}


def list_notes(standards: list[str] | None = None) -> list[dict]:
    wanted = {s.upper() for s in standards} if standards else None
    out = []
    for path in sorted(NOTES_DIR.glob("*.md")) if NOTES_DIR.exists() else []:
        loaded = _load(path)
        if loaded and (not wanted or wanted & set(loaded[0]["standards"])):
            m = loaded[0]
            out.append({"slug": path.stem, "title": m["title"], "status": m["status"],
                        "standards": m["standards"], "updated": m["updated"]})
    return out


def check_notes(slugs: list[str] | None = None, accept_changed: bool = False) -> dict:
    """Kiểm tra lại mọi trích dẫn với chỉ mục hiện tại.
    stale: file nguồn mất hoặc đoạn trích không còn khớp. needs_review: nguồn đã đổi (SHA256 khác) nhưng đoạn trích còn khớp,
    hoặc trang có cảnh báo từ lúc lưu. accept_changed=True xác nhận các nguồn đã đổi mà trích dẫn còn khớp."""
    results, summary = [], {"verified": 0, "needs_review": 0, "stale": 0}
    for path in sorted(NOTES_DIR.glob("*.md")) if NOTES_DIR.exists() else []:
        if slugs and path.stem not in slugs:
            continue
        loaded = _load(path)
        if not loaded:
            continue
        meta, body = loaded
        problems, changed = [], False
        for ct in meta["citations"]:
            info = kb.file_info(ct["source"])
            if not info:
                problems.append({"id": ct["id"], "source": ct["source"], "issue": "source_missing"})
                continue
            pt = kb.page_text(ct["source"], ct["page"])
            if not pt or not _quote_found(ct["quote"], _n(pt[0])):
                problems.append({"id": ct["id"], "source": ct["source"], "page": ct["page"], "issue": "quote_not_found"})
            elif info["sha256"] != ct.get("sha256"):
                if accept_changed:
                    ct["sha256"], changed = info["sha256"], True
                else:
                    problems.append({"id": ct["id"], "source": ct["source"], "issue": "source_changed"})
        stale = any(p["issue"] != "source_changed" for p in problems)
        status = "stale" if stale else ("needs_review" if problems or meta.get("warnings") else "verified")
        if status != meta["status"] or changed:
            meta["status"] = status
            path.write_text(_render(meta, body), encoding="utf-8")
        summary[status] += 1
        results.append({"slug": path.stem, "status": status, "problems": problems})
    return {"summary": summary, "results": results}


def search_notes(query: str, limit: int = 3, standards: list[str] | None = None) -> list[dict]:
    """Tìm theo từ khóa trong các trang ghi chú; trả về đoạn văn kèm trích dẫn gốc (file, trang, nguyên văn)."""
    words = [w for w in re.findall(r"\w+", norm(query)) if len(w) > 1]
    wanted = {s.upper() for s in standards} if standards else None
    out = []
    for path in sorted(NOTES_DIR.glob("*.md")) if NOTES_DIR.exists() and words else []:
        loaded = _load(path)
        if not loaded or (wanted and not wanted & set(loaded[0]["standards"])):
            continue
        meta, body = loaded
        by_id = {c["id"]: c for c in meta["citations"]}
        for para in re.split(r"\n\s*\n", body):
            score = sum(norm(para).count(w) for w in words)
            if score:
                out.append({"score": score, "slug": path.stem, "title": meta["title"], "status": meta["status"],
                            "standards": meta["standards"], "text": para.strip(),
                            "citations": [{k: by_id[i][k] for k in ("id", "source", "page", "quote")}
                                          for i in CITE_RE.findall(para) if i in by_id]})
    return sorted(out, key=lambda x: -x["score"])[:limit]
