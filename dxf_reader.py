"""Đọc bản vẽ DXF bằng ezdxf. (AutoCAD: lưu/xuất ra .dxf; DWG cần convert bằng ODA File Converter)."""
from fnmatch import fnmatch
import ezdxf
from ezdxf import bbox


def open_dxf(path: str):
    return ezdxf.readfile(path)


def inspect(path: str) -> dict:
    doc = open_dxf(path)
    kinds, layers, blocks = {}, {}, {}
    for e in doc.modelspace():
        kinds[e.dxftype()] = kinds.get(e.dxftype(), 0) + 1
        layers[e.dxf.layer] = layers.get(e.dxf.layer, 0) + 1
        if e.dxftype() == "INSERT":
            blocks[e.dxf.name] = blocks.get(e.dxf.name, 0) + 1
    return {"dxf_version": doc.dxfversion, "insunits": doc.header.get("$INSUNITS"),
            "entity_types": kinds, "layers": layers, "blocks": blocks}


def _length(e) -> float:
    if e.dxftype() == "LINE":
        return e.dxf.start.distance(e.dxf.end)
    pts = [(p[0], p[1]) for p in e.get_points()]
    if e.closed:
        pts.append(pts[0])
    return sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in zip(pts, pts[1:]))


def _area(e) -> float:
    """Diện tích polyline kín theo công thức Shoelace (bỏ qua cung tròn/bulge)."""
    pts = [(p[0], p[1]) for p in e.get_points()]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def measure(path: str, target: dict) -> list[dict]:
    """Các kiểu đo: dimension, line_length, block_width, area (polyline kín), block_count (chỉ trong modelspace)."""
    msp = open_dxf(path).modelspace()
    ttype, pattern = target["type"], target.get("layer", "*").lower()
    if ttype == "block_count":
        bpat = target.get("block", "*").lower()
        n = sum(1 for e in msp if e.dxftype() == "INSERT" and fnmatch(e.dxf.layer.lower(), pattern)
                and fnmatch(e.dxf.name.lower(), bpat))
        return [{"handle": "-", "layer": target.get("layer", "*"), "value": float(n)}]
    res = []
    for e in msp:
        if not fnmatch(e.dxf.layer.lower(), pattern):
            continue
        t, val = e.dxftype(), None
        if ttype == "dimension" and t == "DIMENSION":
            try:
                val = e.get_measurement()
                val = val if isinstance(val, (int, float)) else None
            except Exception:
                val = e.dxf.get("actual_measurement", None)
        elif ttype == "line_length" and t in ("LINE", "LWPOLYLINE"):
            val = _length(e)
        elif ttype == "area" and t == "LWPOLYLINE" and e.closed:
            val = _area(e)
        elif ttype == "block_width" and t == "INSERT":
            ext = bbox.extents([e])
            val = ext.size.x if ext.has_data else None
        if val is not None:
            res.append({"handle": e.dxf.handle, "layer": e.dxf.layer, "value": float(val)})
    return res
