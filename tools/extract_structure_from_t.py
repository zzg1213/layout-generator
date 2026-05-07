import itertools
import hashlib
import json
import math
import re
import sys
import statistics
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parent
SDATA_PATH = ROOT / "SDATA.SAT"
WMASS_PATH = ROOT / "WMASS.OUT"

TEXT_HEADER_SECOND_DWORD = b"\x08\x00\x34\xff"
MASSLINE_HEADER_SECOND_DWORD = b"\x02\x004\xff"
STANDARD_DIAMETERS = ("32", "28", "25", "22", "20", "18", "16", "14", "12", "10", "8", "6")
STANDARD_SPACINGS = ("300", "250", "220", "200", "150", "125", "120", "110", "100", "90", "80", "70", "60", "50")
STANDARD_SECTION_DIMS = ("1200", "1000", "900", "850", "800", "750", "700", "650", "600", "550", "500", "450", "400", "350", "300", "250", "220", "200", "180", "160", "150", "120", "100")
FORBIDDEN_FIELDS = {
    "PGA_rare_max",
    "Gc_kN_m3",
    "Gs_kN_m3",
    "Gb_kN_m3",
    "Asc",
    "Asxt",
    "Asyt",
    "Asxb",
    "Asyb",
    "Asvx",
    "Asvy",
    "As_top",
    "As_bot",
    "As_ctl",
    "Asv_sum",
    "positions",
    "top_Ast_9pts",
    "btm_Ast_9pts",
    "Asv_9pts",
    "Astt",
    "main_rebar_text",
    "other_rebar_text",
    "inplace_texts",
}

CONCRETE_GRADE_BY_STRENGTH = {
    20: "C20",
    25: "C25",
    30: "C30",
    35: "C35",
    40: "C40",
    45: "C45",
    50: "C50",
    55: "C55",
    60: "C60",
}

CONCRETE_PROPERTIES = {
    "C20": {"E_c": 25500.0, "fc": 9.6, "fck": 13.4, "ft": 1.10, "ftk": 1.54},
    "C25": {"E_c": 28000.0, "fc": 11.9, "fck": 16.7, "ft": 1.27, "ftk": 1.78},
    "C30": {"E_c": 30000.0, "fc": 14.3, "fck": 20.1, "ft": 1.43, "ftk": 2.01},
    "C35": {"E_c": 31500.0, "fc": 16.7, "fck": 23.4, "ft": 1.57, "ftk": 2.20},
    "C40": {"E_c": 32500.0, "fc": 19.1, "fck": 26.8, "ft": 1.71, "ftk": 2.39},
    "C45": {"E_c": 33500.0, "fc": 21.1, "fck": 29.6, "ft": 1.80, "ftk": 2.51},
    "C50": {"E_c": 34500.0, "fc": 23.1, "fck": 32.4, "ft": 1.89, "ftk": 2.64},
    "C55": {"E_c": 35500.0, "fc": 25.3, "fck": 35.5, "ft": 1.96, "ftk": 2.74},
    "C60": {"E_c": 36000.0, "fc": 27.5, "fck": 38.5, "ft": 2.04, "ftk": 2.85},
}

STEEL_GRADE_BY_FY = {
    270: "HPB300",
    300: "HRB335",
    360: "HRB400",
    435: "HRB500",
}

STEEL_ELASTIC_MODULUS = {
    "HPB300": 200000.0,
    "HRB335": 200000.0,
    "HRB400": 200000.0,
    "HRB500": 200000.0,
}


@dataclass
class TextRecord:
    marker: str
    x: float
    y: float
    raw: bytes
    angle_deg: float


@dataclass
class BeamConnectivity:
    serial: int
    global_node_i: int
    global_node_j: int
    code: int


@dataclass
class ColumnPoint:
    x_mm: int
    y_mm: int
    z_top_mm: int
    story_index: int
    story_code_ids: Tuple[int, int, int]


@dataclass
class BeamCandidate:
    serial: int
    local_node_i: int
    local_node_j: int
    x1_mm: int
    y1_mm: int
    x2_mm: int
    y2_mm: int


@dataclass
class BeamStrip:
    beam_id: str
    count: int
    orientation: str
    strip_coord_mm: int
    section_b_mm: int
    section_h_mm: int
    top_middle_text: str
    bottom_text: str
    waist_text: str
    stirrup_text: str


@dataclass
class BeamRun:
    orientation: str
    strip_coord_mm: int
    start_coord_mm: int
    end_coord_mm: int
    center_x_mm: float
    center_y_mm: float
    beam_keys: Tuple[int, ...]


def configure_project(project_root: Path) -> None:
    global ROOT, SDATA_PATH, WMASS_PATH
    ROOT = project_root.resolve()
    SDATA_PATH = ROOT / "SDATA.SAT"
    WMASS_PATH = ROOT / "WMASS.OUT"


def workspace_root_for_project(project_root: Path) -> Path:
    project_root = project_root.resolve()
    return project_root.parent if project_root.name.endswith("_extracted") else project_root


