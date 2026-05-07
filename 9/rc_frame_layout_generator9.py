#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RC Frame Layout Generator (2D X-Z / X-Y)

研究用途声明：
本项目用于算法验证、数据生成示例；不构成工程设计成果。

未覆盖的工程必需内容（按总控 Prompt 原样列出）：
- 二阶效应（P-Δ / P-δ）、几何非线性
- 材料非线性、构件开裂与刚度折减
- 抗震构造措施与节点核心区校核
- 裂缝、挠度、耐久性、构造详图
- 柱 P–M 相互作用严格分析（仅允许做“示例级估算”）
- 基础、楼板协同、剪切变形、楼层刚性假定校核
- 工程应用前必须按现行规范补齐并由注册结构工程师复核
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from typing import Any, Dict, List, Tuple

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import torch

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    HAS_MPL = True
except Exception:
    HAS_MPL = False


DEFAULT_CONFIG: Dict[str, Any] = {
    "plane": "XY",  # 平面类型："XY" 或 "XZ"
    "layout_type": "layout9",
    "num_spans_range": [5, 8],  # X 向跨数随机范围（含端点）
    "num_stories": None,  # None 表示从 4..12 随机采样
    "num_stories_range": [4, 12],  # 层数随机范围（含端点）
    "num_spans_y_range": [4, 7],  # Y 向跨数随机范围（自动取奇数）
    "span_length_x_mm": None,  # X 向跨长固定值；None 表示从范围采样并重复
    "span_length_y_other_mm": None,  # Y 向其余跨长度；None 表示从范围采样并重复
    "span_length_range_mm": [4500, 6000],  # 跨长候选范围（mm，300mm步长）
    "span_length_y_range_mm": [4500, 6000],  # Y向跨长候选范围（mm，300mm步长）
    "story_height_range_mm": [3900, 3900],  # 层高候选：3300/3600/3900/4200/4500
    "beam_section_range_mm": {"b": [250, 400], "h": [400, 900]},  # 按层数分档固定
    "column_section_range_mm": {"b": [400, 900], "h": [400, 900]},  # 按层数分档固定
    "column_vary_by_story": False,  # 是否按层变化柱截面
    "max_attempts": 20,
    "visualize": {"enabled": False},  # 生成失败时最大重试次数
}

STORY_COUNT_CHOICES = [4, 6, 8, 9, 10, 12]
STORY_HEIGHT_CHOICES_MM = [3900.0]
SPAN_LENGTH_CHOICES_MM = [4500.0, 6000.0]
X_SPAN_COUNT_CHOICES = [5, 6, 7, 8]
Y_SPAN_COUNT_CHOICES = [4, 5, 6, 7]
MAX_HEIGHT_TO_WIDTH_RATIO = 3.0
MAX_LENGTH_TO_WIDTH_RATIO = 5.0
MAX_REENTRANT_RATIO = 0.30
SLAB_THICKNESS_MM = 120.0
BEAM_LINE_LOAD_KN_PER_M = 8.5
LOAD_CASE = "DEAD"
REBAR_GRADE = "HRB400"
LAYOUT_ID = 9
LAYOUT_NAME = "H-shape"

BEAM_SECTIONS_BY_STORY_AND_SPAN_MM: Dict[int, Dict[float, Tuple[float, float]]] = {
    4: {
        4500.0: (250.0, 450.0),
        6000.0: (300.0, 550.0),
    },
    6: {
        4500.0: (250.0, 450.0),
        6000.0: (300.0, 600.0),
    },
    8: {
        4500.0: (250.0, 500.0),
        6000.0: (300.0, 600.0),
    },
    9: {
        4500.0: (250.0, 500.0),
        6000.0: (300.0, 650.0),
    },
    10: {
        4500.0: (250.0, 500.0),
        6000.0: (300.0, 650.0),
    },
    12: {
        4500.0: (250.0, 550.0),
        6000.0: (300.0, 650.0),
    },
}

COLUMN_SECTIONS_BY_STORY_AND_SPAN_MM: Dict[int, Dict[float, Tuple[float, float]]] = {
    4: {
        4500.0: (350.0, 350.0),
        6000.0: (450.0, 450.0),
    },
    6: {
        4500.0: (400.0, 400.0),
        6000.0: (500.0, 500.0),
    },
    8: {
        4500.0: (450.0, 450.0),
        6000.0: (550.0, 550.0),
    },
    9: {
        4500.0: (500.0, 500.0),
        6000.0: (600.0, 600.0),
    },
    10: {
        4500.0: (550.0, 550.0),
        6000.0: (600.0, 600.0),
    },
    12: {
        4500.0: (600.0, 600.0),
        6000.0: (650.0, 650.0),
    },
}


