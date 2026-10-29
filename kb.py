"""Kho tri thức tiêu chuẩn (TCVN, QCVN, NFPA, ASHRAE, JIS, ISO...): PDF -> (OCR nếu là ảnh scan) -> SQLite FTS5 + embedding.
Tên tiêu chuẩn lấy từ thư mục con: knowledge/pdf/<TIÊU_CHUẨN>/file.pdf (file nằm ngay trong pdf/ thuộc nhóm GENERAL).
Tìm kiếm lai: BM25 (từ khóa, không phân biệt dấu) + ngữ nghĩa (embedding), hợp nhất bằng Reciprocal Rank Fusion."""
import hashlib
import os
import re
import sqlite3
from pathlib import Path
from core import norm

BASE = Path(__file__).parent / "knowledge"
DB_PATH = BASE / "standards_index.db"
PDF_DIR = BASE / "pdf"
MAX_CHUNK = 700  # ký tự mỗi đoạn
EMBED_MODEL = os.environ.get("STD_EMBED_MODEL") or os.environ.get("TCVN_EMBED_MODEL") or "BAAI/bge-m3"
OCR_LANG = os.environ.get("STD_OCR_LANG") or os.environ.get("TCVN_OCR_LANG") or "vie+eng"
OCR_DPI = 300
RRF_K = 60
BAD_CHARS = re.compile("[\ufffd■□]")
SYMBOLS = re.compile("[\u00a1-\u00af\u00b4\u00b6\u00b8-\u00bb\u00bf]")  # bỏ qua ° ± ² ³ µ · ½ × ÷ thường gặp trong tiêu chuẩn kỹ thuật  # ký tự Latin-1 lạ: dấu hiệu font tiếng Việt cũ bị đọc sai
_model = None
_cache: dict = {}


# ---------- cơ sở dữ liệu ----------
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
              "norm_text, text UNINDEXED, source UNINDEXED, page UNINDEXED, ocr UNINDEXED, standard UNINDEXED, "
              "tokenize='unicode61')")
    c.execute("CREATE TABLE IF NOT EXISTS vecs(id INTEGER PRIMARY KEY, vec BLOB NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS files(source TEXT PRIMARY KEY, standard TEXT, size INTEGER, mtime REAL, "
              "pages INTEGER, empty_pages INTEGER, ocr_pages INTEGER, sha256 TEXT)")
    if "sha256" not in [r[1] for r in c.execute("PRAGMA table_info(files)")]:  # nâng cấp CSDL cũ
        c.execute("ALTER TABLE files ADD COLUMN sha256 TEXT")
    c.execute("CREATE TABLE IF NOT EXISTS ocr_cache(sha TEXT, page INTEGER, lang TEXT, text TEXT, PRIMARY KEY(sha, page, lang))")
    c.execute("CREATE TABLE IF NOT EXISTS vec_cache(h TEXT, model TEXT, vec BLOB NOT NULL, PRIMARY KEY(h, model))")
    c.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    return c


