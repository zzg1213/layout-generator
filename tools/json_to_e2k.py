from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


MM_TO_M = 0.001
MPA_TO_KN_PER_M2 = 1000.0
TOL = 1.0e-6
SLAB_THICKNESS_MM = 120.0
BEAM_LINE_LOAD_KN_PER_M = 8.5
LOAD_CASE = "DEAD"
SLAB_SECTION_NAME = "SLAB120"
REBAR_GRADE = "HRB400"
STANDARD_STORY_INDEX = 1


def fmt(value: float) -> str:
    if abs(value) < 1.0e-12:
        return "0"
    return f"{value:.12g}"


def quote(value: str) -> str:
    return f'"{value}"'


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def parse_story_label(label: str) -> tuple[int, int]:
    if "-" in label:
        start, end = label.split("-", 1)
        return int(start), int(end)
    value = int(label)
    return value, value


def story_name(story_index: int) -> str:
    return f"S{story_index}"


def natural_object_key(name: str) -> tuple[Any, ...]:
    parts = name.split("_")
    result: list[Any] = [parts[0]]
    for part in parts[1:]:
        try:
            result.append(int(part))
        except ValueError:
            result.append(part)
    result.append(name)
    return tuple(result)


def natural_node_key(name: str) -> tuple[Any, ...]:
    parts = name.split("_")
    result: list[Any] = [parts[0]]
    for part in parts[1:]:
        try:
            result.append(int(part))
        except ValueError:
            result.append(part)
    result.append(name)
    return tuple(result)


def node_indices(name: str) -> tuple[int, int] | None:
    parts = name.split("_")
    if len(parts) != 3 or parts[0] != "N":
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def coord_mm(endpoint: dict[str, Any]) -> tuple[float, float, float]:
    for axis in ("x", "y", "z"):
        require(axis in endpoint, f"endpoint missing {axis!r}: {endpoint}")
    return float(endpoint["x"]), float(endpoint["y"]), float(endpoint["z"])


def coord_key(endpoint: dict[str, Any]) -> tuple[int, int, int]:
    x, y, z = coord_mm(endpoint)
    return round(x * 1000), round(y * 1000), round(z * 1000)


def plan_key_from_node(node: dict[str, Any]) -> tuple[int, int]:
    return round(float(node["x"]) * 1000), round(float(node["y"]) * 1000)


def plan_key_from_endpoint(endpoint_key: tuple[int, int, int]) -> tuple[int, int]:
    return endpoint_key[0], endpoint_key[1]


def plan_coord_to_m(key: tuple[int, int]) -> tuple[float, float]:
    return key[0] / 1000.0 * MM_TO_M, key[1] / 1000.0 * MM_TO_M


def validate_story_grid(layout: dict[str, Any]) -> tuple[int, list[float]]:
    story_count = int(layout["story_count"])
    story_heights = [float(value) for value in layout["story_height"]]
    grid_z = [float(value) for value in layout["geometry"]["grid_z"]]
    require(len(story_heights) == story_count, "story_height length must match story_count.")
    require(len(grid_z) == story_count + 1, "geometry.grid_z must contain base plus every story top.")
    require(abs(grid_z[0]) <= TOL, "geometry.grid_z must start at 0.")

    cumulative = 0.0
    expected = [0.0]
    for height in story_heights:
        cumulative += height
        expected.append(cumulative)
    for index, (actual, expected_value) in enumerate(zip(grid_z, expected)):
        require(
            abs(actual - expected_value) <= TOL,
            f"grid_z[{index}]={actual} does not match cumulative height {expected_value}.",
        )
    return story_count, grid_z


def validate_active_cells(
    layout: dict[str, Any], grid_x: list[float], grid_y: list[float]
) -> list[tuple[int, int]]:
    raw_cells = layout["geometry"].get("active_cells")
    require(isinstance(raw_cells, list) and raw_cells, "geometry.active_cells is required.")
    max_i = len(grid_x) - 1
    max_j = len(grid_y) - 1
    cells: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for cell in raw_cells:
        require(isinstance(cell, list) and len(cell) == 2, f"invalid active cell: {cell!r}")
        i = int(cell[0])
        j = int(cell[1])
        require(0 <= i < max_i and 0 <= j < max_j, f"active cell {(i, j)} is outside the grid.")
        require((i, j) not in seen, f"duplicate active cell {(i, j)}.")
        seen.add((i, j))
        cells.append((i, j))
    return sorted(cells)