def sample_story_setup(
    cfg: Dict[str, Any], g: torch.Generator, device: torch.device
) -> Tuple[int, List[float]]:
    configured_stories = cfg.get("num_stories")
    if configured_stories is None:
        story_range = cfg.get("num_stories_range", [min(STORY_COUNT_CHOICES), max(STORY_COUNT_CHOICES)])
        story_candidates = [
            s for s in STORY_COUNT_CHOICES if int(story_range[0]) <= s <= int(story_range[1])
        ]
        if not story_candidates:
            raise ValueError("num_stories_range has no supported story count.")
        num_stories = int(sample_choice(g, device, story_candidates))
    else:
        num_stories = int(configured_stories)
    if num_stories not in STORY_COUNT_CHOICES:
        raise ValueError("num_stories must be one of 4, 6, 8, 9, 10, 12.")

    configured_height = cfg.get("story_height_mm")
    if configured_height is None:
        story_height = float(sample_choice(g, device, STORY_HEIGHT_CHOICES_MM))
    else:
        story_height = float(configured_height)
        if story_height not in STORY_HEIGHT_CHOICES_MM:
            raise ValueError("story_height_mm must be 3900.")
    return num_stories, [story_height for _ in range(num_stories)]


def span_candidates_for_story_count(num_stories: int) -> List[float]:
    if num_stories in STORY_COUNT_CHOICES:
        return [4500.0, 6000.0]
    raise ValueError("num_stories must be one of 4, 6, 8, 9, 10, 12.")


def span_count_candidates_for_story_count(num_stories: int) -> Tuple[List[int], List[int]]:
    if num_stories in STORY_COUNT_CHOICES:
        return [5, 6, 7, 8], [4, 5, 6, 7]
    raise ValueError("num_stories must be one of 4, 6, 8, 9, 10, 12.")


def section_sizes_for_story_count(
    num_stories: int, main_span_mm: float
) -> Tuple[float, float, float, float]:
    main_span_mm = float(main_span_mm)
    beam_table = BEAM_SECTIONS_BY_STORY_AND_SPAN_MM.get(num_stories)
    column_table = COLUMN_SECTIONS_BY_STORY_AND_SPAN_MM.get(num_stories)
    if beam_table is None or main_span_mm not in beam_table:
        raise ValueError("No beam section is defined for this story count and main span.")
    if column_table is None or main_span_mm not in column_table:
        raise ValueError("No column section is defined for this story count and main span.")
    beam_b, beam_h = beam_table[main_span_mm]
    column_b, column_h = column_table[main_span_mm]
    return beam_b, beam_h, column_b, column_h


def configured_count_candidates(
    cfg: Dict[str, Any],
    fixed_key: str,
    range_key: str,
    defaults: List[int],
    allowed: List[int],
    name: str,
) -> List[int]:
    fixed = cfg.get(fixed_key)
    if fixed is not None:
        value = int(fixed)
        if value not in allowed:
            raise ValueError(f"{name} must be in the supported count choices.")
        return [value]
    low, high = cfg.get(range_key, [min(allowed), max(allowed)])
    candidates = [c for c in defaults if int(low) <= c <= int(high) and c in allowed]
    if not candidates:
        raise ValueError(f"{name} has no valid candidates.")
    return candidates


def validate_span_candidate(value: float, story_spans: List[float], name: str) -> float:
    value = float(value)
    if value not in SPAN_LENGTH_CHOICES_MM:
        raise ValueError(f"{name} must be one of 4500, 6000.")
    if value not in story_spans:
        raise ValueError(f"{name} does not match the story-count span band.")
    return value


def uniform_span_from_list(values: List[Any], expected_count: int, name: str) -> float:
    if len(values) != expected_count:
        raise ValueError(f"{name} length must match span count.")
    numeric = [float(v) for v in values]
    if len({round(v, 6) for v in numeric}) != 1:
        raise ValueError(f"{name} must be uniform within the direction.")
    return numeric[0]


def configured_span_candidates(
    cfg: Dict[str, Any],
    list_key: str,
    fixed_key: str,
    expected_count: int,
    story_spans: List[float],
    name: str,
) -> List[float]:
    values = cfg.get(list_key)
    if values:
        return [validate_span_candidate(uniform_span_from_list(values, expected_count, list_key), story_spans, list_key)]
    fixed = cfg.get(fixed_key)
    if fixed is not None:
        return [validate_span_candidate(float(fixed), story_spans, fixed_key)]
    return list(story_spans)


def full_active_cells(num_spans_x: int, num_spans_y: int) -> set[tuple[int, int]]:
    return {(i, j) for j in range(num_spans_y) for i in range(num_spans_x)}


