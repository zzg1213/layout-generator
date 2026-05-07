#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate all required RC frame layouts deterministically."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any


STORY_COUNTS = [4, 6, 8, 9, 10, 12]
STORY_HEIGHT_MM = 3900.0
SPAN_LENGTHS_MM = [4500.0, 6000.0]
X_SPAN_COUNTS = [5, 6, 7, 8]
Y_SPAN_COUNTS = [4, 5, 6, 7]
LAYOUT_IDS = range(1, 10)
BASE_SEED = 20260506


def parse_args() -> argparse.Namespace:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Generate every supported RC frame layout JSON.",
    )
    parser.add_argument(
        "--outdir",
        type=str,
        default=str(base_dir / "out"),
        help="output directory (default: ./out under this script folder)",
    )
    return parser.parse_args()


def load_generator(layout_id: int, base_dir: Path) -> ModuleType:
    generator_path = base_dir / str(layout_id) / f"rc_frame_layout_generator{layout_id}.py"
    if not generator_path.exists():
        raise FileNotFoundError(f"generator not found: {generator_path}")

    spec = importlib.util.spec_from_file_location(f"rc_frame_layout_generator{layout_id}", generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load generator: {generator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def iter_span_count_pairs() -> list[tuple[int, int]]:
    return [
        (x_count, y_count)
        for x_count in X_SPAN_COUNTS
        for y_count in Y_SPAN_COUNTS
        if x_count >= y_count
    ]


def output_name(layout_id: int, story_count: int, x_count: int, y_count: int, span_mm: float) -> str:
    span_m = int(round(span_mm / 1000.0 * 10))
    return f"layout{layout_id}_s{story_count:02d}_x{x_count}_y{y_count}_span{span_m:02d}"


def build_config(module: ModuleType, story_count: int, x_count: int, y_count: int, span_mm: float) -> dict[str, Any]:
    return {
        "plane": "XY",
        "layout_type": module.DEFAULT_CONFIG.get("layout_type"),
        "num_stories": story_count,
        "story_height_mm": STORY_HEIGHT_MM,
        "num_spans": x_count,
        "num_spans_y": y_count,
        "span_length_x_mm": span_mm,
        "span_length_y_other_mm": span_mm,
        "visualize": {"enabled": False},
        "max_attempts": 1,
    }


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    outdir = Path(args.outdir)
    if not outdir.is_absolute():
        outdir = (base_dir / outdir).resolve()

    story_batches_dir = outdir / "story_batches"
    if story_batches_dir.exists():
        shutil.rmtree(story_batches_dir)
    story_batches_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    skipped: list[str] = []
    span_count_pairs = iter_span_count_pairs()

    for layout_id in LAYOUT_IDS:
        module = load_generator(layout_id, base_dir)
        device = module.torch.device("cuda" if module.torch.cuda.is_available() else "cpu")
        for story_count in STORY_COUNTS:
            story_dir = story_batches_dir / f"story_{story_count:02d}"
            story_dir.mkdir(parents=True, exist_ok=True)
            for x_count, y_count in span_count_pairs:
                for span_mm in SPAN_LENGTHS_MM:
                    cfg = build_config(module, story_count, x_count, y_count, span_mm)
                    name = output_name(layout_id, story_count, x_count, y_count, span_mm)
                    try:
                        model, _summary = module.build_model(cfg, BASE_SEED + layout_id * 1000, generated, device)
                    except Exception as exc:
                        skipped.append(f"{name}: {exc}")
                        continue

                    json_path = story_dir / f"{name}.json"
                    with json_path.open("w", encoding="utf-8") as file:
                        json.dump(model, file, indent=2)
                    generated += 1

    print(f"[DONE] generated={generated} skipped={len(skipped)} output={story_batches_dir}")
    for item in skipped:
        print(f"[SKIP] {item}")
    return 0 if not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