def validate_properties(layout: dict[str, Any]) -> None:
    slab = layout.get("SlabProperties")
    loads = layout.get("LoadProperties")
    material = layout["MaterialProperties"]
    reinforcement = layout.get("ReinforcementProperties")
    require(isinstance(slab, dict), "SlabProperties is required.")
    require(isinstance(loads, dict), "LoadProperties is required.")
    require(abs(float(slab.get("thickness_mm")) - SLAB_THICKNESS_MM) <= TOL, "invalid slab thickness.")
    require(
        abs(float(loads.get("beam_line_load_kn_per_m")) - BEAM_LINE_LOAD_KN_PER_M) <= TOL,
        "invalid beam line load.",
    )
    require(str(loads.get("load_case")) == LOAD_CASE, f"LoadProperties.load_case must be {LOAD_CASE}.")
    require(str(material.get("steel_grade")) == REBAR_GRADE, f"MaterialProperties.steel_grade must be {REBAR_GRADE}.")
    require(isinstance(reinforcement, dict), "ReinforcementProperties is required.")
    for key in ("beam_steel_grade", "column_steel_grade", "wall_steel_grade"):
        require(str(reinforcement.get(key)) == REBAR_GRADE, f"ReinforcementProperties.{key} must be {REBAR_GRADE}.")


def flatten_components(
    component_groups: dict[str, dict[str, dict[str, Any]]],
    component_type: str,
    story_count: int,
    section_names: set[str],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for label, objects in sorted(component_groups.items(), key=lambda item: parse_story_label(item[0])[0]):
        label_start, label_end = parse_story_label(label)
        require(label_start == label_end, f"{component_type} group {label} must be per-story.")
        require(1 <= label_start <= story_count, f"{component_type} group {label} is outside story range.")
        for name, item in sorted(objects.items(), key=lambda entry: natural_object_key(entry[0])):
            require("start" in item and "end" in item, f"{component_type} {name} is missing start/end coordinates.")
            story = int(item.get("story", label_start))
            require(story == label_start, f"{component_type} {name} story does not match group {label}.")
            require(item.get("section") in section_names, f"{component_type} {name} references missing section.")
            require(item.get("rebar_grade") == REBAR_GRADE, f"{component_type} {name} rebar_grade must be {REBAR_GRADE}.")
            start_key = coord_key(item["start"])
            end_key = coord_key(item["end"])
            require(start_key != end_key, f"{component_type} {name} has zero length.")
            component: dict[str, Any] = {
                "type": component_type,
                "name": f"{name}_S{story}",
                "story": story,
                "section": str(item["section"]),
                "rebar_grade": str(item["rebar_grade"]),
                "start": start_key,
                "end": end_key,
            }
            if component_type == "beam":
                line_load = item.get("line_load")
                require(isinstance(line_load, dict), f"beam {name} is missing line_load.")
                require(str(line_load.get("case")) == LOAD_CASE, f"beam {name} line_load.case must be {LOAD_CASE}.")
                require(str(line_load.get("direction")) == "Gravity", f"beam {name} line_load.direction must be Gravity.")
                require(
                    abs(float(line_load.get("value_kn_per_m")) - BEAM_LINE_LOAD_KN_PER_M) <= TOL,
                    f"beam {name} line_load.value_kn_per_m must be {BEAM_LINE_LOAD_KN_PER_M}.",
                )
                component["line_load"] = line_load
            components.append(component)
    return components


def validate_layout(layout: dict[str, Any]) -> dict[str, Any]:
    story_count, grid_z = validate_story_grid(layout)
    validate_properties(layout)
    sections = layout["sections"]
    section_names = set(sections)
    columns = flatten_components(layout["columns"], "column", story_count, section_names)
    beams = flatten_components(layout["beams"], "beam", story_count, section_names)
    grid_x = [float(value) for value in layout["geometry"]["grid_x"]]
    grid_y = [float(value) for value in layout["geometry"]["grid_y"]]
    active_cells = validate_active_cells(layout, grid_x, grid_y)

    for column in columns:
        require(column["end"][2] > column["start"][2], f"column {column['name']} must start below its end.")
    for beam in beams:
        require(abs(beam["start"][2] - beam["end"][2]) <= TOL, f"beam {beam['name']} must be horizontal.")

    return {
        "story_count": story_count,
        "grid_z": grid_z,
        "nodes": layout["nodes"],
        "sections": sections,
        "columns": columns,
        "beams": beams,
        "material": layout["MaterialProperties"],
        "grid_x": grid_x,
        "grid_y": grid_y,
        "active_cells": active_cells,
    }


def build_plan_point_map(nodes: dict[str, Any]) -> dict[tuple[int, int], str]:
    point_map: dict[tuple[int, int], str] = {}
    for node_name, node in sorted(nodes.items(), key=lambda item: natural_node_key(item[0])):
        point_map[plan_key_from_node(node)] = node_name
    return point_map


def build_floor_areas(
    nodes: dict[str, Any], active_cells: list[tuple[int, int]]
) -> dict[str, tuple[str, str, str, str]]:
    node_names_by_index: dict[tuple[int, int], str] = {}
    for node_name in nodes:
        index = node_indices(node_name)
        if index is not None:
            node_names_by_index[index] = node_name

    areas: dict[str, tuple[str, str, str, str]] = {}
    for i, j in active_cells:
        corners = ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))
        require(
            all(corner in node_names_by_index for corner in corners),
            f"active cell {(i, j)} references a missing corner node.",
        )
        areas[f"F_{i}_{j}"] = tuple(node_names_by_index[corner] for corner in corners)  # type: ignore[assignment]
    return areas