def active_nodes_from_cells(cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
    nodes: set[tuple[int, int]] = set()
    for i, j in cells:
        nodes.update(((i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)))
    return nodes


def full_active_nodes(num_spans_x: int, num_spans_y: int) -> set[tuple[int, int]]:
    return {(i, j) for j in range(num_spans_y + 1) for i in range(num_spans_x + 1)}


def active_cells_from_nodes(active_nodes: set[tuple[int, int]], num_spans_x: int, num_spans_y: int) -> set[tuple[int, int]]:
    cells = set()
    for j in range(num_spans_y):
        for i in range(num_spans_x):
            corners = ((i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1))
            if all(corner in active_nodes for corner in corners):
                cells.add((i, j))
    return cells


def resolve_active_cells(num_spans_x: int, num_spans_y: int, layout_type: str | None) -> set[tuple[int, int]]:
    cell_builder = globals().get("build_active_cells")
    if cell_builder is not None:
        return cell_builder(num_spans_x, num_spans_y, layout_type)
    node_builder = globals().get("build_active_nodes")
    if node_builder is not None:
        return active_cells_from_nodes(node_builder(num_spans_x, num_spans_y, layout_type), num_spans_x, num_spans_y)
    return full_active_cells(num_spans_x, num_spans_y)


def normalise_edge(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    return (a, b) if a <= b else (b, a)


def active_edges_from_cells(cells: set[tuple[int, int]]) -> set[tuple[tuple[int, int], tuple[int, int]]]:
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for i, j in cells:
        edges.add(normalise_edge((i, j), (i + 1, j)))
        edges.add(normalise_edge((i, j + 1), (i + 1, j + 1)))
        edges.add(normalise_edge((i, j), (i, j + 1)))
        edges.add(normalise_edge((i + 1, j), (i + 1, j + 1)))
    return edges


def edge_missing_depths(cells: set[tuple[int, int]], num_spans_x: int, num_spans_y: int) -> Tuple[int, int, int, int]:
    left = right = bottom = top = 0
    for j in range(num_spans_y):
        depth = 0
        while depth < num_spans_x and (depth, j) not in cells:
            depth += 1
        if depth < num_spans_x:
            left = max(left, depth)
        depth = 0
        while depth < num_spans_x and (num_spans_x - 1 - depth, j) not in cells:
            depth += 1
        if depth < num_spans_x:
            right = max(right, depth)
    for i in range(num_spans_x):
        depth = 0
        while depth < num_spans_y and (i, depth) not in cells:
            depth += 1
        if depth < num_spans_y:
            bottom = max(bottom, depth)
        depth = 0
        while depth < num_spans_y and (i, num_spans_y - 1 - depth) not in cells:
            depth += 1
        if depth < num_spans_y:
            top = max(top, depth)
    return left, right, bottom, top


def reentrant_ratio(active_cells: set[tuple[int, int]], num_spans_x: int, num_spans_y: int, span_x: float, span_y: float) -> float:
    if not active_cells:
        raise ValueError("layout has no active cells.")
    total_x = num_spans_x * span_x
    total_y = num_spans_y * span_y
    left, right, bottom, top = edge_missing_depths(active_cells, num_spans_x, num_spans_y)
    return max(left * span_x / total_x, right * span_x / total_x, bottom * span_y / total_y, top * span_y / total_y)


def layout_constraints_ok(active_cells: set[tuple[int, int]], num_spans_x: int, num_spans_y: int, span_x: float, span_y: float, story_heights: List[float]) -> bool:
    total_x = num_spans_x * span_x
    total_y = num_spans_y * span_y
    if total_x < total_y:
        return False
    height = sum(float(v) for v in story_heights)
    width = min(total_x, total_y)
    length = max(total_x, total_y)
    if height / width > MAX_HEIGHT_TO_WIDTH_RATIO:
        return False
    if length / width > MAX_LENGTH_TO_WIDTH_RATIO:
        return False
    return reentrant_ratio(active_cells, num_spans_x, num_spans_y, span_x, span_y) <= MAX_REENTRANT_RATIO


def sample_plan_geometry(
    cfg: Dict[str, Any],
    g: torch.Generator,
    device: torch.device,
    num_stories: int,
    story_heights: List[float],
    layout_type: str | None,
) -> Tuple[int, int, List[float], List[float], set[tuple[int, int]]]:
    default_x_counts, default_y_counts = span_count_candidates_for_story_count(num_stories)
    x_counts = configured_count_candidates(cfg, "num_spans", "num_spans_range", default_x_counts, X_SPAN_COUNT_CHOICES, "num_spans")
    y_counts = configured_count_candidates(cfg, "num_spans_y", "num_spans_y_range", default_y_counts, Y_SPAN_COUNT_CHOICES, "num_spans_y")
    story_spans = span_candidates_for_story_count(num_stories)
    valid: List[Tuple[int, int, float, float, set[tuple[int, int]]]] = []
    for num_spans_x in x_counts:
        for num_spans_y in y_counts:
            span_x_candidates = configured_span_candidates(cfg, "span_lengths", "span_length_x_mm", num_spans_x, story_spans, "span_x")
            span_y_candidates = configured_span_candidates(cfg, "span_lengths_y", "span_length_y_other_mm", num_spans_y, story_spans, "span_y")
            for span_x in span_x_candidates:
                for span_y in span_y_candidates:
                    main_span = max(span_x, span_y)
                    try:
                        section_sizes_for_story_count(num_stories, main_span)
                        active_cells = resolve_active_cells(num_spans_x, num_spans_y, layout_type)
                        if layout_constraints_ok(active_cells, num_spans_x, num_spans_y, span_x, span_y, story_heights):
                            valid.append((num_spans_x, num_spans_y, span_x, span_y, active_cells))
                    except ValueError:
                        continue
    if not valid:
        raise ValueError("No layout geometry satisfies span, ratio, and section constraints.")
    num_spans_x, num_spans_y, span_x, span_y, active_cells = sample_choice(g, device, valid)
    return (num_spans_x, num_spans_y, [float(span_x) for _ in range(num_spans_x)], [float(span_y) for _ in range(num_spans_y)], active_cells)

def deep_update(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = deep_update(dict(base[key]), value)
        else:
            base[key] = value
    return base


# 读取配置文件并应用覆盖
def load_config(path: str | None) -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if path:
        with open(path, "r", encoding="utf-8") as f:
            overrides = json.load(f)
        cfg = deep_update(cfg, overrides)
    return cfg


# 创建可复现的随机数生成器
def make_generator(seed: int, device: torch.device) -> torch.Generator:
    if device.type == "cuda":
        torch.cuda.manual_seed_all(seed)
    g = torch.Generator(device=device)
    g.manual_seed(seed)
    return g


# 采样整数（闭区间）
def sample_int(g: torch.Generator, device: torch.device, low: int, high: int) -> int:
    if low == high:
        return int(low)
    return int(torch.randint(low, high + 1, (1,), generator=g, device=device).item())


# 采样浮点数（闭区间）
def sample_float(
    g: torch.Generator, device: torch.device, low: float, high: float
) -> float:
    if math.isclose(low, high):
        return float(low)
    r = torch.rand(1, generator=g, device=device).item()
    return float(low + (high - low) * r)


# 从候选列表中随机选择一个
def sample_choice(g: torch.Generator, device: torch.device, choices: List[Any]) -> Any:
    idx = sample_int(g, device, 0, len(choices) - 1)
    return choices[idx]



# 按指定步长量化长度，并限制在范围内
def quantize_length(value: float, step: float, low: float | None = None, high: float | None = None) -> float:
    if step <= 0:
        raise ValueError("step must be positive.")
    q = round(value / step) * step
    if low is not None and q < low:
        q = math.ceil(low / step) * step
    if high is not None and q > high:
        q = math.floor(high / step) * step
    return float(q)


# 采样奇数整数（用于 Y 向跨数）
def sample_odd_int(
    g: torch.Generator, device: torch.device, low: int, high: int
) -> int:
    if low == high:
        if low % 2 == 0:
            raise ValueError("num_spans_y must be odd.")
        return int(low)
    for _ in range(20):
        val = sample_int(g, device, low, high)
        if val % 2 == 1:
            return val
    # 兜底：在范围内取最近的奇数
    if low % 2 == 1:
        return low
    if high % 2 == 1:
        return high
    raise ValueError("No odd number in the given range for num_spans_y.")


# 由跨度/层高列表生成累计坐标
def cumulative_positions(lengths: List[float]) -> List[float]:
    coords = [0.0]
    total = 0.0
    for L in lengths:
        total += L
        coords.append(total)
    return coords


# 格式化楼层编号为两位字符串
# Active floor cells for notches, atrium, and openings
def build_active_cells(
    num_spans_x: int, num_spans_y: int, layout_type: str | None
) -> set[tuple[int, int]]:
    if layout_type not in (None, "full", "layout9"):
        raise ValueError(f"unsupported layout_type: {layout_type}")
    cells = full_active_cells(num_spans_x, num_spans_y)
    if layout_type in (None, "full"):
        return cells
    if num_spans_x < 4 or num_spans_y < 3:
        raise ValueError("layout9 requires at least 4 X spans and 3 Y spans.")
    slot_w = 1 if num_spans_x < 7 else 2
    start_i = (num_spans_x - slot_w) // 2
    margin_y = 1 if num_spans_y < 6 else 2
    start_j = margin_y
    end_j = num_spans_y - margin_y
    if start_j >= end_j:
        start_j = 1
        end_j = num_spans_y - 1
    for j in range(start_j, end_j):
        for i in range(start_i, start_i + slot_w):
            cells.discard((i, j))
    return cells

def format_story_label(story_index: int) -> str:
    return f"{story_index:02d}"


# 解析楼层标签的起止索引
def parse_story_label(label: str) -> Tuple[int, int]:
    if "-" in label:
        start_str, end_str = label.split("-", 1)
        return int(start_str), int(end_str)
    value = int(label)
    return value, value


# 按内容合并连续楼层区间（如 01-05）
def group_story_ranges(
    story_map: Dict[str, Dict[str, Dict[str, Any]]]
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if not story_map:
        return {}
    ordered_labels = sorted(story_map.keys(), key=lambda k: parse_story_label(k)[0])
    merged: Dict[str, Dict[str, Dict[str, Any]]] = {}
    run_start = ordered_labels[0]
    run_end = ordered_labels[0]
    run_value = story_map[ordered_labels[0]]
    for label in ordered_labels[1:]:
        value = story_map[label]
        prev_end = parse_story_label(run_end)[1]
        curr_start, curr_end = parse_story_label(label)
        if value == run_value and curr_start == prev_end + 1 and curr_start == curr_end:
            run_end = label
            continue
        start_idx, _ = parse_story_label(run_start)
        end_idx, _ = parse_story_label(run_end)
        if start_idx == end_idx:
            key = format_story_label(start_idx)
        else:
            key = f"{format_story_label(start_idx)}-{format_story_label(end_idx)}"
        merged[key] = run_value
        run_start = label
        run_end = label
        run_value = value
    start_idx, _ = parse_story_label(run_start)
    end_idx, _ = parse_story_label(run_end)
    if start_idx == end_idx:
        key = format_story_label(start_idx)
    else:
        key = f"{format_story_label(start_idx)}-{format_story_label(end_idx)}"
    merged[key] = run_value
    return merged


# 强制合并为完整楼层区间（如 01-06）
def collapse_to_full_range(
    story_map: Dict[str, Dict[str, Dict[str, Any]]], num_stories: int
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if not story_map:
        return {}
    first_label = sorted(story_map.keys(), key=lambda k: parse_story_label(k)[0])[0]
    value = story_map[first_label]
    if num_stories <= 1:
        key = format_story_label(1)
    else:
        key = f"{format_story_label(1)}-{format_story_label(num_stories)}"
    return {key: value}


# 由网格坐标生成平面节点字典
def build_nodes_dict(
    x_coords: List[float], y_coords: List[float], active_nodes: set[tuple[int, int]]
) -> Dict[str, Dict[str, float]]:
    nodes_out: Dict[str, Dict[str, float]] = {}
    for i, j in sorted(active_nodes):
        nodes_out[f"N_{i}_{j}"] = {"x": float(x_coords[i]), "y": float(y_coords[j])}
    return nodes_out


# 构建截面输出字典及截面 ID 到名称的映射
def build_section_maps(
    sections: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, float]], Dict[int, str]]:
    sections_out: Dict[str, Dict[str, float]] = {}
    id_to_name: Dict[int, str] = {}
    beam_idx = 1
    column_idx = 1
    for sec in sections:
        if sec["type"] == "beam":
            name = f"beam{beam_idx}"
            beam_idx += 1
        else:
            name = f"column{column_idx}"
            column_idx += 1
        sections_out[name] = {"b": float(sec["b"]), "h": float(sec["h"])}
        id_to_name[int(sec["id"])] = name
    return sections_out, id_to_name


# 按层生成柱构件信息（平面布局）
def element_endpoint(node: Dict[str, Any]) -> Dict[str, float]:
    return {
        "x": float(node["x"]),
        "y": float(node["y"]),
        "z": float(node["z"]),
    }


# 按层生成柱构件信息（平面布局）
def build_columns_by_story(
    elements: List[Dict[str, Any]],
    nodes_by_id: Dict[int, Dict[str, Any]],
    section_name_by_id: Dict[int, str],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    columns_by_story: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for e in elements:
        if e["type"] != "column":
            continue
        ni = nodes_by_id[e["ni"]]
        nj = nodes_by_id[e["nj"]]
        lower, upper = (ni, nj) if float(ni["z"]) <= float(nj["z"]) else (nj, ni)
        story = max(int(ni["story"]), int(nj["story"]))
        story_label = format_story_label(story)
        columns_by_story.setdefault(story_label, {})
        i = int(lower["grid_i"])
        j = int(lower["grid_j"])
        key = f"C_{i}_{j}"
        length = abs(float(upper["z"]) - float(lower["z"]))
        section_name = section_name_by_id.get(int(e["section_id"]), "column1")
        columns_by_story[story_label][key] = {
            "node": f"N_{i}_{j}",
            "story": story,
            "direction": "Z",
            "length": length,
            "section": section_name,
            "rebar_grade": REBAR_GRADE,
            "start": element_endpoint(lower),
            "end": element_endpoint(upper),
        }
    return columns_by_story


# 按层生成梁构件信息（平面布局）
def build_beams_by_story(
    elements: List[Dict[str, Any]],
    nodes_by_id: Dict[int, Dict[str, Any]],
    section_name_by_id: Dict[int, str],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    beams_by_story: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for e in elements:
        if e["type"] != "beam":
            continue
        ni = nodes_by_id[e["ni"]]
        nj = nodes_by_id[e["nj"]]
        story = int(ni["story"])
        story_label = format_story_label(story)
        beams_by_story.setdefault(story_label, {})
        dir_flag = e.get("dir")
        if dir_flag == "x":
            if int(ni["grid_i"]) <= int(nj["grid_i"]):
                start, end = ni, nj
            else:
                start, end = nj, ni
            i = int(start["grid_i"])
            j = int(start["grid_j"])
            key = f"B_{i}_{j}_1"
            i_node = f"N_{i}_{j}"
            j_node = f"N_{int(end['grid_i'])}_{int(end['grid_j'])}"
            length = abs(float(end["x"]) - float(start["x"]))
            direction = "X"
        else:
            if int(ni["grid_j"]) <= int(nj["grid_j"]):
                start, end = ni, nj
            else:
                start, end = nj, ni
            i = int(start["grid_i"])
            j = int(start["grid_j"])
            key = f"B_{i}_{j}_2"
            i_node = f"N_{i}_{j}"
            j_node = f"N_{int(end['grid_i'])}_{int(end['grid_j'])}"
            length = abs(float(end["y"]) - float(start["y"]))
            direction = "Y"
        section_name = section_name_by_id.get(int(e["section_id"]), "beam1")
        beams_by_story[story_label][key] = {
            "i_node": i_node,
            "j_node": j_node,
            "story": story,
            "direction": direction,
            "length": length,
            "section": section_name,
            "rebar_grade": REBAR_GRADE,
            "start": element_endpoint(start),
            "end": element_endpoint(end),
            "line_load": {
                "case": LOAD_CASE,
                "direction": "Gravity",
                "value_kn_per_m": BEAM_LINE_LOAD_KN_PER_M,
            },
        }
    return beams_by_story


# 计算 XZ 平面节点编号
def node_id(story_index: int, grid_x_index: int, num_spans: int) -> int:
    return story_index * (num_spans + 1) + grid_x_index


# 计算 XY 平面节点编号
def node_id_xy(
    level_index: int, grid_y_index: int, grid_x_index: int, num_spans_x: int, num_spans_y: int
) -> int:
    return (
        level_index * (num_spans_x + 1) * (num_spans_y + 1)
        + grid_y_index * (num_spans_x + 1)
        + grid_x_index
    )


# 选择水平荷载输入类型
def choose_horizontal_input(
    g: torch.Generator, device: torch.device, mode: str
) -> str:
    if mode in ("F_story", "V_base"):
        return mode
    return sample_choice(g, device, ["F_story", "V_base"])


# 按规则分配层间水平力
def distribute_story_forces(
    z_coords: List[float], V_base: float, rule: str, w_power: float
) -> List[float]:
    story_heights = z_coords[1:]
    if not story_heights:
        return []

    if rule == "uniform":
        weights = [1.0 for _ in story_heights]
    elif rule == "w_power":
        weights = [h**w_power for h in story_heights]
    else:
        weights = [h for h in story_heights]

    total = sum(weights)
    if math.isclose(total, 0.0):
        return [0.0 for _ in story_heights]
    return [V_base * w / total for w in weights]


# 构建布局模型（仅输出布局与构件尺寸）
def build_model(cfg: Dict[str, Any], base_seed: int, index: int, device: torch.device) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    g = make_generator(base_seed + index, device)
    plane = cfg.get("plane", "XY").upper()
    if plane != "XY":
        raise ValueError("????? XY ???????")

    layout_type = cfg.get("layout_type", "full")
    num_stories, story_heights = sample_story_setup(cfg, g, device)
    num_spans_x, num_spans_y, span_lengths_x, span_lengths_y, active_cells = sample_plan_geometry(cfg, g, device, num_stories, story_heights, layout_type)
    x_coords = cumulative_positions(span_lengths_x)
    y_coords = cumulative_positions(span_lengths_y)
    z_coords = cumulative_positions(story_heights)
    active_nodes = active_nodes_from_cells(active_cells)
    active_edges = active_edges_from_cells(active_cells)

    nodes: List[Dict[str, Any]] = []
    for level in range(num_stories + 1):
        z_val = z_coords[level]
        for i, j in sorted(active_nodes):
            nodes.append({"id": node_id_xy(level, j, i, num_spans_x, num_spans_y), "x": x_coords[i], "y": y_coords[j], "z": z_val, "story": level, "grid_i": i, "grid_j": j})

    beam_b, beam_h, column_b, column_h = section_sizes_for_story_count(num_stories, max(span_lengths_x[0], span_lengths_y[0]))
    sections: List[Dict[str, Any]] = [{"id": 1, "type": "beam", "b": beam_b, "h": beam_h}, {"id": 101, "type": "column", "b": column_b, "h": column_h}]
    elements: List[Dict[str, Any]] = []
    eid = 1
    for level in range(1, num_stories + 1):
        for i, j in sorted(active_nodes):
            elements.append({"id": eid, "type": "column", "ni": node_id_xy(level - 1, j, i, num_spans_x, num_spans_y), "nj": node_id_xy(level, j, i, num_spans_x, num_spans_y), "section_id": 101})
            eid += 1
    for level in range(1, num_stories + 1):
        for start, end in sorted(active_edges):
            i0, j0 = start
            i1, j1 = end
            elements.append({"id": eid, "type": "beam", "dir": "x" if j0 == j1 else "y", "ni": node_id_xy(level, j0, i0, num_spans_x, num_spans_y), "nj": node_id_xy(level, j1, i1, num_spans_x, num_spans_y), "section_id": 1})
            eid += 1

    nodes_by_id = {n["id"]: n for n in nodes}
    nodes_out = build_nodes_dict(x_coords, y_coords, active_nodes)
    sections_out, section_name_by_id = build_section_maps(sections)
    columns_raw = build_columns_by_story(elements, nodes_by_id, section_name_by_id)
    beams_raw = build_beams_by_story(elements, nodes_by_id, section_name_by_id)
    columns_out = group_story_ranges(columns_raw)
    beams_out = group_story_ranges(beams_raw)
    model_id = f"m{base_seed}_{index:04d}" if base_seed is not None else f"m{index:04d}"
    model = {
        "parameters": {"structure_type": "RC_Frame", "num_stories": num_stories, "layout_id": LAYOUT_ID, "layout_type": layout_type, "layout_name": LAYOUT_NAME},
        "story_height": story_heights,
        "story_count": num_stories,
        "SlabProperties": {"thickness_mm": SLAB_THICKNESS_MM},
        "LoadProperties": {"beam_line_load_kn_per_m": BEAM_LINE_LOAD_KN_PER_M, "load_case": LOAD_CASE},
        "SeismicParameters": {"seismic_intensity": 8, "PGA_target": 0.2, "PGA_rare_max": 4000.0, "design_group": 2, "site_class": "II", "damping_ratio": 0.05},
        "MaterialProperties": {"concrete_grade": "C30", "steel_grade": "HRB400", "E_c": 30000.0, "E_s": 200000.0, "fc": 14.3, "fck": 20.1, "ft": 1.43, "ftk": 2.01, "fy": 360.0, "concrete_cover_mm": 20.0},
        "ReinforcementProperties": {"beam_steel_grade": REBAR_GRADE, "column_steel_grade": REBAR_GRADE, "wall_steel_grade": REBAR_GRADE},
        "geometry": {"grid_x": x_coords, "grid_y": y_coords, "grid_z": z_coords, "active_cells": [[i, j] for i, j in sorted(active_cells)]},
        "nodes": nodes_out,
        "sections": sections_out,
        "columns": columns_out,
        "beams": beams_out,
    }
    num_beams = sum(len(v) for v in beams_raw.values())
    num_columns = sum(len(v) for v in columns_raw.values())
    summary = {"model_id": model_id, "plane": plane, "num_spans": num_spans_x, "num_spans_y": num_spans_y, "num_stories": num_stories, "num_nodes": len(nodes_out), "num_elements": num_beams + num_columns, "num_beams": num_beams, "num_columns": num_columns}
    return model, summary


def plot_layout(model: Dict[str, Any], out_path: str, plot_rooms: bool = True, dpi: int = 180) -> None:
    if not HAS_MPL:
        raise RuntimeError("matplotlib is required for visualization.")
    grid_x = model["geometry"]["grid_x"]
    grid_y = model["geometry"]["grid_y"]
    active_cells = {tuple(cell) for cell in model["geometry"].get("active_cells", [])}
    nodes = model["nodes"]
    beams_by_story = model.get("beams", {})
    story_keys = sorted(beams_by_story.keys(), key=lambda k: parse_story_label(k)[0])
    beams = beams_by_story[story_keys[0]] if story_keys else {}
    fig, ax = plt.subplots(figsize=(8, 5))
    if plot_rooms:
        for i, j in sorted(active_cells):
            x0, x1 = grid_x[i], grid_x[i + 1]
            y0, y1 = grid_y[j], grid_y[j + 1]
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor="#f0f3f6", edgecolor="none", alpha=0.7))
    for beam in beams.values():
        ni = nodes[beam["i_node"]]
        nj = nodes[beam["j_node"]]
        ax.plot([ni["x"], nj["x"]], [ni["y"], nj["y"]], color="#2a6fdb", linewidth=2.0)
    ax.scatter([n["x"] for n in nodes.values()], [n["y"] for n in nodes.values()], s=18, color="#111111", zorder=5)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    story_count = int(model.get("story_count", model.get("parameters", {}).get("num_stories", 0)))
    story_heights = [float(v) for v in model.get("story_height", [])]
    story_height_text = f"{story_heights[0] / 1000.0:.1f} m" if story_heights else "-"
    x_span_count = max(0, len(grid_x) - 1)
    y_span_count = max(0, len(grid_y) - 1)
    x_span_text = f"{x_span_count} (avg {(grid_x[-1] - grid_x[0]) / x_span_count / 1000.0:.1f} m)" if x_span_count else "-"
    y_span_text = f"{y_span_count} (avg {(grid_y[-1] - grid_y[0]) / y_span_count / 1000.0:.1f} m)" if y_span_count else "-"
    params = model.get("parameters", {})
    layout_id = int(params.get("layout_id", LAYOUT_ID))
    layout_name = str(params.get("layout_name", LAYOUT_NAME))
    title = f"RC Frame Plan Layout - {layout_id:02d}"
    if layout_name:
        title = f"{title} ({layout_name})"
    subtitle = f"Stories: {story_count} | Story height: {story_height_text} | X spans: {x_span_text} | Y spans: {y_span_text}"
    ax.set_title(f"{title}\n{subtitle}")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.5)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)


def main() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    default_outdir = os.path.abspath(os.path.join(base_dir, os.pardir, "out"))
    parser = argparse.ArgumentParser(description="2D RC frame layout generator")
    parser.add_argument(
        "--n",
        type=int,
        default=1,
        help="number of models to generate (default: 1)",
    )
    parser.add_argument(
        "--outdir",
        type=str,
        default=default_outdir,
        help="output directory (default: ../out under the layout_generator1_9 folder)",
    )
    parser.add_argument("--seed", type=int, default=None, help="random seed")
    parser.add_argument(
        "--config", type=str, default=None, help="JSON config to override defaults"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="overwrite existing files"
    )
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    cfg = load_config(args.config)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[INFO] torch device = {device}")
    if args.seed is None:
        base_seed = int(time.time())
    else:
        base_seed = args.seed

    attempts = 0
    generated = 0
    while generated < args.n and attempts < cfg.get("max_attempts", 20):
        try:
            model, summary = build_model(cfg, base_seed, generated, device)
        except Exception as exc:
            attempts += 1
            print(f"[WARN] generation failed: {exc} (attempt {attempts})")
            continue

        model_id = summary["model_id"]
        layout_prefix = f"layout{LAYOUT_ID}_"
        json_path = os.path.join(args.outdir, f"{layout_prefix}{model_id}.json")
        png_path = os.path.join(args.outdir, f"{layout_prefix}{model_id}_layout.png")

        if not args.overwrite and (os.path.exists(json_path) or os.path.exists(png_path)):
            print(f"[SKIP] {model_id} exists (use --overwrite to replace)")
            generated += 1
            continue

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(model, f, indent=2)

        if cfg.get("visualize", {}).get("enabled", True):
            if HAS_MPL:
                plot_layout(
                    model,
                    png_path,
                    plot_rooms=cfg.get("visualize", {}).get("plot_rooms", True),
                    dpi=cfg.get("visualize", {}).get("dpi", 180),
                )
            else:
                print("[WARN] matplotlib not available; skipping PNG visualization.")

        if summary["plane"] == "XY":
            print(
                f"[OK] {model_id} | spans_x={summary['num_spans']} spans_y={summary['num_spans_y']} "
                f"stories={summary['num_stories']} nodes={summary['num_nodes']} "
                f"elements={summary['num_elements']} beams={summary['num_beams']} "
                f"columns={summary['num_columns']}"
            )
        else:
            print(
                f"[OK] {model_id} | spans={summary['num_spans']} stories={summary['num_stories']} "
                f"nodes={summary['num_nodes']} elements={summary['num_elements']} "
                f"beams={summary['num_beams']} columns={summary['num_columns']}"
            )
        generated += 1

    if generated < args.n:
        print(f"[WARN] only generated {generated} models after {attempts} attempts.")


if __name__ == "__main__":
    main()