def project_output_seed(project_root: Path, used_seeds: Optional[set] = None) -> str:
    digest = hashlib.sha256()
    digest.update(project_root.name.encode("utf-8", errors="ignore"))
    for path in sorted(project_root.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        stat = path.stat()
        digest.update(path.name.encode("utf-8", errors="ignore"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(int(stat.st_mtime_ns)).encode("ascii"))
    seed = digest.hexdigest()[:8]
    if used_seeds is None:
        return seed
    counter = 0
    while seed in used_seeds:
        counter += 1
        seed = hashlib.sha256(f"{digest.hexdigest()}:{counter}".encode("ascii")).hexdigest()[:8]
    used_seeds.add(seed)
    return seed


def project_output_name(project_root: Path, seed: str) -> str:
    match = re.search(r"(\d+).*?(\d+)_extracted$", project_root.name)
    if not match:
        safe_name = re.sub(r"[^A-Za-z0-9]+", "_", project_root.stem).strip("_").lower() or "structure"
        return f"{safe_name}_rc_seed_{seed}.json"
    story_count = int(match.group(1))
    layout_id = int(match.group(2))
    return f"stories_{story_count:02d}_layout_{layout_id:02d}_rc_seed_{seed}.json"


def output_path_for_project(project_root: Path, used_seeds: Optional[set] = None) -> Path:
    out_dir = workspace_root_for_project(project_root) / "rc_out"
    seed = project_output_seed(project_root, used_seeds)
    return out_dir / project_output_name(project_root, seed)


def parse_story_file_index(path: Path, prefix: str) -> Optional[int]:
    match = re.fullmatch(rf"{re.escape(prefix)}(\d+)\.T", path.name, re.I)
    return int(match.group(1)) if match else None


def discover_story_files(prefix: str) -> Dict[int, Path]:
    output: Dict[int, Path] = {}
    for path in ROOT.glob(f"{prefix}*.T"):
        index = parse_story_file_index(path, prefix)
        if index is None:
            continue
        output[index] = path
    return output


def read_float_quad(data: bytes, offset: int) -> Tuple[float, float, float, float]:
    chunk = data[offset : offset + 16]
    if len(chunk) != 16:
        raise ValueError("incomplete float quad")
    return tuple(float(v) for v in struct.unpack("<ffff", chunk))


def decode_raw_text(raw: bytes) -> str:
    text = raw.decode("latin1", errors="ignore").replace("\x00", "").strip()
    text = text.replace("\x84", "Φ")
    for bad in ("Å", "º", "î", "脜", "潞", "卯"):
        text = text.replace(bad, "")
    return re.sub(r"\s+", " ", text).strip()


def pick_standard_prefix(raw_digits: str, standard_values: Sequence[str]) -> str:
    for value in standard_values:
        if raw_digits.startswith(value):
            return value
    if len(raw_digits) >= 3:
        return raw_digits[:3]
    if len(raw_digits) >= 2:
        return raw_digits[:2]
    return raw_digits


def normalize_section_dim(raw_digits: str) -> int:
    digits = re.sub(r"\D", "", raw_digits)
    if not digits:
        raise RuntimeError("empty section dimension")
    for value in STANDARD_SECTION_DIMS:
        if digits.startswith(value):
            return int(value)
    if len(digits) >= 4:
        return int(digits[:4])
    if len(digits) >= 3:
        return int(digits[:3])
    return int(digits)


def canonicalize_rebar_expr(text: str, keep_prefix_n: bool = False) -> str:
    prefix = ""
    compact = text.replace(" ", "").replace("（", "(").replace("）", ")")
    if keep_prefix_n and compact.startswith("N"):
        prefix = "N"
        compact = compact[1:]
    compact = compact.replace("(", "").replace(")", "")
    compact = re.sub(r"[^0-9Φ+]+", "+", compact)
    compact = re.sub(r"\++", "+", compact).strip("+")
    if not compact:
        return prefix

    parts: List[str] = []
    for token in compact.split("+"):
        match = re.search(r"(\d+)Φ(\d+)", token)
        if match:
            count = match.group(1)
            diameter_digits = match.group(2)
        elif token.isdigit() and len(token) >= 3:
            count = token[0]
            diameter_digits = token[1:]
        else:
            continue
        diameter = pick_standard_prefix(diameter_digits, STANDARD_DIAMETERS)
        parts.append(f"{count}Φ{diameter}")
    return prefix + "+".join(parts)


def canonicalize_support_text(text: str) -> str:
    cleaned = text.replace(" ", "").replace("（", "(").replace("）", ")")
    cleaned = re.sub(r"[^0-9A-Za-zΦ+/@()]+", "", cleaned)
    if not cleaned:
        return ""
    if cleaned.startswith("N"):
        return canonicalize_rebar_expr(cleaned, keep_prefix_n=True)

    special = re.fullmatch(r"(\d+Φ\d+)(\d+)/2", cleaned)
    if special:
        cleaned = f"{special.group(1)}/{special.group(2)}Φ22"
    else:
        special = re.fullmatch(r"(\d+Φ\d+)\s*(\d+)/2", text.replace("（", "(").replace("）", ")").strip())
        if special:
            cleaned = f"{special.group(1)}/{special.group(2)}Φ22"

    if "/" not in cleaned:
        return canonicalize_rebar_expr(cleaned)

    left_raw, right_raw = cleaned.split("/", 1)
    left = canonicalize_rebar_expr(left_raw)
    if "Φ" not in right_raw and re.fullmatch(r"\d+", right_raw):
        right_raw = f"{right_raw}Φ22"
    right = canonicalize_rebar_expr(right_raw)
    if left and right:
        return f"{left}+{right}"
    return left or right


def canonicalize_support_text_v2(text: str) -> str:
    if ":" in text:
        return ""
    source = text.replace("(", "(").replace(")", ")").strip()
    source = re.sub(r"[^0-9A-Za-zΦ+/@()\s]+", "", source)
    if not source:
        return ""
    if source.startswith("N"):
        return canonicalize_rebar_expr(source, keep_prefix_n=True)

    layout_only = re.fullmatch(r"(.+?\d+Φ\d+)\s+\d+/2(?:\s+\d+/2)*", source)
    if layout_only and "/" not in layout_only.group(1):
        return canonicalize_rebar_expr(layout_only.group(1))

    cleaned = source.replace(" ", "")
    cleaned = re.sub(r"\)\d+$", "", cleaned)
    if "/" not in cleaned:
        return canonicalize_rebar_expr(cleaned)

    left_raw, right_raw = cleaned.split("/", 1)
    left = canonicalize_rebar_expr(left_raw)
    if "Φ" not in right_raw:
        return left
    right = canonicalize_rebar_expr(right_raw)
    if left and right:
        return f"{left}+{right}"
    return left or right


def canonicalize_beam_text(raw: bytes) -> str:
    text = decode_raw_text(raw)
    return re.sub(r"[^0-9A-Za-zΦ@+*/().,;:~ xXN-]+", "", text).strip()


def canonicalize_stirrup_text(text: str) -> str:
    compact = text.replace(" ", "")
    match = re.search(r"Φ?(\d+)@(\d+)(?:/(\d+))?(?:\((\d+)\))?", compact)
    if not match:
        raise RuntimeError(f"invalid stirrup text: {text}")
    diameter = pick_standard_prefix(match.group(1), STANDARD_DIAMETERS)
    spacing_1 = pick_standard_prefix(match.group(2), STANDARD_SPACINGS)
    spacing_2 = pick_standard_prefix(match.group(3), STANDARD_SPACINGS) if match.group(3) else None
    limbs = match.group(4)
    result = f"Φ{diameter}@{spacing_1}"
    if spacing_2:
        result += f"/{spacing_2}"
    if limbs:
        result += f"({limbs})"
    return result


def parse_stirrup_payload(text: str) -> Tuple[str, str, str, int]:
    canonical = canonicalize_stirrup_text(text)
    match = re.fullmatch(r"Φ(\d+)@(\d+)(?:/(\d+))?(?:\((\d+)\))?", canonical)
    if not match:
        raise RuntimeError(f"invalid canonical stirrup text: {canonical}")
    diameter = match.group(1)
    dense_spacing = match.group(2)
    non_dense_spacing = match.group(3) or dense_spacing
    limbs = int(match.group(4) or "0")
    dense_text = f"Φ{diameter}@{dense_spacing}" + (f"({limbs})" if limbs else "")
    non_dense_text = f"Φ{diameter}@{non_dense_spacing}" + (f"({limbs})" if limbs else "")
    return canonical, dense_text, non_dense_text, limbs


def normalize_column_text(raw: bytes) -> str:
    latin = decode_raw_text(raw)
    latin = re.sub(r"[^0-9A-Za-z\u4e00-\u9fffΦ@+*/().,;:~ xX-]+", "", latin).strip()
    if re.search(r"(KZ\d|Φ|@|~|\d+\.\d+)", latin):
        latin = re.sub(r"(KZ\d+)[A-Za-z.]+$", r"\1", latin)
        latin = re.sub(r"([0-9Φ@+~xX/-]+)[A-Za-z.]+$", r"\1", latin)
        return latin
    try:
        return raw.decode("gb18030").replace("\x00", "").strip()
    except UnicodeDecodeError:
        return latin


def find_text_start(data: bytes, marker_index: int) -> Optional[int]:
    text_end = marker_index - 24
    best_start = None
    for start in range(max(0, marker_index - 240), text_end - 68):
        if data[start + 4 : start + 8] != TEXT_HEADER_SECOND_DWORD:
            continue
        raw = data[start + 68 : text_end].rstrip(b"\x00")
        if not raw or len(raw) > 160:
            continue
        try:
            x, y, dx, dy = read_float_quad(data, start + 40)
        except ValueError:
            continue
        if not (-50000 <= x <= 50000 and -50000 <= y <= 50000):
            continue
        if not (-5000 <= dx <= 5000 and -5000 <= dy <= 5000):
            continue
        best_start = start
    return best_start


def parse_text_records(path: Path, marker: bytes) -> List[TextRecord]:
    data = path.read_bytes()
    records: List[TextRecord] = []
    pos = 0
    marker_name = marker.decode("ascii")
    while True:
        idx = data.find(marker, pos)
        if idx < 0:
            break
        start = find_text_start(data, idx)
        if start is not None:
            text_end = idx - 24
            x, y, _, _ = read_float_quad(data, start + 40)
            angle_deg = float(struct.unpack("<f", data[start + 64 : start + 68])[0])
            raw = data[start + 68 : text_end].rstrip(b"\x00")
            records.append(TextRecord(marker_name, x, y, raw, angle_deg))
        pos = idx + 1
    return records


def parse_massline_coords(path: Path) -> List[Tuple[float, float]]:
    data = path.read_bytes()
    coords: List[Tuple[float, float]] = []
    pos = 0
    marker = b"$BP_MASSLINE"
    while True:
        idx = data.find(marker, pos)
        if idx < 0:
            break
        start = None
        for candidate in range(max(0, idx - 160), idx - 64):
            if data[candidate + 4 : candidate + 8] == MASSLINE_HEADER_SECOND_DWORD:
                start = candidate
        if start is not None:
            x, y, _, _ = read_float_quad(data, start + 40)
            coords.append((x, y))
        pos = idx + 1
    return coords


def fit_translation(plan_points: Sequence[Tuple[float, float]], model_points: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    model_xs = sorted({x for x, _ in model_points})
    model_ys = sorted({y for _, y in model_points})
    dxs = []
    dys = []
    for x, y in plan_points:
        nearest_x = min(model_xs, key=lambda v: abs(v - x))
        nearest_y = min(model_ys, key=lambda v: abs(v - y))
        dxs.append(x - nearest_x)
        dys.append(y - nearest_y)
    dx = statistics.median(dxs)
    dy = statistics.median(dys)
    if max(abs(v - dx) for v in dxs) > 2.0 or max(abs(v - dy) for v in dys) > 2.0:
        raise RuntimeError(f"failed to fit stable translation: dx={dxs}, dy={dys}")
    return dx, dy


def parse_first_table_value(text: str, header_pattern: str, row_pattern: str) -> Optional[float]:
    match = re.search(header_pattern + r".*?" + row_pattern, text, re.S)
    return float(match.group(1)) if match else None


def parse_wmass() -> Tuple[dict, dict, dict]:
    text = WMASS_PATH.read_text(encoding="gb18030", errors="ignore")

    def find_float(pattern: str) -> Optional[float]:
        match = re.search(pattern, text, re.M)
        return float(match.group(1)) if match else None

    intensity = find_float(r"地震烈度:.*?=\s*([0-9.]+)")
    site_match = re.search(r"场地类别:.*?=\s*([A-Z]+)", text)
    design_group_match = re.search(r"设计地震分组:\s*([一二三四])组", text)
    damping = find_float(r"DAMP\s*=\s*([0-9.]+)")
    beam_cover = find_float(r"梁保护层厚度 \(mm\):.*?=\s*([0-9.]+)")
    column_cover = find_float(r"柱保护层厚度 \(mm\):.*?=\s*([0-9.]+)")
    story_count_match = re.search(r"NSTI\s*=\s*([0-9.]+)", text)
    floor_area = parse_first_table_value(
        text,
        r"层号\s+塔号\s+面积\s+形心X\s+形心Y",
        r"\n\s*1\s+1\s+([0-9.]+)",
    )

    material_row_match = re.search(
        r"^\s*1\(\s*1\)\s+1\s+\d+\(\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\)\s+\d+\(\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\)",
        text,
        re.M,
    )
    if not material_row_match:
        raise RuntimeError("failed to parse WMASS material row")
    beam_concrete_strength = int(material_row_match.group(1))
    beam_main_strength = int(material_row_match.group(2))
    beam_stirrup_strength = int(material_row_match.group(3))
    column_concrete_strength = int(material_row_match.group(4))
    column_main_strength = int(material_row_match.group(5))
    column_stirrup_strength = int(material_row_match.group(6))
    if beam_concrete_strength != column_concrete_strength:
        raise RuntimeError("beam/column concrete strengths differ in WMASS.OUT")
    if beam_main_strength != column_main_strength:
        raise RuntimeError("beam/column main steel strengths differ in WMASS.OUT")
    if beam_stirrup_strength != column_stirrup_strength:
        raise RuntimeError("beam/column stirrup strengths differ in WMASS.OUT")

    concrete_grade = CONCRETE_GRADE_BY_STRENGTH.get(beam_concrete_strength)
    if concrete_grade is None:
        raise RuntimeError(f"unsupported concrete strength in WMASS.OUT: {beam_concrete_strength}")
    steel_grade = STEEL_GRADE_BY_FY.get(beam_main_strength)
    if steel_grade is None:
        raise RuntimeError(f"unsupported steel strength in WMASS.OUT: {beam_main_strength}")
    concrete_props = CONCRETE_PROPERTIES[concrete_grade]
    steel_modulus = STEEL_ELASTIC_MODULUS[steel_grade]
    if beam_cover is None or column_cover is None:
        raise RuntimeError("failed to parse beam/column concrete cover in WMASS.OUT")
    if floor_area is None:
        raise RuntimeError("failed to parse floor_area from WMASS.OUT")
    if story_count_match is None:
        raise RuntimeError("failed to parse story_count from WMASS.OUT")

    group_map = {"一": 1, "二": 2, "三": 3, "四": 4}
    seismic = {
        "seismic_intensity": int(round(intensity or 0.0)),
        "PGA_target": 0.2,
        "design_group": group_map.get(design_group_match.group(1), None) if design_group_match else None,
        "site_class": site_match.group(1) if site_match else None,
        "damping_ratio": (damping or 0.0) / 100.0 if damping is not None else None,
    }
    materials = {
        "concrete_grade": concrete_grade,
        "steel_grade": steel_grade,
        "E_c": concrete_props["E_c"],
        "E_s": steel_modulus,
        "fc": concrete_props["fc"],
        "fck": concrete_props["fck"],
        "ft": concrete_props["ft"],
        "ftk": concrete_props["ftk"],
        "fy": float(beam_main_strength),
        "concrete_cover_mm": min(beam_cover, column_cover),
    }
    geometry = {
        "floor_area": floor_area,
        "story_count": int(round(float(story_count_match.group(1)))),
    }
    return seismic, materials, geometry


def infer_section_pairs_from_sdata() -> Tuple[Tuple[int, int], Tuple[int, int]]:
    data = SDATA_PATH.read_bytes()
    pair_counts: Dict[Tuple[float, float], int] = {}
    for off in range(0, len(data) - 24, 4):
        values = struct.unpack("<6f", data[off : off + 24])
        a, b, c, d, e, f = values
        if not all(math.isfinite(v) for v in values):
            continue
        if not (0.05 <= a <= 2.0 and 0.05 <= b <= 2.0):
            continue
        if sum(1 for v in (c, d, e, f) if abs(v) < 1e-6) < 2:
            continue
        key = (round(a, 3), round(b, 3))
        pair_counts[key] = pair_counts.get(key, 0) + 1
    sorted_pairs = sorted(pair_counts.items(), key=lambda item: item[1], reverse=True)
    beam_pair = next(pair for pair, _ in sorted_pairs if pair[0] != pair[1])
    column_candidates = []
    for pair, count in sorted_pairs:
        if pair[0] != pair[1] or pair[0] < 0.5:
            continue
        dim = pair[0]
        companion_count = max(pair_counts.get((1.0, dim), 0), pair_counts.get((dim, 1.0), 0))
        if dim < 1.0 and companion_count >= max(1, int(count * 0.5)):
            column_candidates.append((pair, count, companion_count))
    if column_candidates:
        column_pair = max(column_candidates, key=lambda item: (item[1], item[2]))[0]
    else:
        column_pair = next(pair for pair, _ in sorted_pairs if pair[0] == pair[1] and pair[0] >= 0.5)
    beam_dims = (int(round(beam_pair[0] * 1000.0)), int(round(beam_pair[1] * 1000.0)))
    column_dims = (int(round(column_pair[0] * 1000.0)), int(round(column_pair[1] * 1000.0)))
    return beam_dims, column_dims


def parse_sdata_column_points(expected_story_count: int) -> List[ColumnPoint]:
    data = SDATA_PATH.read_bytes()
    coords_by_key: Dict[Tuple[int, int, int], ColumnPoint] = {}
    for off in range(0, len(data) - 48, 4):
        a, b, c, d, e, code, x, y, z, i1, i2, i3 = struct.unpack("<12f", data[off : off + 48])
        if code != 101.0:
            continue
        if not all(math.isfinite(v) for v in (x, y, z, i1, i2, i3)):
            continue
        if z <= 0.0:
            continue
        if any(abs(v - round(v)) > 1e-6 for v in (i1, i2, i3)):
            continue
        if x < -0.001 or y < -0.001 or z < 0.0 or x > 100.0 or y > 100.0 or z > 100.0:
            continue
        key = (int(round(x * 1000.0)), int(round(y * 1000.0)), int(round(z * 1000.0)))
        coords_by_key[key] = ColumnPoint(
            x_mm=key[0],
            y_mm=key[1],
            z_top_mm=key[2],
            story_index=-1,
            story_code_ids=(int(round(i1)), int(round(i2)), int(round(i3))),
        )
    points_by_z: Dict[int, List[ColumnPoint]] = {}
    for point in coords_by_key.values():
        points_by_z.setdefault(point.z_top_mm, []).append(point)
    z_groups_by_count: Dict[int, List[int]] = {}
    for z, items in points_by_z.items():
        z_groups_by_count.setdefault(len(items), []).append(z)
    candidate_groups = [
        (count, sorted(z_levels))
        for count, z_levels in z_groups_by_count.items()
        if len(z_levels) >= expected_story_count
    ]
    if not candidate_groups:
        raise RuntimeError(
            f"failed to find {expected_story_count} stable story z-levels in SDATA.SAT: "
            f"{sorted((z, len(items)) for z, items in points_by_z.items())}"
        )
    candidate_groups.sort(key=lambda item: (-len(item[1]), -item[0]))
    stable_count, stable_z_levels = candidate_groups[0]
    valid_z = stable_z_levels[:expected_story_count]
    z_to_story = {z: index for index, z in enumerate(valid_z)}
    points = []
    for point in sorted(coords_by_key.values(), key=lambda item: (item.z_top_mm, item.x_mm, item.y_mm)):
        if point.z_top_mm not in z_to_story:
            continue
        points.append(
            ColumnPoint(
                x_mm=point.x_mm,
                y_mm=point.y_mm,
                z_top_mm=point.z_top_mm,
                story_index=z_to_story[point.z_top_mm],
                story_code_ids=point.story_code_ids,
            )
        )
    return points


def build_story_nodes(points: Sequence[ColumnPoint]) -> Dict[int, Dict[str, object]]:
    stories: Dict[int, Dict[str, object]] = {}
    grouped: Dict[int, List[ColumnPoint]] = {}
    for point in points:
        grouped.setdefault(point.story_index, []).append(point)

    for story_index, story_points in grouped.items():
        sorted_points = sorted(story_points, key=lambda item: (item.y_mm, item.x_mm))
        local_node_by_coord = {
            (point.x_mm, point.y_mm): index + 1
            for index, point in enumerate(sorted_points)
        }
        stories[story_index] = {
            "points": sorted_points,
            "local_node_by_coord": local_node_by_coord,
            "z_top_mm": sorted_points[0].z_top_mm,
            "z_bottom_mm": 0 if story_index == 0 else grouped[story_index - 1][0].z_top_mm,
        }
    return stories


def generate_candidate_beams(local_node_by_coord: Dict[Tuple[int, int], int]) -> List[BeamCandidate]:
    coords = sorted(local_node_by_coord.keys())
    all_x_levels = sorted({coord[0] for coord in coords})
    all_y_levels = sorted({coord[1] for coord in coords})
    y_levels = sorted({coord[1] for coord in coords})
    beams: List[BeamCandidate] = []
    serial = 1

    def has_intermediate_axis(levels: Sequence[int], start: int, end: int) -> bool:
        low, high = sorted((start, end))
        return any(low < level < high for level in levels)

    for index, y in enumerate(y_levels):
        row_x = sorted(coord[0] for coord in coords if coord[1] == y)
        for x1, x2 in zip(row_x, row_x[1:]):
            if has_intermediate_axis(all_x_levels, x1, x2):
                continue
            beams.append(
                BeamCandidate(
                    serial=serial,
                    local_node_i=local_node_by_coord[(x1, y)],
                    local_node_j=local_node_by_coord[(x2, y)],
                    x1_mm=x1,
                    y1_mm=y,
                    x2_mm=x2,
                    y2_mm=y,
                )
            )
            serial += 1
        if index + 1 >= len(y_levels):
            continue
        next_y = y_levels[index + 1]
        xs_here = {coord[0] for coord in coords if coord[1] == y}
        xs_next = {coord[0] for coord in coords if coord[1] == next_y}
        for x in sorted(xs_here & xs_next):
            if has_intermediate_axis(all_y_levels, y, next_y):
                continue
            beams.append(
                BeamCandidate(
                    serial=serial,
                    local_node_i=local_node_by_coord[(x, y)],
                    local_node_j=local_node_by_coord[(x, next_y)],
                    x1_mm=x,
                    y1_mm=y,
                    x2_mm=x,
                    y2_mm=next_y,
                )
            )
            serial += 1
    return beams


def parse_sdata_beam_blocks(expected_beam_counts: Sequence[int]) -> List[List[BeamConnectivity]]:
    data = SDATA_PATH.read_bytes()
    records: List[Tuple[int, BeamConnectivity]] = []
    for off in range(24, len(data), 4):
        code = int(round(struct.unpack("<f", data[off : off + 4])[0]))
        if code not in {1001, 2001, 2011}:
            continue
        values = struct.unpack("<7f", data[off - 24 : off + 4])
        story_flag, serial1, serial2, global_i, global_j, zero, code_value = values
        if any(abs(v - round(v)) > 1e-6 for v in values):
            continue
        if int(round(zero)) != 0 or int(round(serial1)) != int(round(serial2)):
            continue
        records.append(
            (
                off,
                BeamConnectivity(
                    serial=int(round(serial1)),
                    global_node_i=int(round(global_i)),
                    global_node_j=int(round(global_j)),
                    code=int(round(code_value)),
                ),
            )
        )

    blocks: List[List[BeamConnectivity]] = []
    current: List[BeamConnectivity] = []
    previous_serial: Optional[int] = None
    for _, record in records:
        if previous_serial is not None and record.serial <= previous_serial:
            if current:
                blocks.append(current)
            current = []
        current.append(record)
        previous_serial = record.serial
    if current:
        blocks.append(current)

    selected: List[List[BeamConnectivity]] = []
    expected_index = 0
    for block in blocks:
        if expected_index >= len(expected_beam_counts):
            break
        expected_beam_count = expected_beam_counts[expected_index]
        if len(block) != expected_beam_count:
            continue
        if [record.serial for record in block] != list(range(1, expected_beam_count + 1)):
            continue
        selected.append(block)
        expected_index += 1
    if len(selected) != len(expected_beam_counts):
        raise RuntimeError(
            f"beam connectivity blocks not enough: expected {len(expected_beam_counts)}, got {len(selected)} "
            f"for counts {list(expected_beam_counts)}"
        )
    return selected


def derive_story_geometry(
    story_nodes: Dict[int, Dict[str, object]],
    beam_blocks: Sequence[List[BeamConnectivity]],
) -> Tuple[Dict[int, dict], Dict[int, dict]]:
    node_maps: Dict[int, dict] = {}
    beam_maps: Dict[int, dict] = {}

    for story_index in sorted(story_nodes):
        local_node_by_coord = story_nodes[story_index]["local_node_by_coord"]  # type: ignore[index]
        candidates = generate_candidate_beams(local_node_by_coord)  # type: ignore[arg-type]
        block = beam_blocks[story_index]
        if len(candidates) != len(block):
            raise RuntimeError(f"story {story_index}: candidate beams {len(candidates)} != SDATA beams {len(block)}")

        global_by_local: Dict[int, int] = {}
        coord_by_global: Dict[int, Tuple[int, int]] = {}
        for connectivity, candidate in zip(block, candidates):
            if connectivity.serial != candidate.serial:
                raise RuntimeError(f"story {story_index}: beam serial mismatch {connectivity.serial} != {candidate.serial}")
            for global_id, local_id, coord in (
                (connectivity.global_node_i, candidate.local_node_i, (candidate.x1_mm, candidate.y1_mm)),
                (connectivity.global_node_j, candidate.local_node_j, (candidate.x2_mm, candidate.y2_mm)),
            ):
                existing_local = global_by_local.get(local_id)
                if existing_local is None:
                    global_by_local[local_id] = global_id
                elif existing_local != global_id:
                    raise RuntimeError(f"story {story_index}: conflicting global id for local node {local_id}")
                existing_coord = coord_by_global.get(global_id)
                if existing_coord is None:
                    coord_by_global[global_id] = coord
                elif existing_coord != coord:
                    raise RuntimeError(f"story {story_index}: conflicting coord for global node {global_id}")

        points = story_nodes[story_index]["points"]  # type: ignore[index]
        node_map: Dict[int, dict] = {}
        for point in points:  # type: ignore[assignment]
            local_id = local_node_by_coord[(point.x_mm, point.y_mm)]  # type: ignore[index]
            global_id = global_by_local.get(local_id)
            if global_id is None:
                raise RuntimeError(f"story {story_index}: local node {local_id} has no global id")
            node_map[local_id] = {
                "local_node_id": local_id,
                "global_node_id": global_id,
                "x_mm": point.x_mm,
                "y_mm": point.y_mm,
            }
        node_maps[story_index] = node_map

        beam_map: Dict[int, dict] = {}
        for connectivity, candidate in zip(block, candidates):
            beam_map[candidate.serial - 1] = {
                "serial": candidate.serial,
                "global_node_I": connectivity.global_node_i,
                "global_node_J": connectivity.global_node_j,
                "local_node_I": candidate.local_node_i,
                "local_node_J": candidate.local_node_j,
                "x1_mm": candidate.x1_mm,
                "y1_mm": candidate.y1_mm,
                "x2_mm": candidate.x2_mm,
                "y2_mm": candidate.y2_mm,
                "z_mm": story_nodes[story_index]["z_top_mm"],  # type: ignore[index]
            }
        beam_maps[story_index] = beam_map
    return node_maps, beam_maps


def build_column_type_table(detail_records: Sequence[TextRecord]) -> Dict[str, dict]:
    cleaned = []
    for record in detail_records:
        text = normalize_column_text(record.raw)
        if text:
            cleaned.append((record.x, record.y, text))
    if not cleaned:
        raise RuntimeError("detail table is empty")

    rows: List[dict] = []
    for x, y, text in sorted(cleaned, key=lambda item: item[1]):
        if rows and abs(rows[-1]["y"] - y) <= 5.0:
            rows[-1]["items"].append((x, y, text))
            item_count = len(rows[-1]["items"])
            rows[-1]["y"] = (rows[-1]["y"] * (item_count - 1) + y) / item_count
        else:
            rows.append({"y": y, "items": [(x, y, text)]})

    def row_texts(row: dict) -> List[str]:
        return [item[2] for item in row["items"]]

    id_row = max(
        rows,
        key=lambda row: sum(1 for text in row_texts(row) if re.fullmatch(r"KZ\d+", text)),
    )
    id_items = sorted(
        [(x, text) for x, _, text in id_row["items"] if re.fullmatch(r"KZ\d+", text)],
        key=lambda item: item[0],
    )
    if not id_items:
        raise RuntimeError("detail table has no column type ids")

    id_y = float(id_row["y"])
    lower_rows = [row for row in rows if row["y"] < id_y - 5.0]

    def pick_row(predicate, anchor_y: float) -> Optional[dict]:
        candidates = [row for row in lower_rows if any(predicate(text) for text in row_texts(row))]
        if not candidates:
            return None
        return min(candidates, key=lambda row: (abs(row["y"] - anchor_y), -len(row["items"])))

    elevation_row = pick_row(lambda text: "~" in text and re.search(r"\d+\.\d+", text), id_y)
    if elevation_row is None:
        raise RuntimeError("detail table has no elevation row")
    longitudinal_row = pick_row(
        lambda text: re.search(r"[Φ朴桅]", text) is not None and "@" not in text and not re.fullmatch(r"KZ\d+", text),
        elevation_row["y"],
    )
    stirrup_row = pick_row(lambda text: "@" in text, elevation_row["y"])
    if longitudinal_row is None or stirrup_row is None:
        raise RuntimeError("detail table missing longitudinal/stirrup rows")

    x_positions = [x for x, _ in id_items]
    x_spacings = [b - a for a, b in zip(x_positions, x_positions[1:]) if b > a]
    x_tol = max(250.0, min(1200.0, (statistics.median(x_spacings) / 3.0) if x_spacings else 450.0))

    def nearest_text(row: dict, target_x: float) -> Optional[str]:
        best = None
        for x, _, text in row["items"]:
            dist = abs(x - target_x)
            if dist > x_tol:
                continue
            if best is None or dist < best[0]:
                best = (dist, text)
        return best[1] if best else None

    output: Dict[str, dict] = {}
    for x, column_id in id_items:
        elevation = nearest_text(elevation_row, x)
        longitudinal = nearest_text(longitudinal_row, x)
        stirrup = nearest_text(stirrup_row, x)
        if not all([elevation, longitudinal, stirrup]):
            raise RuntimeError(f"incomplete detail row for {column_id}")
        output[column_id] = {
            "elevation": elevation,
            "longitudinal_rebar": canonicalize_rebar_expr(longitudinal),
            "stirrup": canonicalize_stirrup_text(stirrup),
        }
    return output


def assign_column_types(
    column_plan_records: Sequence[TextRecord],
    story_points: Sequence[ColumnPoint],
    translation: Tuple[float, float],
) -> Dict[Tuple[int, int], str]:
    dx, dy = translation
    point_coords = {(point.x_mm, point.y_mm) for point in story_points}
    output: Dict[Tuple[int, int], str] = {}
    used = set()
    for record in column_plan_records:
        text = normalize_column_text(record.raw)
        match = re.search(r"KZ\d+", text)
        if not match:
            continue
        model_x = int(round(record.x - dx))
        model_y = int(round(record.y - dy))
        nearest = min(point_coords, key=lambda coord: math.hypot(coord[0] - model_x, coord[1] - model_y))
        dist = math.hypot(nearest[0] - model_x, nearest[1] - model_y)
        if dist > 10.0:
            continue
        existing = output.get(nearest)
        if existing is not None:
            if existing != match.group(0):
                raise RuntimeError(f"conflicting KZ label assignment at {nearest}: {existing} vs {match.group(0)}")
            continue
        used.add(nearest)
        output[nearest] = match.group(0)
    if not output:
        raise RuntimeError("column type assignment found no KZ labels")
    return output


def beam_header_pattern(text: str) -> Optional[re.Match[str]]:
    return re.search(r"([A-Z]*KL\d+\((\d+)\))", text)


def choose_header_partition(
    header_items: Sequence[Tuple[TextRecord, str, int, int, int, int, int]],
    horizontal_counts: Sequence[int],
    vertical_counts: Sequence[int],
) -> Tuple[List[Tuple[TextRecord, str, int, int, int, int, int]], List[Tuple[TextRecord, str, int, int, int, int, int]]]:
    indices = range(len(header_items))
    horizontal_size = len(horizontal_counts)
    solutions = []
    for combo in itertools.combinations(indices, horizontal_size):
        horizontal = sorted((header_items[index] for index in combo), key=lambda item: item[6])
        vertical = sorted((header_items[index] for index in indices if index not in combo), key=lambda item: item[5])
        if [item[2] for item in horizontal] != list(horizontal_counts):
            continue
        if [item[2] for item in vertical] != list(vertical_counts):
            continue
        score = (
            sum(abs(horizontal[index][6] - horizontal[index - 1][6]) for index in range(1, len(horizontal))),
            sum(abs(vertical[index][5] - vertical[index - 1][5]) for index in range(1, len(vertical))),
        )
        solutions.append((score, horizontal, vertical))
    if not solutions:
        raise RuntimeError("failed to partition beam strip headers")
    solutions.sort(key=lambda item: item[0])
    return solutions[0][1], solutions[0][2]


def collect_strip_texts(
    header_record: TextRecord,
    orientation: str,
    mass_records: Sequence[TextRecord],
    dx: float,
    dy: float,
) -> Tuple[str, str, str, str]:
    header_x = header_record.x - dx
    header_y = header_record.y - dy
    nearby_texts: List[str] = []
    for mass in mass_records:
        model_x = mass.x - dx
        model_y = mass.y - dy
        if orientation == "H":
            if abs(model_x - header_x) <= 700 and abs(model_y - header_y) <= 1400:
                nearby_texts.append(canonicalize_beam_text(mass.raw))
        else:
            if abs(model_y - header_y) <= 700 and abs(model_x - header_x) <= 1800:
                nearby_texts.append(canonicalize_beam_text(mass.raw))

    stirrup_raw = next((text for text in nearby_texts if "@" in text), None)
    main_raw = next(
        (
            text
            for text in nearby_texts
            if "@" not in text and not text.startswith("N") and not beam_header_pattern(text)
        ),
        None,
    )
    waist_raw = next((text for text in nearby_texts if text.startswith("N")), None)
    if not stirrup_raw or not main_raw:
        raise RuntimeError(f"incomplete beam strip texts near ({header_x}, {header_y})")

    parts = [part for part in main_raw.split(";") if part.strip()]
    top_middle = canonicalize_rebar_expr(parts[0]) if parts else ""
    bottom = canonicalize_rebar_expr(parts[1]) if len(parts) >= 2 else ""
    waist = canonicalize_rebar_expr(waist_raw, keep_prefix_n=True) if waist_raw else "0"
    stirrup = canonicalize_stirrup_text(stirrup_raw)
    return top_middle, bottom, waist, stirrup


def parse_story_beam_strips(
    story_index: int,
    beam_map: Dict[int, dict],
    translation: Tuple[float, float],
) -> Tuple[Dict[Tuple[str, int, int, int], BeamStrip], Dict[int, Tuple[str, int, int, int]]]:
    beam_path = ROOT / f"Beam{story_index + 1}.T"
    mass_records = parse_text_records(beam_path, b"$BP_MASS")
    massline_coords = parse_massline_coords(beam_path)
    header_items = []
    dx, dy = translation
    for record in mass_records:
        text = canonicalize_beam_text(record.raw)
        match = beam_header_pattern(text)
        if not match:
            continue
        section_match = re.search(r"(\d+)x(\d+)", text)
        header_items.append(
            (
                record,
                match.group(1),
                int(match.group(2)),
                normalize_section_dim(section_match.group(1)) if section_match else 0,
                normalize_section_dim(section_match.group(2)) if section_match else 0,
                int(round(record.x - dx)),
                int(round(record.y - dy)),
            )
        )
    sections_by_beam_id: Dict[str, Tuple[int, int]] = {}
    for _, beam_id, _, b_mm, h_mm, _, _ in header_items:
        if b_mm and h_mm:
            sections_by_beam_id[beam_id] = (b_mm, h_mm)
    header_items = [
        (
            record,
            beam_id,
            count,
            sections_by_beam_id.get(beam_id, (b_mm, h_mm))[0],
            sections_by_beam_id.get(beam_id, (b_mm, h_mm))[1],
            model_x,
            model_y,
        )
        for record, beam_id, count, b_mm, h_mm, model_x, model_y in header_items
    ]
    deduped_header_items = []
    seen_header_keys = set()
    for item in header_items:
        _, beam_id, count, _, _, model_x, model_y = item
        key = (beam_id, count, model_x, model_y)
        if key in seen_header_keys:
            continue
        seen_header_keys.add(key)
        deduped_header_items.append(item)
    header_items = deduped_header_items
    missing_sections = [beam_id for _, beam_id, _, b_mm, h_mm, _, _ in header_items if not b_mm or not h_mm]
    if missing_sections:
        raise RuntimeError(f"beam strip headers still missing section after beam-id fill: {missing_sections}")
    if not header_items:
        raise RuntimeError(f"Beam{story_index + 1}.T: no beam strip headers found")

    y_groups: Dict[int, int] = {}
    x_groups: Dict[int, int] = {}
    for beam in beam_map.values():
        if beam["y1_mm"] == beam["y2_mm"]:
            y_groups[beam["y1_mm"]] = y_groups.get(beam["y1_mm"], 0) + 1
        else:
            x_groups[beam["x1_mm"]] = x_groups.get(beam["x1_mm"], 0) + 1

    if len(header_items) == len(y_groups) + len(x_groups):
        try:
            horizontal_counts = [count for _, count in sorted(y_groups.items())]
            vertical_counts = [count for _, count in sorted(x_groups.items())]
            horizontal_headers, vertical_headers = choose_header_partition(header_items, horizontal_counts, vertical_counts)
            strips: Dict[Tuple[str, int, int, int], BeamStrip] = {}
            beam_to_strip_key: Dict[int, Tuple[str, int, int, int]] = {}
            strip_text_cache: Dict[str, Tuple[str, str, str, str]] = {}

            def resolve_strip_texts(record: TextRecord, orientation: str, beam_id: str) -> Tuple[str, str, str, str]:
                try:
                    values = collect_strip_texts(record, orientation, mass_records, dx, dy)
                    strip_text_cache[beam_id] = values
                    return values
                except RuntimeError:
                    cached = strip_text_cache.get(beam_id)
                    if cached is None:
                        raise
                    return cached

            for (strip_y, _), item in zip(sorted(y_groups.items()), horizontal_headers):
                record, beam_id, count, b_mm, h_mm, _, _ = item
                top_middle, bottom, waist, stirrup = resolve_strip_texts(record, "H", beam_id)
                row_beam_keys = sorted(
                    [beam_key for beam_key, beam in beam_map.items() if beam["y1_mm"] == beam["y2_mm"] and beam["y1_mm"] == strip_y],
                    key=lambda beam_key: beam_map[beam_key]["x1_mm"],
                )
                strip_key = ("H", strip_y, beam_map[row_beam_keys[0]]["x1_mm"], beam_map[row_beam_keys[-1]]["x2_mm"])
                strips[strip_key] = BeamStrip(
                    beam_id=beam_id,
                    count=count,
                    orientation="H",
                    strip_coord_mm=strip_y,
                    section_b_mm=b_mm,
                    section_h_mm=h_mm,
                    top_middle_text=top_middle,
                    bottom_text=bottom,
                    waist_text=waist,
                    stirrup_text=stirrup,
                )
                for beam_key in row_beam_keys:
                    beam_to_strip_key[beam_key] = strip_key

            for (strip_x, _), item in zip(sorted(x_groups.items()), vertical_headers):
                record, beam_id, count, b_mm, h_mm, _, _ = item
                top_middle, bottom, waist, stirrup = resolve_strip_texts(record, "V", beam_id)
                col_beam_keys = sorted(
                    [beam_key for beam_key, beam in beam_map.items() if beam["x1_mm"] == beam["x2_mm"] and beam["x1_mm"] == strip_x],
                    key=lambda beam_key: beam_map[beam_key]["y1_mm"],
                )
                strip_key = ("V", strip_x, beam_map[col_beam_keys[0]]["y1_mm"], beam_map[col_beam_keys[-1]]["y2_mm"])
                strips[strip_key] = BeamStrip(
                    beam_id=beam_id,
                    count=count,
                    orientation="V",
                    strip_coord_mm=strip_x,
                    section_b_mm=b_mm,
                    section_h_mm=h_mm,
                    top_middle_text=top_middle,
                    bottom_text=bottom,
                    waist_text=waist,
                    stirrup_text=stirrup,
                )
                for beam_key in col_beam_keys:
                    beam_to_strip_key[beam_key] = strip_key

            if len(beam_to_strip_key) == len(beam_map):
                return strips, beam_to_strip_key
        except RuntimeError:
            pass

    unique_counts = sorted({count for _, _, count, _, _, _, _ in header_items})
    candidate_runs: List[BeamRun] = []
    horizontal_rows: Dict[int, List[int]] = {}
    vertical_cols: Dict[int, List[int]] = {}
    for beam_key, beam in beam_map.items():
        if beam["y1_mm"] == beam["y2_mm"]:
            horizontal_rows.setdefault(beam["y1_mm"], []).append(beam_key)
        else:
            vertical_cols.setdefault(beam["x1_mm"], []).append(beam_key)

    for strip_y, beam_keys in horizontal_rows.items():
        ordered = sorted(beam_keys, key=lambda key: beam_map[key]["x1_mm"])
        for count in unique_counts:
            if count > len(ordered):
                continue
            for start in range(0, len(ordered) - count + 1):
                run_beam_keys = tuple(ordered[start : start + count])
                first = beam_map[run_beam_keys[0]]
                last = beam_map[run_beam_keys[-1]]
                candidate_runs.append(
                    BeamRun(
                        orientation="H",
                        strip_coord_mm=strip_y,
                        start_coord_mm=first["x1_mm"],
                        end_coord_mm=last["x2_mm"],
                        center_x_mm=(first["x1_mm"] + last["x2_mm"]) / 2.0,
                        center_y_mm=float(strip_y),
                        beam_keys=run_beam_keys,
                    )
                )

    for strip_x, beam_keys in vertical_cols.items():
        ordered = sorted(beam_keys, key=lambda key: beam_map[key]["y1_mm"])
        for count in unique_counts:
            if count > len(ordered):
                continue
            for start in range(0, len(ordered) - count + 1):
                run_beam_keys = tuple(ordered[start : start + count])
                first = beam_map[run_beam_keys[0]]
                last = beam_map[run_beam_keys[-1]]
                candidate_runs.append(
                    BeamRun(
                        orientation="V",
                        strip_coord_mm=strip_x,
                        start_coord_mm=first["y1_mm"],
                        end_coord_mm=last["y2_mm"],
                        center_x_mm=float(strip_x),
                        center_y_mm=(first["y1_mm"] + last["y2_mm"]) / 2.0,
                        beam_keys=run_beam_keys,
                    )
                )

    header_candidates: List[List[Tuple[float, int]]] = []
    for header_index, (record, _, count, _, _, model_x, model_y) in enumerate(header_items):
        candidates: List[Tuple[float, int]] = []
        angle_mod = abs(record.angle_deg) % 180.0
        header_orientation = ""
        if math.isclose(angle_mod, 90.0, abs_tol=5.0):
            header_orientation = "V"
        elif math.isclose(angle_mod, 0.0, abs_tol=5.0):
            header_orientation = "H"
        for run_index, run in enumerate(candidate_runs):
            if len(run.beam_keys) != count:
                continue
            if header_orientation and run.orientation != header_orientation:
                continue
            if run.orientation == "H":
                if model_x < run.start_coord_mm - 4000 or model_x > run.end_coord_mm + 4000:
                    continue
                score = abs(model_y - run.strip_coord_mm) * 20.0 + abs(model_x - run.center_x_mm)
            else:
                if model_y < run.start_coord_mm - 4000 or model_y > run.end_coord_mm + 4000:
                    continue
                score = abs(model_x - run.strip_coord_mm) * 20.0 + abs(model_y - run.center_y_mm)
            candidates.append((score, run_index))
        if not candidates:
            raise RuntimeError(f"story {story_index}: header {header_index} has no candidate run")
        header_candidates.append(sorted(candidates, key=lambda item: item[0]))

    run_beam_sets = [set(run.beam_keys) for run in candidate_runs]

    def search_assignments(
        assigned_headers: Dict[int, int],
        used_beams: set[int],
    ) -> Optional[Dict[int, int]]:
        if len(assigned_headers) == len(header_items):
            return dict(assigned_headers)

        best_header_index = -1
        best_candidates: List[Tuple[float, int]] = []
        for header_index, candidates in enumerate(header_candidates):
            if header_index in assigned_headers:
                continue
            feasible = [
                (score, run_index)
                for score, run_index in candidates
                if not (run_beam_sets[run_index] & used_beams)
            ]
            if not feasible:
                return None
            if best_header_index < 0 or len(feasible) < len(best_candidates):
                best_header_index = header_index
                best_candidates = feasible
            elif len(feasible) == len(best_candidates):
                if feasible[0][0] < best_candidates[0][0]:
                    best_header_index = header_index
                    best_candidates = feasible

        for _, run_index in best_candidates:
            assigned_headers[best_header_index] = run_index
            added_beams = run_beam_sets[run_index]
            used_beams.update(added_beams)
            solved = search_assignments(assigned_headers, used_beams)
            if solved is not None:
                return solved
            del assigned_headers[best_header_index]
            used_beams.difference_update(added_beams)
        return None

    assigned_headers = search_assignments({}, set())
    if assigned_headers is None:
        remaining = list(range(len(header_items)))
        raise RuntimeError(f"story {story_index}: failed to assign beam strip headers {remaining}")

    strips: Dict[Tuple[str, int, int, int], BeamStrip] = {}
    beam_to_strip_key: Dict[int, Tuple[str, int, int, int]] = {}
    strip_text_cache: Dict[str, Tuple[str, str, str, str]] = {}

    def resolve_strip_texts(record: TextRecord, orientation: str, beam_id: str) -> Tuple[str, str, str, str]:
        try:
            values = collect_strip_texts(record, orientation, mass_records, dx, dy)
            strip_text_cache[beam_id] = values
            return values
        except RuntimeError:
            cached = strip_text_cache.get(beam_id)
            if cached is None:
                raise
            return cached

    for header_index in sorted(assigned_headers):
        run_index = assigned_headers[header_index]
        record, beam_id, count, b_mm, h_mm, _, _ = header_items[header_index]
        run = candidate_runs[run_index]
        top_middle, bottom, waist, stirrup = resolve_strip_texts(record, run.orientation, beam_id)
        strip_key = (run.orientation, run.strip_coord_mm, run.start_coord_mm, run.end_coord_mm)
        strips[strip_key] = BeamStrip(
            beam_id=beam_id,
            count=count,
            orientation=run.orientation,
            strip_coord_mm=run.strip_coord_mm,
            section_b_mm=b_mm,
            section_h_mm=h_mm,
            top_middle_text=top_middle,
            bottom_text=bottom,
            waist_text=waist,
            stirrup_text=stirrup,
        )
        for beam_key in run.beam_keys:
            if beam_key in beam_to_strip_key:
                raise RuntimeError(f"story {story_index}: beam {beam_key} assigned to multiple strips")
            beam_to_strip_key[beam_key] = strip_key

    if len(beam_to_strip_key) != len(beam_map):
        missing = set(beam_map) - set(beam_to_strip_key)
        for orientation, groups in (
            (
                "H",
                {
                    strip_y: sorted(
                        [beam_key for beam_key in missing if beam_map[beam_key]["y1_mm"] == beam_map[beam_key]["y2_mm"] == strip_y],
                        key=lambda beam_key: beam_map[beam_key]["x1_mm"],
                    )
                    for strip_y in sorted({beam_map[beam_key]["y1_mm"] for beam_key in missing if beam_map[beam_key]["y1_mm"] == beam_map[beam_key]["y2_mm"]})
                },
            ),
            (
                "V",
                {
                    strip_x: sorted(
                        [beam_key for beam_key in missing if beam_map[beam_key]["x1_mm"] == beam_map[beam_key]["x2_mm"] == strip_x],
                        key=lambda beam_key: beam_map[beam_key]["y1_mm"],
                    )
                    for strip_x in sorted({beam_map[beam_key]["x1_mm"] for beam_key in missing if beam_map[beam_key]["x1_mm"] == beam_map[beam_key]["x2_mm"]})
                },
            ),
        ):
            for strip_coord_mm, beam_keys in groups.items():
                if not beam_keys:
                    continue
                start_coord_mm = beam_map[beam_keys[0]]["x1_mm"] if orientation == "H" else beam_map[beam_keys[0]]["y1_mm"]
                end_coord_mm = beam_map[beam_keys[-1]]["x2_mm"] if orientation == "H" else beam_map[beam_keys[-1]]["y2_mm"]
                parallel_candidates = [
                    (abs(source.strip_coord_mm - strip_coord_mm), source_key, source)
                    for source_key, source in strips.items()
                    if source.orientation == orientation
                    and source.count == len(beam_keys)
                    and source_key[2] == start_coord_mm
                    and source_key[3] == end_coord_mm
                ]
                if not parallel_candidates:
                    continue
                _, _, source = min(parallel_candidates, key=lambda item: item[0])
                strip_key = (orientation, strip_coord_mm, start_coord_mm, end_coord_mm)
                strips[strip_key] = BeamStrip(
                    beam_id=source.beam_id,
                    count=source.count,
                    orientation=orientation,
                    strip_coord_mm=strip_coord_mm,
                    section_b_mm=source.section_b_mm,
                    section_h_mm=source.section_h_mm,
                    top_middle_text=source.top_middle_text,
                    bottom_text=source.bottom_text,
                    waist_text=source.waist_text,
                    stirrup_text=source.stirrup_text,
                )
                for beam_key in beam_keys:
                    beam_to_strip_key[beam_key] = strip_key
        missing = sorted(set(beam_map) - set(beam_to_strip_key))
        if not missing:
            return strips, beam_to_strip_key
        raise RuntimeError(f"story {story_index}: some beams are not covered by strip headers: {missing}")
    return strips, beam_to_strip_key


def assign_midspan_texts(
    story_index: int,
    beam_map: Dict[int, dict],
    translation: Tuple[float, float],
) -> Dict[int, str]:
    beam_path = ROOT / f"Beam{story_index + 1}.T"
    inplace_records = parse_text_records(beam_path, b"$BP_INPLACE")
    dx, dy = translation
    result = {beam_key: "" for beam_key in beam_map}

    for record in inplace_records:
        text = canonicalize_support_text_v2(canonicalize_beam_text(record.raw))
        if not text or text.startswith("N") or "@" in text or "/" in text:
            continue
        model_x = record.x - dx
        model_y = record.y - dy
        best = None
        for beam_key, beam in beam_map.items():
            x1, y1, x2, y2 = beam["x1_mm"], beam["y1_mm"], beam["x2_mm"], beam["y2_mm"]
            length = math.hypot(x2 - x1, y2 - y1)
            if y1 == y2:
                along = model_x - x1
                perp = abs(model_y - y1)
            else:
                along = model_y - y1
                perp = abs(model_x - x1)
            if along < 1200 or along > length - 1200:
                continue
            if perp > 1500:
                continue
            score = perp * 10.0 + abs(along - length / 2.0)
            if best is None or score < best[0]:
                best = (score, beam_key)
        if best is None:
            continue
        beam_key = best[1]
        current = result[beam_key]
        if current and current != text:
            if len(text) > len(current):
                result[beam_key] = text
        else:
            result[beam_key] = text
    return result


def mirrored_beam_keys(
    beam_key: int,
    beam_map: Dict[int, dict],
    beam_strips: Dict[Tuple[str, int, int, int], BeamStrip],
    beam_to_strip_key: Dict[int, Tuple[str, int, int, int]],
) -> List[int]:
    min_x = min(min(beam["x1_mm"], beam["x2_mm"]) for beam in beam_map.values())
    max_x = max(max(beam["x1_mm"], beam["x2_mm"]) for beam in beam_map.values())
    min_y = min(min(beam["y1_mm"], beam["y2_mm"]) for beam in beam_map.values())
    max_y = max(max(beam["y1_mm"], beam["y2_mm"]) for beam in beam_map.values())
    center_x2 = min_x + max_x
    center_y2 = min_y + max_y
    beam_by_geometry = {
        (beam["x1_mm"], beam["y1_mm"], beam["x2_mm"], beam["y2_mm"]): current_key
        for current_key, beam in beam_map.items()
    }

    beam = beam_map[beam_key]
    strip = beam_strips[beam_to_strip_key[beam_key]]
    mirrors = []
    for coords in (
        (center_x2 - beam["x2_mm"], beam["y1_mm"], center_x2 - beam["x1_mm"], beam["y2_mm"]),
        (beam["x1_mm"], center_y2 - beam["y2_mm"], beam["x2_mm"], center_y2 - beam["y1_mm"]),
    ):
        other_key = beam_by_geometry.get(coords)
        if other_key is None or other_key == beam_key:
            continue
        other_strip = beam_strips[beam_to_strip_key[other_key]]
        if (
            other_strip.orientation == strip.orientation
            and other_strip.beam_id == strip.beam_id
            and other_strip.section_b_mm == strip.section_b_mm
            and other_strip.section_h_mm == strip.section_h_mm
        ):
            mirrors.append(other_key)
    return mirrors


def beam_length_mm(beam: dict) -> float:
    return math.hypot(beam["x2_mm"] - beam["x1_mm"], beam["y2_mm"] - beam["y1_mm"])


def same_parallel_beam_group(
    current_key: int,
    candidate_key: int,
    beam_map: Dict[int, dict],
    beam_strips: Dict[Tuple[str, int, int, int], BeamStrip],
    beam_to_strip_key: Dict[int, Tuple[str, int, int, int]],
) -> bool:
    current = beam_map[current_key]
    candidate = beam_map[candidate_key]
    current_strip = beam_strips[beam_to_strip_key[current_key]]
    candidate_strip = beam_strips[beam_to_strip_key[candidate_key]]
    if (
        current_strip.orientation != candidate_strip.orientation
        or current_strip.beam_id != candidate_strip.beam_id
        or current_strip.section_b_mm != candidate_strip.section_b_mm
        or current_strip.section_h_mm != candidate_strip.section_h_mm
        or not math.isclose(beam_length_mm(current), beam_length_mm(candidate), abs_tol=1.0)
    ):
        return False
    if current_strip.orientation == "H":
        return current["x1_mm"] == candidate["x1_mm"] and current["x2_mm"] == candidate["x2_mm"]
    return current["y1_mm"] == candidate["y1_mm"] and current["y2_mm"] == candidate["y2_mm"]


def previous_parallel_beam_keys(
    current_key: int,
    beam_map: Dict[int, dict],
    beam_strips: Dict[Tuple[str, int, int, int], BeamStrip],
    beam_to_strip_key: Dict[int, Tuple[str, int, int, int]],
) -> List[int]:
    current = beam_map[current_key]
    current_strip = beam_strips[beam_to_strip_key[current_key]]
    candidates: List[Tuple[int, int]] = []
    for candidate_key in beam_map:
        if candidate_key == current_key or not same_parallel_beam_group(
            current_key, candidate_key, beam_map, beam_strips, beam_to_strip_key
        ):
            continue
        candidate = beam_map[candidate_key]
        if current_strip.orientation == "H":
            delta = current["y1_mm"] - candidate["y1_mm"]
        else:
            delta = current["x1_mm"] - candidate["x1_mm"]
        if delta > 0:
            candidates.append((delta, candidate_key))
    return [candidate_key for _, candidate_key in sorted(candidates)]


def ordered_parallel_resolution_keys(
    beam_map: Dict[int, dict],
    beam_strips: Dict[Tuple[str, int, int, int], BeamStrip],
    beam_to_strip_key: Dict[int, Tuple[str, int, int, int]],
) -> List[int]:
    return sorted(
        beam_map,
        key=lambda key: (
            0 if beam_strips[beam_to_strip_key[key]].orientation == "H" else 1,
            beam_map[key]["y1_mm"] if beam_strips[beam_to_strip_key[key]].orientation == "H" else beam_map[key]["x1_mm"],
            beam_map[key]["x1_mm"] if beam_strips[beam_to_strip_key[key]].orientation == "H" else beam_map[key]["y1_mm"],
        ),
    )


def assign_middle_bottom_texts(
    story_index: int,
    beam_map: Dict[int, dict],
    beam_strips: Dict[Tuple[str, int, int, int], BeamStrip],
    beam_to_strip_key: Dict[int, Tuple[str, int, int, int]],
    translation: Tuple[float, float],
) -> Dict[int, Dict[str, str]]:
    beam_path = ROOT / f"Beam{story_index + 1}.T"
    inplace_records = parse_text_records(beam_path, b"$BP_INPLACE")
    dx, dy = translation
    direct: Dict[int, Dict[str, str]] = {
        beam_key: {"top_middle": "", "bottom": "", "top_middle_source": "", "bottom_source": ""}
        for beam_key in beam_map
    }

    def choose_text(texts: Sequence[str]) -> str:
        if not texts:
            return ""
        counts: Dict[str, int] = {}
        for text in texts:
            counts[text] = counts.get(text, 0) + 1
        return max(counts.items(), key=lambda item: (item[1], len(item[0]), item[0]))[0]

    candidates_by_beam: Dict[int, List[dict]] = {beam_key: [] for beam_key in beam_map}
    loose_bottom_candidates: Dict[int, List[dict]] = {beam_key: [] for beam_key in beam_map}
    for record in inplace_records:
        raw_text = canonicalize_beam_text(record.raw)
        text = canonicalize_support_text_v2(raw_text)
        if not text or text.startswith("N") or "@" in text:
            continue
        model_x = record.x - dx
        model_y = record.y - dy
        angle_mod = abs(record.angle_deg) % 180.0

        for beam_key, beam in beam_map.items():
            strip = beam_strips[beam_to_strip_key[beam_key]]
            expected_angle = 0.0 if strip.orientation == "H" else 90.0
            if not math.isclose(angle_mod, expected_angle, abs_tol=5.0):
                continue

            x1, y1, x2, y2 = beam["x1_mm"], beam["y1_mm"], beam["x2_mm"], beam["y2_mm"]
            length = math.hypot(x2 - x1, y2 - y1)
            if strip.orientation == "H":
                along = model_x - x1
                offset = model_y - y1
            else:
                along = model_y - y1
                offset = model_x - x1
            if along < 0.25 * length or along > 0.75 * length:
                continue
            if abs(offset) > 1800.0:
                continue
            candidates_by_beam[beam_key].append(
                {
                    "text": text,
                    "along": along,
                    "offset": offset,
                    "score": abs(along - length / 2.0) + abs(offset) * 0.1,
                }
            )

        if "/" in raw_text:
            continue
        for beam_key, beam in beam_map.items():
            strip = beam_strips[beam_to_strip_key[beam_key]]
            x1, y1, x2, y2 = beam["x1_mm"], beam["y1_mm"], beam["x2_mm"], beam["y2_mm"]
            length = math.hypot(x2 - x1, y2 - y1)
            if strip.orientation == "H":
                along = model_x - x1
                offset = model_y - y1
            else:
                along = model_y - y1
                offset = model_x - x1
            if along < -1200.0 or along > length + 1200.0:
                continue
            if abs(offset) > 2800.0:
                continue
            loose_bottom_candidates[beam_key].append(
                {
                    "text": text,
                    "score": abs(along - length / 2.0) + abs(offset) * 0.1,
                }
            )

    for beam_key, candidates in candidates_by_beam.items():
        if not candidates:
            continue
        strip = beam_strips[beam_to_strip_key[beam_key]]
        candidates = sorted(candidates, key=lambda item: item["score"])
        anchor_along = candidates[0]["along"]
        clustered = [item for item in candidates if abs(item["along"] - anchor_along) <= 900.0]
        if len(clustered) >= 2:
            ordered = sorted(clustered, key=lambda item: item["offset"])
            if strip.orientation == "H":
                direct[beam_key]["bottom"] = choose_text([ordered[0]["text"]])
                direct[beam_key]["top_middle"] = choose_text([ordered[-1]["text"]])
            else:
                direct[beam_key]["top_middle"] = choose_text([ordered[0]["text"]])
                direct[beam_key]["bottom"] = choose_text([ordered[-1]["text"]])
            direct[beam_key]["top_middle_source"] = "direct"
            direct[beam_key]["bottom_source"] = "direct"
            continue

        only = clustered[0]
        if strip.orientation == "H":
            role = "top_middle" if only["offset"] > 0 else "bottom"
        else:
            # Rotated text anchors are not the visual center; a single midspan Y-text
            # in these drawings denotes the bottom line while the header supplies top.
            role = "bottom"
        direct[beam_key][role] = only["text"]
        direct[beam_key][f"{role}_source"] = "direct"

    for beam_key, candidates in loose_bottom_candidates.items():
        strip = beam_strips[beam_to_strip_key[beam_key]]
        if direct[beam_key]["bottom"] or strip.bottom_text or not candidates:
            continue
        edge_candidates: List[dict] = []
        beam = beam_map[beam_key]
        x1, y1, x2, y2 = beam["x1_mm"], beam["y1_mm"], beam["x2_mm"], beam["y2_mm"]
        length = math.hypot(x2 - x1, y2 - y1)
        for candidate in candidates:
            if candidate["score"] <= length:
                edge_candidates.append(candidate)
        candidates = sorted(edge_candidates or candidates, key=lambda item: item["score"])
        direct[beam_key]["bottom"] = candidates[0]["text"]
        direct[beam_key]["bottom_source"] = "loose"

    result: Dict[int, Dict[str, str]] = {}
    for beam_key in beam_map:
        strip = beam_strips[beam_to_strip_key[beam_key]]
        result[beam_key] = {
            "top_middle": direct[beam_key]["top_middle"] or strip.top_middle_text,
            "bottom": direct[beam_key]["bottom"] or strip.bottom_text,
            "top_middle_source": direct[beam_key]["top_middle_source"] or ("header" if strip.top_middle_text else ""),
            "bottom_source": direct[beam_key]["bottom_source"] or ("header" if strip.bottom_text else ""),
        }

    for beam_key in ordered_parallel_resolution_keys(beam_map, beam_strips, beam_to_strip_key):
        for field in ("top_middle", "bottom"):
            source_key = f"{field}_source"
            if result[beam_key].get(source_key) == "direct":
                continue
            for candidate_key in previous_parallel_beam_keys(beam_key, beam_map, beam_strips, beam_to_strip_key):
                candidate = result[candidate_key]
                if candidate.get(source_key) not in {"direct", "parallel"} or not candidate.get(field):
                    continue
                result[beam_key][field] = candidate[field]
                result[beam_key][source_key] = "parallel"
                break

    for beam_key in beam_map:
        for mirror_key in mirrored_beam_keys(beam_key, beam_map, beam_strips, beam_to_strip_key):
            mirror = result[mirror_key]
            for field in ("top_middle", "bottom"):
                source_key = f"{field}_source"
                current_source = result[beam_key].get(source_key, "")
                mirror_source = mirror.get(source_key, "")
                if current_source not in {"", "loose"}:
                    continue
                if mirror_source in {"", "loose"} or not mirror.get(field):
                    continue
                result[beam_key][field] = mirror[field]
                result[beam_key][source_key] = "mirror"
    return result


def assign_support_texts(
    story_index: int,
    beam_map: Dict[int, dict],
    beam_strips: Dict[Tuple[str, int, int, int], BeamStrip],
    beam_to_strip_key: Dict[int, Tuple[str, int, int, int]],
    local_to_coord: Dict[int, Tuple[int, int]],
    translation: Tuple[float, float],
) -> Dict[int, Dict[str, str]]:
    beam_path = ROOT / f"Beam{story_index + 1}.T"
    inplace_records = parse_text_records(beam_path, b"$BP_INPLACE")
    dx, dy = translation
    assignments = {beam_key: {"I": "", "J": ""} for beam_key in beam_map}

    def choose_text(texts: Sequence[str]) -> str:
        if not texts:
            return ""
        counts: Dict[str, int] = {}
        for text in texts:
            counts[text] = counts.get(text, 0) + 1
        return max(counts.items(), key=lambda item: (item[1], len(item[0]), item[0]))[0]

    node_coords = list(local_to_coord.values())
    if not node_coords:
        return assignments

    node_groups: Dict[Tuple[int, int], Dict[str, Dict[str, List[Tuple[int, str, str]]]]] = {}
    for beam_key, beam in beam_map.items():
        strip = beam_strips[beam_to_strip_key[beam_key]]
        orientation = strip.orientation
        beam_id = strip.beam_id

        node_i = (beam["x1_mm"], beam["y1_mm"])
        node_j = (beam["x2_mm"], beam["y2_mm"])
        if orientation == "H":
            node_groups.setdefault(node_i, {}).setdefault("H", {}).setdefault("right", []).append((beam_key, "I", beam_id))
            node_groups.setdefault(node_j, {}).setdefault("H", {}).setdefault("left", []).append((beam_key, "J", beam_id))
        else:
            node_groups.setdefault(node_i, {}).setdefault("V", {}).setdefault("up", []).append((beam_key, "I", beam_id))
            node_groups.setdefault(node_j, {}).setdefault("V", {}).setdefault("down", []).append((beam_key, "J", beam_id))

    text_groups: Dict[Tuple[Tuple[int, int], str, str], List[str]] = {}
    node_orientation_records: Dict[Tuple[Tuple[int, int], str], List[dict]] = {}
    node_text_pool: Dict[Tuple[int, int], List[str]] = {}
    for record in inplace_records:
        raw_text = canonicalize_beam_text(record.raw)
        text = canonicalize_support_text_v2(raw_text)
        if not text or text.startswith("N") or "@" in text:
            continue
        model_x = record.x - dx
        model_y = record.y - dy
        nearest = min(node_coords, key=lambda coord: math.hypot(coord[0] - model_x, coord[1] - model_y))
        delta_x = model_x - nearest[0]
        delta_y = model_y - nearest[1]
        distance = math.hypot(delta_x, delta_y)
        if distance > 1800.0:
            continue

        angle_mod = abs(record.angle_deg) % 180.0
        if math.isclose(angle_mod, 90.0, abs_tol=5.0):
            orientation = "V"
        elif math.isclose(angle_mod, 0.0, abs_tol=5.0):
            orientation = "H"
        else:
            orientation = "H" if abs(delta_x) > abs(delta_y) else "V"
        if orientation == "H":
            side = "left" if delta_x < 0 else "right"
        else:
            side = "down" if delta_y < 0 else "up"
        text_groups.setdefault((nearest, orientation, side), []).append(text)
        text_groups.setdefault((nearest, orientation, "any"), []).append(text)
        node_orientation_records.setdefault((nearest, orientation), []).append(
            {
                "text": text,
                "model_x": model_x,
                "model_y": model_y,
                "delta_x": delta_x,
                "delta_y": delta_y,
            }
        )
        node_text_pool.setdefault(nearest, []).append(text)

    opposite_side = {
        ("H", "left"): "right",
        ("H", "right"): "left",
        ("V", "down"): "up",
        ("V", "up"): "down",
    }

    for node_coord, orientation_groups in node_groups.items():
        for orientation, side_groups in orientation_groups.items():
            records = node_orientation_records.get((node_coord, orientation), [])
            if len(side_groups) == 1:
                side = next(iter(side_groups))
                text = choose_text([record["text"] for record in records]) or choose_text(text_groups.get((node_coord, orientation, "any"), []))
                if text:
                    for beam_key, endpoint, _ in side_groups[side]:
                        assignments[beam_key][endpoint] = text
                continue

            side_texts = {side: "" for side in side_groups}
            if len(records) >= 2:
                if orientation == "H":
                    ordered_records = sorted(records, key=lambda item: item["model_x"])
                    left_bucket_x = ordered_records[0]["model_x"]
                    right_bucket_x = ordered_records[-1]["model_x"]
                    side_texts["left"] = choose_text([item["text"] for item in ordered_records if abs(item["model_x"] - left_bucket_x) <= 250.0])
                    side_texts["right"] = choose_text([item["text"] for item in ordered_records if abs(item["model_x"] - right_bucket_x) <= 250.0])
                else:
                    ordered_records = sorted(records, key=lambda item: item["model_y"])
                    down_bucket_y = ordered_records[0]["model_y"]
                    up_bucket_y = ordered_records[-1]["model_y"]
                    side_texts["down"] = choose_text([item["text"] for item in ordered_records if abs(item["model_y"] - down_bucket_y) <= 250.0])
                    side_texts["up"] = choose_text([item["text"] for item in ordered_records if abs(item["model_y"] - up_bucket_y) <= 250.0])
            else:
                side_texts = {
                    side: choose_text(text_groups.get((node_coord, orientation, side), []))
                    for side in side_groups
                }
            for side, endpoints in side_groups.items():
                text = side_texts.get(side, "")
                if text:
                    for beam_key, endpoint, _ in endpoints:
                        assignments[beam_key][endpoint] = text
                    continue

                other_side = opposite_side[(orientation, side)]
                other_text = side_texts.get(other_side, "")
                other_endpoints = side_groups.get(other_side, [])
                if not other_text or not endpoints or not other_endpoints:
                    continue
                if len(endpoints) != 1 or len(other_endpoints) != 1:
                    continue
                if endpoints[0][2] != other_endpoints[0][2]:
                    continue
                beam_key, endpoint, _ = endpoints[0]
                assignments[beam_key][endpoint] = other_text

    strips_by_group: Dict[Tuple[str, str], Dict[Tuple[str, int, int, int], List[int]]] = {}
    for beam_key, beam in beam_map.items():
        strip_key = beam_to_strip_key[beam_key]
        strip = beam_strips[strip_key]
        strips_by_group.setdefault((strip.orientation, strip.beam_id), {}).setdefault(strip_key, []).append(beam_key)

    for (orientation, _beam_id), strips in strips_by_group.items():
        ordered_strips: Dict[Tuple[str, int, int, int], List[int]] = {}
        for strip_key, beam_keys in strips.items():
            ordered_strips[strip_key] = sorted(
                beam_keys,
                key=lambda beam_key: beam_map[beam_key]["x1_mm"] if orientation == "H" else beam_map[beam_key]["y1_mm"],
            )

        for beam_keys in ordered_strips.values():
            comparable = [
                source
                for source in ordered_strips.values()
                if source is not beam_keys and len(source) == len(beam_keys)
            ]
            if not comparable:
                continue
            for index, beam_key in enumerate(beam_keys):
                if not assignments[beam_key]["I"]:
                    candidates = [assignments[source[index]]["I"] for source in comparable if assignments[source[index]]["I"]]
                    fill = choose_text(candidates)
                    if fill:
                        assignments[beam_key]["I"] = fill
                if not assignments[beam_key]["J"]:
                    candidates = [assignments[source[index]]["J"] for source in comparable if assignments[source[index]]["J"]]
                    fill = choose_text(candidates)
                    if fill:
                        assignments[beam_key]["J"] = fill

    for node_coord, orientation_groups in node_groups.items():
        pooled_texts = node_text_pool.get(node_coord, [])
        unique_texts = sorted(set(pooled_texts))
        if len(unique_texts) != 1:
            continue
        fallback_text = unique_texts[0]
        for orientation, side_groups in orientation_groups.items():
            if any(text_groups.get((node_coord, orientation, side), []) for side in side_groups):
                continue
            sides = list(side_groups.keys())
            if len(sides) == 1:
                for beam_key, endpoint, _ in side_groups[sides[0]]:
                    if not assignments[beam_key][endpoint]:
                        assignments[beam_key][endpoint] = fallback_text
                continue
            if len(sides) != 2:
                continue
            first_side, second_side = sides
            first_endpoints = side_groups[first_side]
            second_endpoints = side_groups[second_side]
            if len(first_endpoints) == 1 and len(second_endpoints) == 1 and first_endpoints[0][2] == second_endpoints[0][2]:
                for beam_key, endpoint, _ in first_endpoints + second_endpoints:
                    if not assignments[beam_key][endpoint]:
                        assignments[beam_key][endpoint] = fallback_text

    for (orientation, beam_id), strips in strips_by_group.items():
        for strip_key, beam_keys in strips.items():
            ordered_beam_keys = sorted(
                beam_keys,
                key=lambda beam_key: beam_map[beam_key]["x1_mm"] if orientation == "H" else beam_map[beam_key]["y1_mm"],
            )
            node_texts: Dict[int, str] = {}
            for index, beam_key in enumerate(ordered_beam_keys):
                left_text = assignments[beam_key]["I"]
                right_text = assignments[beam_key]["J"]
                left_node = beam_map[beam_key]["x1_mm"] if orientation == "H" else beam_map[beam_key]["y1_mm"]
                right_node = beam_map[beam_key]["x2_mm"] if orientation == "H" else beam_map[beam_key]["y2_mm"]
                if left_text:
                    node_texts[left_node] = left_text
                if right_text:
                    node_texts[right_node] = right_text

            ordered_nodes = [
                beam_map[ordered_beam_keys[0]]["x1_mm"] if orientation == "H" else beam_map[ordered_beam_keys[0]]["y1_mm"]
            ] + [
                beam_map[beam_key]["x2_mm"] if orientation == "H" else beam_map[beam_key]["y2_mm"]
                for beam_key in ordered_beam_keys
            ]
            for index in range(1, len(ordered_nodes) - 1):
                node_coord = ordered_nodes[index]
                if node_coord in node_texts:
                    continue
                left_text = node_texts.get(ordered_nodes[index - 1], "")
                right_text = node_texts.get(ordered_nodes[index + 1], "")
                if not left_text or left_text != right_text:
                    continue
                left_beam = ordered_beam_keys[index - 1]
                right_beam = ordered_beam_keys[index]
                if not assignments[left_beam]["J"]:
                    assignments[left_beam]["J"] = left_text
                if not assignments[right_beam]["I"]:
                    assignments[right_beam]["I"] = left_text

    for (orientation, _beam_id), strips in strips_by_group.items():
        ordered_strips = [
            sorted(
                beam_keys,
                key=lambda beam_key: beam_map[beam_key]["x1_mm"] if orientation == "H" else beam_map[beam_key]["y1_mm"],
            )
            for beam_keys in strips.values()
        ]
        for beam_keys in ordered_strips:
            comparable = [source for source in ordered_strips if source is not beam_keys and len(source) == len(beam_keys)]
            if not comparable:
                continue
            for index, beam_key in enumerate(beam_keys):
                if not assignments[beam_key]["I"]:
                    fill = choose_text([assignments[source[index]]["I"] for source in comparable if assignments[source[index]]["I"]])
                    if fill:
                        assignments[beam_key]["I"] = fill
                if not assignments[beam_key]["J"]:
                    fill = choose_text([assignments[source[index]]["J"] for source in comparable if assignments[source[index]]["J"]])
                    if fill:
                        assignments[beam_key]["J"] = fill

    for beam_keys in strips_by_group.values():
        for strip_beam_keys in beam_keys.values():
            ordered_beam_keys = sorted(
                strip_beam_keys,
                key=lambda beam_key: (
                    beam_map[beam_key]["x1_mm"],
                    beam_map[beam_key]["y1_mm"],
                    beam_map[beam_key]["x2_mm"],
                    beam_map[beam_key]["y2_mm"],
                ),
            )
            first_beam = ordered_beam_keys[0]
            last_beam = ordered_beam_keys[-1]
            if not assignments[first_beam]["I"] and assignments[first_beam]["J"]:
                assignments[first_beam]["I"] = assignments[first_beam]["J"]
            if not assignments[last_beam]["J"] and assignments[last_beam]["I"]:
                assignments[last_beam]["J"] = assignments[last_beam]["I"]

    for (_orientation, _beam_id), strips in strips_by_group.items():
        for strip_key, strip_beam_keys in strips.items():
            strip = beam_strips[strip_key]
            ordered_beam_keys = sorted(
                strip_beam_keys,
                key=lambda beam_key: beam_map[beam_key]["x1_mm"] if strip.orientation == "H" else beam_map[beam_key]["y1_mm"],
            )
            if not ordered_beam_keys:
                continue
            node_positions = [
                beam_map[ordered_beam_keys[0]]["x1_mm"] if strip.orientation == "H" else beam_map[ordered_beam_keys[0]]["y1_mm"]
            ] + [
                beam_map[beam_key]["x2_mm"] if strip.orientation == "H" else beam_map[beam_key]["y2_mm"]
                for beam_key in ordered_beam_keys
            ]
            node_texts: Dict[int, str] = {}
            for index, beam_key in enumerate(ordered_beam_keys):
                if assignments[beam_key]["I"]:
                    node_texts[node_positions[index]] = assignments[beam_key]["I"]
                if assignments[beam_key]["J"]:
                    node_texts[node_positions[index + 1]] = assignments[beam_key]["J"]
            if not node_texts:
                continue
            known_positions = sorted(node_texts)
            for index, beam_key in enumerate(ordered_beam_keys):
                for endpoint, node_position in (("I", node_positions[index]), ("J", node_positions[index + 1])):
                    if assignments[beam_key][endpoint]:
                        continue
                    nearest_position = min(known_positions, key=lambda position: (abs(position - node_position), position))
                    assignments[beam_key][endpoint] = node_texts[nearest_position]
    return assignments


def story_has_support_texts(story_index: int) -> bool:
    beam_path = ROOT / f"Beam{story_index + 1}.T"
    for record in parse_text_records(beam_path, b"$BP_INPLACE"):
        text = canonicalize_support_text_v2(canonicalize_beam_text(record.raw))
        if text and not text.startswith("N") and "@" not in text:
            return True
    return False


def assign_beam_waist_texts(
    story_index: int,
    beam_map: Dict[int, dict],
    translation: Tuple[float, float],
) -> Dict[int, str]:
    beam_path = ROOT / f"Beam{story_index + 1}.T"
    inplace_records = parse_text_records(beam_path, b"$BP_INPLACE")
    dx, dy = translation
    result = {beam_key: "" for beam_key in beam_map}

    for record in inplace_records:
        raw_text = canonicalize_beam_text(record.raw)
        if not raw_text.startswith("N"):
            continue
        text = canonicalize_support_text_v2(raw_text)
        if not text:
            continue
        model_x = record.x - dx
        model_y = record.y - dy
        best = None
        for beam_key, beam in beam_map.items():
            x1, y1, x2, y2 = beam["x1_mm"], beam["y1_mm"], beam["x2_mm"], beam["y2_mm"]
            length = math.hypot(x2 - x1, y2 - y1)
            if y1 == y2:
                along = model_x - x1
                perp = abs(model_y - y1)
            else:
                along = model_y - y1
                perp = abs(model_x - x1)
            if along < 1200 or along > length - 1200:
                continue
            if perp > 2000:
                continue
            score = perp * 10.0 + abs(along - length / 2.0)
            if best is None or score < best[0]:
                best = (score, beam_key)
        if best is None:
            continue
        beam_key = best[1]
        current = result[beam_key]
        if current and current != text:
            if len(text) > len(current):
                result[beam_key] = text
        else:
            result[beam_key] = text
    return result


def resolve_waist_texts(
    beam_map: Dict[int, dict],
    beam_strips: Dict[Tuple[str, int, int, int], BeamStrip],
    beam_to_strip_key: Dict[int, Tuple[str, int, int, int]],
    beam_waist_texts: Dict[int, str],
) -> Dict[int, str]:
    resolved = {}
    sources = {}
    for beam_key in beam_map:
        strip = beam_strips[beam_to_strip_key[beam_key]]
        if strip.waist_text != "0":
            resolved[beam_key] = strip.waist_text
            sources[beam_key] = "direct"
        elif beam_waist_texts[beam_key]:
            resolved[beam_key] = beam_waist_texts[beam_key]
            sources[beam_key] = "direct"
        else:
            resolved[beam_key] = "0"
            sources[beam_key] = ""

    for beam_key in ordered_parallel_resolution_keys(beam_map, beam_strips, beam_to_strip_key):
        if resolved[beam_key] != "0":
            continue
        for candidate_key in previous_parallel_beam_keys(beam_key, beam_map, beam_strips, beam_to_strip_key):
            if sources.get(candidate_key) not in {"direct", "parallel"}:
                continue
            candidate_text = resolved.get(candidate_key, "0")
            if candidate_text == "0":
                continue
            resolved[beam_key] = candidate_text
            sources[beam_key] = "parallel"
            break
    return resolved


def build_story_output(
    story_index: int,
    story_nodes: Dict[str, object],
    node_map: Dict[int, dict],
    beam_map: Dict[int, dict],
    column_types: Dict[Tuple[int, int], str],
    column_details: Dict[str, dict],
    beam_strips: Dict[Tuple[str, int, int, int], BeamStrip],
    beam_to_strip_key: Dict[int, Tuple[str, int, int, int]],
    support_texts: Dict[int, Dict[str, str]],
    middle_bottom_texts: Dict[int, Dict[str, str]],
    resolved_waist_texts: Dict[int, str],
    column_dims: Tuple[int, int],
    require_support_texts: bool,
) -> Dict[str, dict]:
    points: Sequence[ColumnPoint] = story_nodes["points"]  # type: ignore[assignment]
    local_node_by_coord: Dict[Tuple[int, int], int] = story_nodes["local_node_by_coord"]  # type: ignore[assignment]
    z_bottom_mm = int(story_nodes["z_bottom_mm"])  # type: ignore[arg-type]
    z_top_mm = int(story_nodes["z_top_mm"])  # type: ignore[arg-type]

    columns: Dict[str, dict] = {}
    for point in points:
        if (point.x_mm, point.y_mm) not in column_types:
            continue
        local_node_id = local_node_by_coord[(point.x_mm, point.y_mm)]
        global_node_id = node_map[local_node_id]["global_node_id"]
        column_type = column_types[(point.x_mm, point.y_mm)]
        detail = column_details[column_type]
        columns[str(local_node_id - 1)] = {
            "geometry": {
                "local_node_id": local_node_id,
                "global_node_id": global_node_id,
                "x": float(point.x_mm),
                "y": float(point.y_mm),
                "z_bottom": float(z_bottom_mm),
                "z_top": float(z_top_mm),
                "B": column_dims[0],
                "H": column_dims[1],
            },
            "reinforcement": {
                "column_id": column_type,
                "section": f"{column_dims[0]}x{column_dims[1]}",
                "elevation": detail["elevation"],
                "longitudinal_rebar": detail["longitudinal_rebar"],
                "stirrup": detail["stirrup"],
            },
        }

    beams: Dict[str, dict] = {}
    for beam_key, beam in beam_map.items():
        strip = beam_strips[beam_to_strip_key[beam_key]]
        orientation = strip.orientation
        x1, y1, x2, y2 = beam["x1_mm"], beam["y1_mm"], beam["x2_mm"], beam["y2_mm"]
        length = int(round(math.hypot(x2 - x1, y2 - y1)))
        half_i = column_dims[0] // 2 if orientation == "H" else column_dims[1] // 2
        half_j = column_dims[0] // 2 if orientation == "H" else column_dims[1] // 2
        clear_span = length - half_i - half_j
        top_left_length = half_i + int(round(clear_span / 3.0))
        top_right_length = half_j + int(round(clear_span / 3.0))
        top_middle_length = length - top_left_length - top_right_length
        dense_left_length = half_i + int(round(1.5 * strip.section_h_mm))
        dense_right_length = half_j + int(round(1.5 * strip.section_h_mm))
        non_dense_length = length - dense_left_length - dense_right_length
        top_left_text = support_texts[beam_key]["I"]
        top_right_text = support_texts[beam_key]["J"]
        top_middle_text = middle_bottom_texts[beam_key]["top_middle"]
        bottom_text = middle_bottom_texts[beam_key]["bottom"]
        waist_text = resolved_waist_texts[beam_key]
        if not all([top_middle_text, bottom_text]):
            raise RuntimeError(f"story {story_index}: incomplete beam reinforcement for beam {beam_key}")
        top_left_text = top_left_text or top_middle_text
        top_right_text = top_right_text or top_middle_text
        if require_support_texts and not all([top_left_text, top_right_text]):
            raise RuntimeError(f"story {story_index}: incomplete beam support reinforcement for beam {beam_key}")
        stirrup_text, dense_text, non_dense_text, limbs = parse_stirrup_payload(strip.stirrup_text)
        beams[str(beam_key)] = {
            "geometry": {
                "global_node_I": beam["global_node_I"],
                "global_node_J": beam["global_node_J"],
                "local_node_I": beam["local_node_I"],
                "local_node_J": beam["local_node_J"],
                "x1": float(x1),
                "y1": float(y1),
                "x2": float(x2),
                "y2": float(y2),
                "z": float(beam["z_mm"]),
                "B": strip.section_b_mm,
                "H": strip.section_h_mm,
                "length": float(length),
            },
            "reinforcement": {
                "beam_id": strip.beam_id,
                "section": f"{strip.section_b_mm}x{strip.section_h_mm}",
                "top_left": {"text": top_left_text, "length": top_left_length},
                "top_middle": {"text": top_middle_text, "length": top_middle_length},
                "top_right": {"text": top_right_text, "length": top_right_length},
                "bottom": {"text": bottom_text, "length": length},
                "waist": {"text": waist_text},
                "stirrup": {
                    "text": stirrup_text,
                    "dense_text": dense_text,
                    "dense_left_length": dense_left_length,
                    "dense_right_length": dense_right_length,
                    "non_dense_text": non_dense_text,
                    "non_dense_length": non_dense_length,
                    "limbs": limbs,
                },
            },
        }
    return {"columns": columns, "beams": beams}


def validate_output(structure: dict) -> None:
    stories = structure["design_results"]["story"]
    if len(stories) != 6:
        raise RuntimeError(f"expected 6 stories, got {len(stories)}")
    for story_key, story in stories.items():
        if not isinstance(story["columns"], dict) or not isinstance(story["beams"], dict):
            raise RuntimeError(f"story {story_key}: beams/columns must remain dict containers")
        if len(story["columns"]) != 37:
            raise RuntimeError(f"story {story_key}: expected 37 columns, got {len(story['columns'])}")
        if len(story["beams"]) != 60:
            raise RuntimeError(f"story {story_key}: expected 60 beams, got {len(story['beams'])}")
    text = json.dumps(structure, ensure_ascii=False)
    for field in FORBIDDEN_FIELDS:
        if f'"{field}"' in text:
            raise RuntimeError(f"forbidden field still present: {field}")
    if "朴" in text or "桅" in text:
        raise RuntimeError("output still contains undecoded reinforcement symbols")

    beam = structure["design_results"]["story"]["0"]["beams"]["8"]["reinforcement"]
    if beam["top_left"]["text"] != "4Φ25+2Φ22":
        raise RuntimeError(f'story 0 beam 8 top_left mismatch: {beam["top_left"]["text"]}')
    if beam["top_middle"]["text"] != "2Φ25+2Φ12":
        raise RuntimeError(f'story 0 beam 8 top_middle mismatch: {beam["top_middle"]["text"]}')
    if beam["top_right"]["text"] != "4Φ25+2Φ22":
        raise RuntimeError(f'story 0 beam 8 top_right mismatch: {beam["top_right"]["text"]}')
    if beam["bottom"]["text"] != "4Φ22":
        raise RuntimeError(f'story 0 beam 8 bottom mismatch: {beam["bottom"]["text"]}')
    if beam["waist"]["text"] != "0":
        raise RuntimeError(f'story 0 beam 8 waist mismatch: {beam["waist"]["text"]}')


def validate_output_v2(structure: dict) -> None:
    stories = structure["design_results"]["story"]
    if len(stories) != 6:
        raise RuntimeError(f"expected 6 stories, got {len(stories)}")
    for story_key, story in stories.items():
        if not isinstance(story["columns"], dict) or not isinstance(story["beams"], dict):
            raise RuntimeError(f"story {story_key}: beams/columns must remain dict containers")
        if len(story["columns"]) != 37:
            raise RuntimeError(f"story {story_key}: expected 37 columns, got {len(story['columns'])}")
        if len(story["beams"]) != 60:
            raise RuntimeError(f"story {story_key}: expected 60 beams, got {len(story['beams'])}")

    text = json.dumps(structure, ensure_ascii=False)
    for field in FORBIDDEN_FIELDS:
        if f'"{field}"' in text:
            raise RuntimeError(f"forbidden field still present: {field}")
    if "朴" in text or "桅" in text:
        raise RuntimeError("output still contains undecoded reinforcement symbols")

    geometry = structure["StructureGeometry"]
    if geometry["floor_area"] != 864.0:
        raise RuntimeError(f'floor_area mismatch: {geometry["floor_area"]}')
    if geometry["total_area"] != 5184.0:
        raise RuntimeError(f'total_area mismatch: {geometry["total_area"]}')

    materials = structure["MaterialProperties"]
    if materials["concrete_grade"] != "C30":
        raise RuntimeError(f'concrete_grade mismatch: {materials["concrete_grade"]}')
    if materials["steel_grade"] != "HRB400":
        raise RuntimeError(f'steel_grade mismatch: {materials["steel_grade"]}')
    if materials["fy"] != 360.0:
        raise RuntimeError(f'fy mismatch: {materials["fy"]}')
    if materials["concrete_cover_mm"] != 20.0:
        raise RuntimeError(f'concrete_cover_mm mismatch: {materials["concrete_cover_mm"]}')

    beam = stories["0"]["beams"]["8"]["reinforcement"]
    if beam["top_left"]["text"] != "4Φ25+2Φ22":
        raise RuntimeError(f'story 0 beam 8 top_left mismatch: {beam["top_left"]["text"]}')
    if beam["top_middle"]["text"] != "2Φ25+2Φ12":
        raise RuntimeError(f'story 0 beam 8 top_middle mismatch: {beam["top_middle"]["text"]}')
    if beam["top_right"]["text"] != "4Φ25+2Φ22":
        raise RuntimeError(f'story 0 beam 8 top_right mismatch: {beam["top_right"]["text"]}')
    if beam["bottom"]["text"] != "4Φ22":
        raise RuntimeError(f'story 0 beam 8 bottom mismatch: {beam["bottom"]["text"]}')
    if beam["waist"]["text"] != "0":
        raise RuntimeError(f'story 0 beam 8 waist mismatch: {beam["waist"]["text"]}')

    beam_12 = stories["0"]["beams"]["12"]["reinforcement"]
    if beam_12["top_left"]["text"] != "3Φ25+2Φ22":
        raise RuntimeError(f'story 0 beam 12 top_left mismatch: {beam_12["top_left"]["text"]}')
    if beam_12["top_middle"]["text"] != "2Φ25+2Φ12":
        raise RuntimeError(f'story 0 beam 12 top_middle mismatch: {beam_12["top_middle"]["text"]}')
    if beam_12["top_right"]["text"] != "3Φ25+2Φ22":
        raise RuntimeError(f'story 0 beam 12 top_right mismatch: {beam_12["top_right"]["text"]}')
    if beam_12["bottom"]["text"] != "4Φ22":
        raise RuntimeError(f'story 0 beam 12 bottom mismatch: {beam_12["bottom"]["text"]}')
    if beam_12["waist"]["text"] != "N4Φ12":
        raise RuntimeError(f'story 0 beam 12 waist mismatch: {beam_12["waist"]["text"]}')

    beam_4 = stories["0"]["beams"]["4"]["reinforcement"]
    if beam_4["top_right"]["text"] != "6Φ25":
        raise RuntimeError(f'story 0 beam 4 top_right mismatch: {beam_4["top_right"]["text"]}')

    beam_10 = stories["0"]["beams"]["10"]["reinforcement"]
    if beam_10["top_left"]["text"] != "4Φ25":
        raise RuntimeError(f'story 0 beam 10 top_left mismatch: {beam_10["top_left"]["text"]}')

    top_beam = stories["5"]["beams"]["2"]["reinforcement"]
    if top_beam["top_left"]["text"] != "2Φ18+2Φ16":
        raise RuntimeError(f'story 5 beam 2 top_left mismatch: {top_beam["top_left"]["text"]}')
    if top_beam["top_middle"]["text"] != "2Φ18+2Φ12":
        raise RuntimeError(f'story 5 beam 2 top_middle mismatch: {top_beam["top_middle"]["text"]}')
    if top_beam["top_right"]["text"] != "2Φ18+2Φ16":
        raise RuntimeError(f'story 5 beam 2 top_right mismatch: {top_beam["top_right"]["text"]}')
    if top_beam["bottom"]["text"] != "4Φ16":
        raise RuntimeError(f'story 5 beam 2 bottom mismatch: {top_beam["bottom"]["text"]}')
    if top_beam["waist"]["text"] != "N4Φ12":
        raise RuntimeError(f'story 5 beam 2 waist mismatch: {top_beam["waist"]["text"]}')


SAMPLE_EXPECTATIONS = {
    ".T - codex": [
        ("0", "8", "top_left", "4Φ25+2Φ22"),
        ("0", "8", "top_right", "4Φ25+2Φ22"),
        ("0", "8", "bottom", "4Φ22"),
        ("0", "8", "waist", "0"),
        ("0", "12", "top_left", "3Φ25+2Φ22"),
        ("0", "12", "top_right", "3Φ25+2Φ22"),
        ("0", "4", "top_right", "6Φ25"),
        ("0", "10", "top_left", "4Φ25"),
        ("5", "2", "top_left", "2Φ18+2Φ16"),
        ("5", "2", "top_right", "2Φ18+2Φ16"),
    ],
    "10层框架8_extracted": [
        ("0", "0", "top_middle", "2Φ25+2Φ12"),
        ("0", "0", "bottom", "2Φ25+2Φ20"),
        ("0", "12", "bottom", "3Φ25+2Φ20"),
        ("0", "37", "top_middle", "2Φ25+2Φ12"),
        ("0", "37", "bottom", "3Φ22+2Φ18"),
    ],
}


def validate_output_generic(
    structure: dict,
    expected_story_count: int,
    expected_column_counts: Dict[str, int],
    expected_beam_counts: Dict[str, int],
    beam_file_count: int,
    column_file_count: int,
    support_required_by_story: Dict[str, bool],
) -> None:
    stories = structure["design_results"]["story"]
    if len(stories) != expected_story_count:
        raise RuntimeError(f"expected {expected_story_count} stories, got {len(stories)}")
    if beam_file_count != expected_story_count or column_file_count != expected_story_count:
        raise RuntimeError(
            f"story drawing count mismatch: beams={beam_file_count}, columns={column_file_count}, stories={expected_story_count}"
        )

    for story_key, story in stories.items():
        if not isinstance(story["columns"], dict) or not isinstance(story["beams"], dict):
            raise RuntimeError(f"story {story_key}: beams/columns must remain dict containers")
        if len(story["columns"]) != expected_column_counts[story_key]:
            raise RuntimeError(
                f"story {story_key}: expected {expected_column_counts[story_key]} columns, got {len(story['columns'])}"
            )
        if len(story["beams"]) != expected_beam_counts[story_key]:
            raise RuntimeError(
                f"story {story_key}: expected {expected_beam_counts[story_key]} beams, got {len(story['beams'])}"
            )
        for beam_key, beam in story["beams"].items():
            reinforcement = beam["reinforcement"]
            if support_required_by_story.get(story_key, True) and (
                not reinforcement["top_left"]["text"] or not reinforcement["top_right"]["text"]
            ):
                raise RuntimeError(f"story {story_key} beam {beam_key}: support text missing")
            if not reinforcement["top_middle"]["text"] or not reinforcement["bottom"]["text"]:
                raise RuntimeError(f"story {story_key} beam {beam_key}: middle/bottom text missing")

    text = json.dumps(structure, ensure_ascii=False)
    for field in FORBIDDEN_FIELDS:
        if f'"{field}"' in text:
            raise RuntimeError(f"forbidden field still present: {field}")
    if "朴" in text or "桅" in text or "无" in text or "峖" in text:
        raise RuntimeError("output still contains undecoded reinforcement symbols")

    geometry = structure["StructureGeometry"]
    if geometry["story_count"] != expected_story_count:
        raise RuntimeError(f'story_count mismatch: {geometry["story_count"]}')
    if not math.isclose(geometry["total_area"], geometry["floor_area"] * expected_story_count, rel_tol=0.0, abs_tol=1e-6):
        raise RuntimeError("total_area is not floor_area * story_count")

    for story_key, beam_key, field, expected in SAMPLE_EXPECTATIONS.get(ROOT.name, []):
        reinforcement = stories[story_key]["beams"][beam_key]["reinforcement"]
        actual = reinforcement[field]["text"] if isinstance(reinforcement[field], dict) else reinforcement[field]
        if actual != expected:
            raise RuntimeError(
                f"sample regression mismatch for story {story_key} beam {beam_key} {field}: {actual} != {expected}"
            )

    if "10" in ROOT.name and "8" in ROOT.name:
        phi = "\u03a6"
        checks = [
            ("0", "40", "bottom", f"3{phi}22+2{phi}18"),
            ("0", "40", "waist", f"N4{phi}12"),
            ("0", "44", "top_right", f"5{phi}25"),
        ]
        for story_key, beam_key, field, expected in checks:
            reinforcement = stories[story_key]["beams"][beam_key]["reinforcement"]
            actual = reinforcement[field]["text"] if isinstance(reinforcement[field], dict) else reinforcement[field]
            if actual != expected:
                raise RuntimeError(
                    f"sample regression mismatch for story {story_key} beam {beam_key} {field}: {actual} != {expected}"
                )


def build_output() -> dict:
    seismic, materials, wmass_geometry = parse_wmass()
    beam_dims, column_dims = infer_section_pairs_from_sdata()
    if beam_dims != (350, 600):
        beam_dims = beam_dims
    if column_dims != (700, 700):
        column_dims = column_dims

    expected_story_count = int(wmass_geometry["story_count"])
    beam_files = discover_story_files("Beam")
    column_files = discover_story_files("ColumnWall")
    if len(beam_files) != expected_story_count or len(column_files) != expected_story_count:
        raise RuntimeError(
            f"story drawing count mismatch in {ROOT}: "
            f"Beam*.T={len(beam_files)}, ColumnWall*.T={len(column_files)}, NSTI={expected_story_count}"
        )
    if sorted(beam_files) != list(range(1, expected_story_count + 1)):
        raise RuntimeError(f"Beam story files are not contiguous 1..{expected_story_count}: {sorted(beam_files)}")
    if sorted(column_files) != list(range(1, expected_story_count + 1)):
        raise RuntimeError(f"ColumnWall story files are not contiguous 1..{expected_story_count}: {sorted(column_files)}")

    column_points = parse_sdata_column_points(expected_story_count)
    story_nodes = build_story_nodes(column_points)
    story_count = len(story_nodes)
    if story_count != expected_story_count:
        raise RuntimeError(f"expected {expected_story_count} story node groups, got {story_count}")

    expected_beam_counts = [
        len(generate_candidate_beams(story_nodes[story_index]["local_node_by_coord"]))  # type: ignore[arg-type,index]
        for story_index in range(story_count)
    ]
    beam_blocks = parse_sdata_beam_blocks(expected_beam_counts)
    node_maps, beam_maps = derive_story_geometry(story_nodes, beam_blocks)

    z_levels = [story_nodes[index]["z_top_mm"] for index in sorted(story_nodes)]  # type: ignore[index]
    story_heights = [z_levels[0]] + [z_levels[index] - z_levels[index - 1] for index in range(1, len(z_levels))]
    all_x = [point.x_mm for point in column_points]
    all_y = [point.y_mm for point in column_points]
    span_x = max(all_x) - min(all_x)
    span_y = max(all_y) - min(all_y)
    floor_area = float(wmass_geometry["floor_area"]) * 1_000_000.0

    output = {
        "StructureGeometry": {
            "story_height": [float(height) for height in story_heights],
            "story_count": story_count,
            "floor_area": floor_area,
            "total_area": floor_area * story_count,
            "span_x": float(span_x),
            "span_y": float(span_y),
        },
        "SeismicParameters": seismic,
        "MaterialProperties": materials,
        "design_results": {"story": {}},
    }

    expected_column_counts: Dict[str, int] = {}
    expected_beam_counts_by_story = {
        str(story_index): len(beam_maps[story_index])
        for story_index in range(story_count)
    }
    support_required_by_story: Dict[str, bool] = {}

    for story_index in range(story_count):
        story_key = str(story_index)
        support_required_by_story[story_key] = story_has_support_texts(story_index)
        story_points: Sequence[ColumnPoint] = story_nodes[story_index]["points"]  # type: ignore[index,assignment]
        coords_mm = [(point.x_mm, point.y_mm) for point in story_points]
        column_plan_records = parse_text_records(column_files[story_index + 1], b"$CP_PLANLABEL")
        detail_records = parse_text_records(column_files[story_index + 1], b"$CP_DETAILLABEL")
        label_points = []
        for record in column_plan_records:
            text = normalize_column_text(record.raw)
            if re.search(r"KZ\d+", text):
                label_points.append((record.x, record.y))
        translation = fit_translation(label_points, coords_mm)
        column_types = assign_column_types(column_plan_records, story_points, translation)
        expected_column_counts[story_key] = len(column_types)
        column_details = build_column_type_table(detail_records)
        beam_strips, beam_to_strip_key = parse_story_beam_strips(story_index, beam_maps[story_index], translation)
        support_texts = assign_support_texts(
            story_index,
            beam_maps[story_index],
            beam_strips,
            beam_to_strip_key,
            {local_id: (node["x_mm"], node["y_mm"]) for local_id, node in node_maps[story_index].items()},
            translation,
        )
        middle_bottom_texts = assign_middle_bottom_texts(
            story_index,
            beam_maps[story_index],
            beam_strips,
            beam_to_strip_key,
            translation,
        )
        beam_waist_texts = assign_beam_waist_texts(story_index, beam_maps[story_index], translation)
        resolved_waist_texts = resolve_waist_texts(
            beam_maps[story_index],
            beam_strips,
            beam_to_strip_key,
            beam_waist_texts,
        )
        output["design_results"]["story"][story_key] = build_story_output(
            story_index,
            story_nodes[story_index],
            node_maps[story_index],
            beam_maps[story_index],
            column_types,
            column_details,
            beam_strips,
            beam_to_strip_key,
            support_texts,
            middle_bottom_texts,
            resolved_waist_texts,
            column_dims,
            support_required_by_story[story_key],
        )

    validate_output_generic(
        output,
        expected_story_count=story_count,
        expected_column_counts=expected_column_counts,
        expected_beam_counts=expected_beam_counts_by_story,
        beam_file_count=len(beam_files),
        column_file_count=len(column_files),
        support_required_by_story=support_required_by_story,
    )
    return output


def write_project_output(project_root: Path, used_seeds: Optional[set] = None) -> Path:
    configure_project(project_root)
    structure = build_output()
    output_path = output_path_for_project(project_root, used_seeds)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def discover_extracted_projects(workspace_root: Path) -> List[Path]:
    return sorted(
        [path for path in workspace_root.resolve().iterdir() if path.is_dir() and path.name.endswith("_extracted")],
        key=lambda path: path.name,
    )


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--all":
        workspace_root = Path(sys.argv[2]).resolve() if len(sys.argv) >= 3 else Path.cwd().resolve()
        projects = discover_extracted_projects(workspace_root)
        if not projects:
            raise RuntimeError(f"no *_extracted directories found in {workspace_root}")
        used_seeds: set = set()
        for project_root in projects:
            write_project_output(project_root, used_seeds)
        return

    project_root = Path(sys.argv[1]).resolve() if len(sys.argv) >= 2 else Path.cwd().resolve()
    write_project_output(project_root)


if __name__ == "__main__":
    main()