def expand_floor_areas_by_story(
    floor_areas: dict[str, tuple[str, str, str, str]], story_count: int
) -> list[tuple[str, str, tuple[str, str, str, str]]]:
    expanded: list[tuple[str, str, tuple[str, str, str, str]]] = []
    for story in range(1, story_count + 1):
        story_label = story_name(story)
        for area_name, corner_names in sorted(floor_areas.items(), key=lambda item: natural_object_key(item[0])):
            expanded.append((f"{area_name}_S{story}", story_label, corner_names))
    return expanded


def base_object_name(component_name: str, story: int) -> str:
    suffix = f"_S{story}"
    if component_name.endswith(suffix):
        return component_name[: -len(suffix)]
    return component_name


def collapse_story_objects(components: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    objects: dict[str, dict[str, Any]] = {}
    for component in components:
        object_name = base_object_name(component["name"], int(component["story"]))
        start_plan = plan_key_from_endpoint(component["start"])
        end_plan = plan_key_from_endpoint(component["end"])
        existing = objects.get(object_name)
        if existing is None:
            objects[object_name] = {
                "type": component["type"],
                "start_plan": start_plan,
                "end_plan": end_plan,
                "assignments": [],
            }
        else:
            require(existing["type"] == component["type"], f"object {object_name} changes type.")
            require(existing["start_plan"] == start_plan, f"object {object_name} changes start plan point.")
            require(existing["end_plan"] == end_plan, f"object {object_name} changes end plan point.")
        objects[object_name]["assignments"].append({"story": int(component["story"]), "section": component["section"]})
    return objects


def build_e2k(model: dict[str, Any], source_path: Path) -> str:
    material = model["material"]
    concrete_name = str(material["concrete_grade"])
    rebar_name = str(material["steel_grade"])
    e_concrete = float(material["E_c"]) * MPA_TO_KN_PER_M2
    e_rebar = float(material["E_s"]) * MPA_TO_KN_PER_M2
    fc = float(material["fc"]) * MPA_TO_KN_PER_M2
    fy = float(material["fy"]) * MPA_TO_KN_PER_M2
    point_map = build_plan_point_map(model["nodes"])
    column_objects = collapse_story_objects(model["columns"])
    beam_objects = collapse_story_objects(model["beams"])
    floor_areas = build_floor_areas(model["nodes"], model["active_cells"])
    floor_area_objects = expand_floor_areas_by_story(floor_areas, model["story_count"])
    assign_story = story_name(STANDARD_STORY_INDEX)

    lines: list[str] = [
        f"$ File {source_path.name} converted from JSON endpoints",
        "$ PROGRAM INFORMATION",
        '  PROGRAM "ETABS" VERSION "9.5.0"',
        "",
        "$ CONTROLS",
        '  UNITS "KN" "M"',
        f"  TITLE1 {quote(source_path.stem)}",
        "  PREFERENCE MERGETOL 0.001",
        "",
        "$ STORIES - IN SEQUENCE FROM TOP",
    ]
    for story in range(model["story_count"], 0, -1):
        height_m = (model["grid_z"][story] - model["grid_z"][story - 1]) * MM_TO_M
        lines.append(f"  STORY {quote(story_name(story))} HEIGHT {fmt(height_m)} MASTERSTORY \"Yes\"")
    lines.extend(['  STORY "BASE" ELEV 0', "", "$ GRIDS", '  GRIDSYSTEM "G1" TYPE "CARTESIAN" BUBBLESIZE 1.25'])
    for index, coord in enumerate(model["grid_x"], start=1):
        lines.append(f"  GRID \"G1\" LABEL \"X{index}\" DIR \"X\" COORD {fmt(coord * MM_TO_M)} VISIBLE \"Yes\" BUBBLELOC \"End\"")
    for index, coord in enumerate(model["grid_y"], start=1):
        lines.append(f"  GRID \"G1\" LABEL \"Y{index}\" DIR \"Y\" COORD {fmt(coord * MM_TO_M)} VISIBLE \"Yes\" BUBBLELOC \"Start\"")

    lines.extend(["", "$ MATERIAL PROPERTIES"])
    lines.append(f"  MATERIAL {quote(concrete_name)} TYPE \"Concrete\"")
    lines.append(f"  MATERIAL {quote(concrete_name)} SYMTYPE \"Isotropic\" E {fmt(e_concrete)}")
    lines.append(f"  MATERIAL {quote(concrete_name)} FC {fmt(fc)}")
    lines.append(f"  MATERIAL {quote(rebar_name)} TYPE \"Rebar\"")
    lines.append(f"  MATERIAL {quote(rebar_name)} SYMTYPE \"Uniaxial\" E {fmt(e_rebar)} FY {fmt(fy)}")

    lines.extend(["", "$ FRAME SECTIONS"])
    for section_name, section in model["sections"].items():
        width = float(section["b"]) * MM_TO_M
        depth = float(section["h"]) * MM_TO_M
        lines.append(
            f"  FRAMESECTION {quote(section_name)} MATERIAL {quote(concrete_name)} "
            f"SHAPE \"Concrete Rectangular\" D {fmt(depth)} B {fmt(width)}"
        )

    lines.extend(["", "$ CONCRETE SECTIONS"])
    cover = float(material.get("concrete_cover_mm", 20.0)) * MM_TO_M
    for section_name in model["sections"]:
        if section_name.lower().startswith("column"):
            lines.append(
                f"  CONCRETESECTION {quote(section_name)} LONGBARMATERIAL {quote(rebar_name)} "
                f"CONFINEBARMATERIAL {quote(rebar_name)} TYPE \"Column\" PATTERN \"R-3-3\" "
                f"TRANSREINF \"TIES\" DESIGNCHECK \"DESIGN\" COVER {fmt(cover)}"
            )
        else:
            lines.append(
                f"  CONCRETESECTION {quote(section_name)} LONGBARMATERIAL {quote(rebar_name)} "
                f"CONFINEBARMATERIAL {quote(rebar_name)} TYPE \"Beam\" COVERTOP {fmt(cover)} "
                f"COVERBOTTOM {fmt(cover)} ATI 0 ABI 0 ATJ 0 ABJ 0"
            )

    lines.extend([
        "",
        "$ WALL DESIGN PREFERENCES",
        f"  WALLPREFERENCE REBARMATERIAL {quote(rebar_name)} REBARSHEARMATERIAL {quote(rebar_name)}",
        "",
        "$ SLAB PROPERTIES",
        f"  SHELLPROP {quote(SLAB_SECTION_NAME)} PROPTYPE \"Slab\" MATERIAL {quote(concrete_name)} "
        f"MODELINGTYPE \"ShellThin\" SLABTYPE \"Slab\" SLABTHICKNESS {fmt(SLAB_THICKNESS_MM * MM_TO_M)}",
        "",
        "$ POINT COORDINATES",
    ])
    for key, point_name in sorted(point_map.items(), key=lambda item: natural_node_key(item[1])):
        x, y = plan_coord_to_m(key)
        lines.append(f"  POINT {quote(point_name)} {fmt(x)} {fmt(y)} 0")

    lines.extend(["", "$ LINE CONNECTIVITIES"])
    for object_name, obj in sorted(column_objects.items(), key=lambda item: natural_object_key(item[0])):
        point_name = point_map[obj["start_plan"]]
        lines.append(f"  LINE {quote(object_name)} COLUMN {quote(point_name)} {quote(point_name)} 1")
    for object_name, obj in sorted(beam_objects.items(), key=lambda item: natural_object_key(item[0])):
        lines.append(
            f"  LINE {quote(object_name)} BEAM {quote(point_map[obj['start_plan']])} "
            f"{quote(point_map[obj['end_plan']])} 0"
        )

    lines.extend(["", "$ AREA CONNECTIVITIES"])
    for area_name, _story_label, corner_names in floor_area_objects:
        p1, p2, p3, p4 = corner_names
        lines.append(f"  AREA {quote(area_name)} FLOOR 4 {quote(p1)} {quote(p2)} {quote(p3)} {quote(p4)} 0 0 0 0")

    lines.extend(["", "$ POINT ASSIGNS"])
    for _key, point_name in sorted(point_map.items(), key=lambda item: natural_node_key(item[1])):
        lines.append(f"  POINTASSIGN {quote(point_name)} \"BASE\" RESTRAINT \"UX UY UZ RX RY RZ\"")

    lines.extend(["", "$ LINE ASSIGNS"])
    for object_name, obj in sorted(column_objects.items(), key=lambda item: natural_object_key(item[0])):
        assignment = sorted(obj["assignments"], key=lambda item: item["story"])[0]
        lines.append(f"  LINEASSIGN {quote(object_name)} {quote(assign_story)} SECTION {quote(assignment['section'])} MINNUMSTA 3")
    for object_name, obj in sorted(beam_objects.items(), key=lambda item: natural_object_key(item[0])):
        assignment = sorted(obj["assignments"], key=lambda item: item["story"])[0]
        lines.append(f"  LINEASSIGN {quote(object_name)} {quote(assign_story)} SECTION {quote(assignment['section'])} MINNUMSTA 3")

    lines.extend(["", "$ AREA ASSIGNS"])
    for area_name, story_label, _corner_names in floor_area_objects:
        lines.append(f"  AREAASSIGN {quote(area_name)} {quote(story_label)} SECTION {quote(SLAB_SECTION_NAME)} OBJMESHTYPE \"DEFAULT\"")

    lines.extend(["", "$ LOAD PATTERNS", f"  LOADPATTERN {quote(LOAD_CASE)} TYPE \"Dead\" SELFWEIGHT 0", "", "$ FRAME OBJECT LOADS"])
    for object_name, _obj in sorted(beam_objects.items(), key=lambda item: natural_object_key(item[0])):
        lines.append(
            f"  LINELOAD {quote(object_name)} {quote(assign_story)} TYPE \"UNIFF\" "
            f"DIR \"GRAV\" LC {quote(LOAD_CASE)} FVAL {fmt(BEAM_LINE_LOAD_KN_PER_M)}"
        )
    lines.extend(["", "$ END"])
    return "\n".join(lines) + "\n"


def convert_file(json_path: Path, output_dir: Path) -> Path:
    with json_path.open("r", encoding="utf-8") as file:
        layout = json.load(file)
    model = validate_layout(layout)
    story_dir = output_dir / f"story_{model['story_count']:02d}"
    model_dir = story_dir / f"{model['story_count']}层{json_path.stem}"
    model_dir.mkdir(parents=True, exist_ok=True)
    output_path = model_dir / json_path.with_suffix(".e2k").name
    output_path.write_text(build_e2k(model, json_path), encoding="utf-8")
    print(
        f"[OK] {json_path.name} -> {model_dir.name}\\{output_path.name} | "
        f"points={len(build_plan_point_map(model['nodes']))} "
        f"columns={len(model['columns'])} beams={len(model['beams'])} "
        f"slabs={len(expand_floor_areas_by_story(build_floor_areas(model['nodes'], model['active_cells']), model['story_count']))} "
        f"beam_loads={len(model['beams'])}"
    )
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert layout JSON files to E2K files.")
    parser.add_argument("source_dir", help="directory containing layout JSON files")
    parser.add_argument("--out", required=True, help="directory for generated E2K files")
    parser.add_argument("--clean", action="store_true", help="remove the output directory before conversion")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source_dir).resolve()
    output_dir = Path(args.out).resolve()
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(path for path in source_dir.rglob("*.json") if path.is_file())
    require(json_files, f"No JSON files found in {source_dir}.")

    converted = 0
    failed: list[tuple[Path, str]] = []
    for json_path in json_files:
        try:
            convert_file(json_path, output_dir)
            converted += 1
        except Exception as exc:
            failed.append((json_path, str(exc)))

    print(
        f"[DONE] json={len(json_files)} e2k={converted} "
        f"skipped=0 failed={len(failed)} output={output_dir}"
    )
    for json_path, message in failed:
        print(f"[FAIL] {json_path}: {message}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