def sha256_file(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(bufsize):
            h.update(chunk)
    return h.hexdigest()


def file_info(source: str) -> dict | None:
    """Thông tin file đã nạp (tiêu chuẩn, sha256) hoặc None nếu chưa có."""
    if not DB_PATH.exists():
        return None
    with _conn() as c:
        row = c.execute("SELECT standard, sha256 FROM files WHERE source=?", (source,)).fetchone()
    return {"standard": row[0], "sha256": row[1]} if row else None


def page_text(source: str, page: int) -> tuple[str, bool] | None:
    """Toàn văn một trang theo chỉ mục (ghép các đoạn) và cờ OCR; None nếu trang không có chữ."""
    if not DB_PATH.exists():
        return None
    with _conn() as c:
        rows = c.execute("SELECT text, ocr FROM chunks WHERE source=? AND page=? ORDER BY rowid", (source, page)).fetchall()
    return (" ".join(r[0] for r in rows), any(r[1] for r in rows)) if rows else None


def split_page(text: str) -> list[str]:
    """Gom các dòng thành đoạn ~MAX_CHUNK ký tự, ngắt ở ranh giới dòng."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    out, cur = [], ""
    for l in filter(None, lines):
        if cur and len(cur) + len(l) + 1 > MAX_CHUNK:
            out.append(cur)
            cur = ""
        cur = f"{cur} {l}".strip()
    if cur:
        out.append(cur)
    return out


def encoding_warning(name: str, text: str) -> str | None:
    """Phát hiện chữ trích xuất bị lỗi mã hóa (font cũ/thiếu ánh xạ Unicode). Không phụ thuộc ngôn ngữ."""
    chars = [ch for ch in text if not ch.isspace()]
    if len(chars) < 500:
        return None
    bad = len(BAD_CHARS.findall(text)) / len(chars)
    sym = len(SYMBOLS.findall(text)) / len(chars)
    if bad > 0.01 or sym > 0.05:
        return (f"{name}: chữ trích xuất có vẻ bị lỗi mã hóa (ký tự lạ {bad:.1%}, ký hiệu Latin-1 {sym:.1%}); "
                "kết quả tìm kiếm có thể thiếu chính xác. Thử ingest với ocr='force'.")
    return None


# ---------- embedding ----------
def embeddings_available() -> bool:
    try:
        import numpy  # noqa: F401
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _embed(texts: list[str], is_query: bool = False):
    """Trả về ma trận float32 đã chuẩn hóa (cosine = tích vô hướng)."""
    global _model
    import numpy as np
    from sentence_transformers import SentenceTransformer
    if _model is None:
        _model = SentenceTransformer(EMBED_MODEL)
    if "e5" in EMBED_MODEL.lower():  # họ E5 cần tiền tố
        texts = [("query: " if is_query else "passage: ") + t for t in texts]
    v = _model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(v, dtype="float32")


def embed_pending(c: sqlite3.Connection) -> tuple[int, int]:
    """Tạo embedding cho các đoạn chưa có; dùng lại vector đã lưu nếu cùng nội dung đoạn (sha256) và cùng mô hình.
    Trả về (số đoạn phải tính mới, số đoạn lấy từ cache). Đổi mô hình thì vector cũ bị bỏ (cache theo mô hình vẫn giữ)."""
    row = c.execute("SELECT v FROM meta WHERE k='embed_model'").fetchone()
    if row and row[0] != EMBED_MODEL:
        c.execute("DELETE FROM vecs")
    rows = c.execute("SELECT rowid, text FROM chunks WHERE rowid NOT IN (SELECT id FROM vecs)").fetchall()
    need, hits = [], 0
    for rid, text in rows:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        hit = c.execute("SELECT vec FROM vec_cache WHERE h=? AND model=?", (h, EMBED_MODEL)).fetchone()
        if hit:
            c.execute("INSERT OR REPLACE INTO vecs VALUES (?,?)", (rid, hit[0]))
            hits += 1
        else:
            need.append((rid, text, h))
    for i in range(0, len(need), 64):
        batch = need[i:i + 64]
        vecs = _embed([t for _, t, _ in batch])
        for (rid, _, h), v in zip(batch, vecs):
            c.execute("INSERT OR REPLACE INTO vecs VALUES (?,?)", (rid, v.tobytes()))
            c.execute("INSERT OR REPLACE INTO vec_cache VALUES (?,?,?)", (h, EMBED_MODEL, v.tobytes()))
    c.execute("INSERT OR REPLACE INTO meta VALUES ('embed_model', ?)", (EMBED_MODEL,))
    _cache.clear()
    return len(need), hits


def build_embeddings() -> dict:
    if not embeddings_available():
        return {"error": "Chưa cài sentence-transformers (pip install -r requirements-extra.txt)."}
    with _conn() as c:
        new, hits = embed_pending(c)
        return {"model": EMBED_MODEL, "embedded_chunks": new, "embed_cache_hits": hits}


# ---------- OCR ----------
def _ocr_setup() -> tuple[str | None, list[str]]:
    """Trả về (chuỗi ngôn ngữ dùng được hoặc None nếu không OCR được, danh sách cảnh báo)."""
    try:
        import pytesseract
        import pypdfium2  # noqa: F401
        pytesseract.get_tesseract_version()
        have = pytesseract.get_languages()
    except Exception as e:
        return None, [f"Không dùng được OCR ({type(e).__name__}): cần cài Tesseract, pytesseract, pypdfium2."]
    want = OCR_LANG.split("+")
    ok = [l for l in want if l in have]
    if not ok:
        return None, [f"Tesseract không có ngôn ngữ nào trong '{OCR_LANG}'."]
    warns = []
    if len(ok) < len(want):
        warns.append(f"Tesseract thiếu dữ liệu ngôn ngữ {sorted(set(want) - set(ok))}; OCR sẽ kém chính xác với phần chữ đó. "
                     "Cài thêm gói ngôn ngữ (Linux: tesseract-ocr-<mã>, ví dụ vie, jpn; Windows: chọn khi cài).")
    return "+".join(ok), warns


def _ocr_page(pdfdoc, index: int, lang: str) -> str:
    import pytesseract
    return pytesseract.image_to_string(pdfdoc[index].render(scale=OCR_DPI / 72).to_pil(), lang=lang)


# ---------- nạp tài liệu ----------
def _standard_of(pdf: Path, root: Path) -> tuple[str, str]:
    rel = pdf.relative_to(root)
    return (rel.parts[0].upper() if len(rel.parts) > 1 else "GENERAL"), rel.as_posix()


def ingest(folder: str | None = None, force: bool = False, ocr: str = "auto", embed: bool = True) -> dict:
    """ocr: 'auto' (chỉ OCR trang không có chữ), 'force' (OCR mọi trang), 'off'.
    Cache theo SHA256: file không đổi nội dung bị bỏ qua dù đổi ngày giờ hoặc tên thư mục cha; kết quả OCR và
    embedding được lưu theo nội dung nên nạp lại (force) không phải tính lại."""
    from pypdf import PdfReader
    root = Path(folder) if folder else PDF_DIR
    report = {"folder": str(root), "indexed": [], "skipped": [], "touched_only": [], "warnings": [],
              "ocr_run": 0, "ocr_cache_hits": 0}
    lang = None
    if ocr != "off":
        lang, warns = _ocr_setup()
        report["warnings"] += warns
    with _conn() as c:
        for pdf in sorted(root.rglob("*.pdf")):
            std, source = _standard_of(pdf, root)
            st = pdf.stat()
            row = c.execute("SELECT size, mtime, sha256 FROM files WHERE source=?", (source,)).fetchone()
            sha = None
            if row and not force:
                same_stat = row[0] == st.st_size and row[1] == st.st_mtime
                if same_stat and row[2]:  # đường nhanh: không cần băm lại
                    report["skipped"].append(source)
                    continue
                sha = sha256_file(pdf)
                if row[2] is None and same_stat:  # CSDL cũ chưa có sha256
                    c.execute("UPDATE files SET sha256=? WHERE source=?", (sha, source))
                    report["skipped"].append(source)
                    continue
                if row[2] == sha:  # chỉ đổi ngày giờ/kích thước metadata, nội dung y nguyên
                    c.execute("UPDATE files SET size=?, mtime=? WHERE source=?", (st.st_size, st.st_mtime, source))
                    report["skipped"].append(source)
                    report["touched_only"].append(source)
                    continue
            sha = sha or sha256_file(pdf)
            twin = c.execute("SELECT source FROM files WHERE sha256=? AND source<>?", (sha, source)).fetchone()
            if twin:
                report["warnings"].append(f"{source}: nội dung trùng với {twin[0]} (vẫn được đánh chỉ mục).")
            c.execute("DELETE FROM vecs WHERE id IN (SELECT rowid FROM chunks WHERE source=?)", (source,))
            c.execute("DELETE FROM chunks WHERE source=?", (source,))
            reader = PdfReader(str(pdf))
            empty = n_chunks = n_ocr = 0
            native_text, pdfdoc = [], None
            for i, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                is_ocr = False
                if lang and (ocr == "force" or (ocr == "auto" and len(text) < 20)):
                    cached = c.execute("SELECT text FROM ocr_cache WHERE sha=? AND page=? AND lang=?",
                                       (sha, i, lang)).fetchone()
                    if cached:
                        text, is_ocr = cached[0], True
                        n_ocr += 1
                        report["ocr_cache_hits"] += 1
                    else:
                        try:
                            if pdfdoc is None:
                                import pypdfium2
                                pdfdoc = pypdfium2.PdfDocument(str(pdf))
                            text = _ocr_page(pdfdoc, i - 1, lang).strip()
                            c.execute("INSERT OR REPLACE INTO ocr_cache VALUES (?,?,?,?)", (sha, i, lang, text))
                            is_ocr = True
                            n_ocr += 1
                            report["ocr_run"] += 1
                        except Exception as e:
                            report["warnings"].append(f"{source} trang {i}: OCR lỗi ({type(e).__name__}: {e}).")
                if len(text) < 20:
                    empty += 1
                    continue
                if not is_ocr:
                    native_text.append(text)
                for ch in split_page(text):
                    c.execute("INSERT INTO chunks(norm_text, text, source, page, ocr, standard) VALUES (?,?,?,?,?,?)",
                              (norm(ch), ch, source, i, int(is_ocr), std))
                    n_chunks += 1
            c.execute("INSERT OR REPLACE INTO files(source, standard, size, mtime, pages, empty_pages, ocr_pages, sha256) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (source, std, st.st_size, st.st_mtime, len(reader.pages), empty, n_ocr, sha))
            report["indexed"].append({"file": source, "standard": std, "pages": len(reader.pages),
                                      "chunks": n_chunks, "ocr_pages": n_ocr})
            warn = encoding_warning(source, " ".join(native_text))
            if warn:
                report["warnings"].append(warn)
            if empty:
                report["warnings"].append(f"{source}: {empty}/{len(reader.pages)} trang không đọc được chữ"
                                          + ("." if lang else " (cần bật OCR)."))
        if embed:
            if embeddings_available():
                try:
                    new, hits = embed_pending(c)
                    report.update(embedded_chunks=new, embed_cache_hits=hits, embed_model=EMBED_MODEL)
                except Exception as e:  # ví dụ: chưa tải được mô hình do không có mạng
                    report["warnings"].append(f"Tạo embedding lỗi ({type(e).__name__}: {e}); vẫn tìm được theo từ khóa.")
            else:
                report["warnings"].append("Chưa cài sentence-transformers nên chưa có tìm kiếm ngữ nghĩa "
                                          "(pip install -r requirements-extra.txt).")
    return report


# ---------- tìm kiếm ----------
def _fts(c, query: str, n: int, stds: list[str] | None) -> list[int]:
    words = [w for w in re.findall(r"\w+", norm(query)) if len(w) > 1]
    if not words:
        return []
    sql, args = "SELECT rowid FROM chunks WHERE chunks MATCH ?", [" OR ".join(f'"{w}"' for w in words)]
    if stds:
        sql += f" AND standard IN ({','.join('?' * len(stds))})"
        args += stds
    return [r[0] for r in c.execute(sql + " ORDER BY bm25(chunks) LIMIT ?", args + [n])]


def _vec_search(c, query: str, n: int, stds: list[str] | None) -> list[int]:
    import numpy as np
    meta = c.execute("SELECT v FROM meta WHERE k='embed_model'").fetchone()
    if not meta:
        raise RuntimeError("chưa có embedding, hãy chạy build_embeddings hoặc ingest_standards")
    if meta[0] != EMBED_MODEL:
        raise RuntimeError(f"embedding đang lưu bằng mô hình '{meta[0]}', khác '{EMBED_MODEL}'; hãy chạy build_embeddings")
    key = DB_PATH.stat().st_mtime_ns
    if _cache.get("key") != key:
        rows = c.execute("SELECT v.id, v.vec, k.standard FROM vecs v JOIN chunks k ON k.rowid = v.id").fetchall()
        _cache.update(key=key, ids=[r[0] for r in rows], stds=[r[2] for r in rows],
                      mat=np.vstack([np.frombuffer(r[1], dtype="float32") for r in rows]) if rows else None)
    if _cache["mat"] is None:
        raise RuntimeError("kho chưa có vector nào")
    sims = _cache["mat"] @ _embed([query], is_query=True)[0]
    if stds:
        sims = np.where(np.array([s in stds for s in _cache["stds"]]), sims, -np.inf)
    return [_cache["ids"][i] for i in np.argsort(-sims)[:n] if np.isfinite(sims[i])]


def search(query: str, limit: int = 5, mode: str = "hybrid", standards: list[str] | None = None) -> dict:
    """mode: 'hybrid' | 'keyword' | 'semantic'. standards: lọc theo tên tiêu chuẩn (mặc định tìm tất cả)."""
    messages: list[str] = []
    if not DB_PATH.exists():
        return {"passages": [], "messages": ["Chưa nạp tài liệu nào, hãy chạy ingest_standards."]}
    stds = [s.upper() for s in standards] if standards else None
    n = max(limit * 4, 20)
    with _conn() as c:
        kw = _fts(c, query, n, stds) if mode in ("hybrid", "keyword") else []
        sem: list[int] = []
        if mode in ("hybrid", "semantic"):
            if not embeddings_available():
                messages.append("Chưa cài sentence-transformers: chỉ tìm theo từ khóa.")
            else:
                try:
                    sem = _vec_search(c, query, n, stds)
                except Exception as e:
                    messages.append(f"Tìm ngữ nghĩa không dùng được ({e}); chỉ dùng kết quả từ khóa.")
        scores: dict[int, float] = {}
        for ranked in (kw, sem):
            for rank, rid in enumerate(ranked):
                scores[rid] = scores.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
        passages = []
        for rid in sorted(scores, key=lambda r: -scores[r])[:limit]:
            t, src, pg, is_ocr, std = c.execute(
                "SELECT text, source, page, ocr, standard FROM chunks WHERE rowid=?", (rid,)).fetchone()
            passages.append({"standard": std, "source": f"{src}, trang {pg}" + (" (OCR, cần đối chiếu bản gốc)" if is_ocr else ""),
                             "text": t})
    return {"passages": passages, "messages": messages}


def list_standards() -> list[dict]:
    if not DB_PATH.exists():
        return []
    with _conn() as c:
        return [dict(zip(("standard", "files", "pages", "ocr_pages"), r)) for r in c.execute(
            "SELECT standard, count(*), sum(pages), sum(ocr_pages) FROM files GROUP BY standard ORDER BY standard")]


def status() -> dict:
    with _conn() as c:
        files = [dict(zip(("file", "standard", "pages", "empty_pages", "ocr_pages", "sha256"), r)) for r in c.execute(
            "SELECT source, standard, pages, empty_pages, ocr_pages, substr(sha256,1,12) FROM files ORDER BY source")]
        meta = c.execute("SELECT v FROM meta WHERE k='embed_model'").fetchone()
        n = lambda t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: E731
        return {"files": files, "chunks": n("chunks"), "embedded_chunks": n("vecs"),
                "cache": {"ocr_pages": n("ocr_cache"), "embeddings": n("vec_cache")},
                "stored_embed_model": meta[0] if meta else None, "configured_embed_model": EMBED_MODEL}
