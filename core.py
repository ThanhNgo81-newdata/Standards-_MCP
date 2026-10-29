"""Logic thuần Python (không cần thư viện ngoài): gói luật theo tiêu chuẩn, so sánh, tìm kiếm ghi chú."""
import json
import re
import unicodedata
from pathlib import Path

KB_DIR = Path(__file__).parent / "knowledge"
RULES_DIR = KB_DIR / "rules"
PROJECT_FILE = KB_DIR / "project.json"
OPS = {
    ">=": lambda a, b: a >= b, ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
    "==": lambda a, b: abs(a - b) < 1e-6,
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# ---------- tiêu chuẩn áp dụng cho dự án ----------
def get_applicable() -> list[str] | None:
    if not PROJECT_FILE.exists():
        return None
    stds = json.loads(PROJECT_FILE.read_text(encoding="utf-8")).get("applicable_standards") or []
    return [s.upper() for s in stds] or None


def set_applicable(standards: list[str]) -> list[str] | None:
    stds = sorted({s.strip().upper() for s in standards if s.strip()})
    PROJECT_FILE.write_text(json.dumps({"applicable_standards": stds}, ensure_ascii=False, indent=2), encoding="utf-8")
    return stds or None


# ---------- gói luật ----------
def load_rules(standards: list[str] | None = None) -> list[dict]:
    """Đọc mọi knowledge/rules/*.json (bỏ file bắt đầu bằng '_' và luật enabled=false); lọc theo tiêu chuẩn nếu có."""
    wanted = {s.upper() for s in standards} if standards else None
    rules, seen = [], set()
    for f in sorted(RULES_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        for r in json.loads(f.read_text(encoding="utf-8")):
            if r.get("enabled", True) is False:
                continue
            if r["id"] in seen:
                raise ValueError(f"Trùng id luật '{r['id']}' (file {f.name}).")
            seen.add(r["id"])
            if wanted is None or r["standard"].upper() in wanted:
                rules.append(r)
    return rules


def search(query: str, limit: int = 5) -> list[dict]:
    """Tìm theo từ khóa trong luật có cấu trúc và ghi chú .md."""
    words = [w for w in re.findall(r"\w+", norm(query)) if len(w) > 1]
    chunks = [(f"rule:{r['id']}", f"{r['standard']} | {r['ref']} | {r['title']} | {r['description']}") for r in load_rules()]
    for f in sorted(KB_DIR.glob("*.md")):
        chunks += [(f"file:{f.name}", p.strip()) for p in re.split(r"\n\s*\n", f.read_text(encoding="utf-8")) if p.strip()]
    out = []
    for src, text in chunks:
        score = sum(norm(text).count(w) for w in words)
        if score:
            out.append({"source": src, "score": score, "text": text})
    return sorted(out, key=lambda x: -x["score"])[:limit]


# ---------- so sánh ----------
def unit_factor(rule: dict, scale: float) -> float:
    """Đổi giá trị đo (đơn vị bản vẽ) sang đơn vị của luật. scale = số mm trên 1 đơn vị bản vẽ."""
    u = rule.get("unit", "mm")
    if u == "m2":
        return scale ** 2 / 1e6
    if u == "m":
        return scale / 1000
    if u == "count":
        return 1.0
    return scale  # mm


def evaluate(rule: dict, measured: list[dict], scale: float = 1.0) -> dict:
    """measured: [{'handle','layer','value'}]. Trả về PASS/FAIL/NO_DATA."""
    base = {"rule": rule["id"], "standard": rule["standard"], "ref": rule["ref"], "title": rule["title"]}
    if not measured:
        return {**base, "status": "NO_DATA", "violations": [],
                "note": "Không tìm thấy đối tượng nào khớp (kiểm tra tên layer/loại đối tượng)."}
    op, limit, unit, k = OPS[rule["op"]], rule["value"], rule.get("unit", "mm"), unit_factor(rule, scale)
    bad = []
    for m in measured:
        v = m["value"] * k
        if not op(v, limit):
            bad.append({**m, "measured": round(v, 3), "required": f"{rule['op']} {limit} {unit}"})
    return {**base, "status": "FAIL" if bad else "PASS", "checked": len(measured), "violations": bad}
