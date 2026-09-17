from __future__ import annotations

import argparse
import json
import math
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = PROJECT_ROOT / "resources" / "emotes.json"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "resources" / "ui" / "opponent_card_bar" / "communication"
)
CELL_SIZE = 64
ATLAS_COLUMNS = 32
VISIBLE_ART_SIZE = 58


def _load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        item
        for item in payload.get("emotes", [])
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("sc_file"), str)
    ]


def _extract_sources(apk_path: Path, filenames: set[str], destination: Path) -> None:
    with zipfile.ZipFile(apk_path) as archive:
        members_by_name = {
            Path(member).name: member
            for member in archive.namelist()
            if member.startswith("assets/sc/")
        }
        missing = sorted(filenames.difference(members_by_name))
        if missing:
            raise RuntimeError(f"APK 缺少 {len(missing)} 个资源：{missing[:5]}")
        for filename in sorted(filenames):
            member = members_by_name[filename]
            target = destination / filename
            target.write_bytes(archive.read(member))


def _fit_to_cell(source: Image.Image) -> Image.Image:
    source = source.convert("RGBA")
    bounds = source.getbbox()
    if bounds is not None:
        source = source.crop(bounds)
    source.thumbnail((VISIBLE_ART_SIZE, VISIBLE_ART_SIZE), Image.Resampling.LANCZOS)
    cell = Image.new("RGBA", (CELL_SIZE, CELL_SIZE))
    cell.alpha_composite(
        source,
        ((CELL_SIZE - source.width) // 2, (CELL_SIZE - source.height) // 2),
    )
    return cell


def _placeholder_cell() -> Image.Image:
    cell = Image.new("RGBA", (CELL_SIZE, CELL_SIZE))
    draw = ImageDraw.Draw(cell)
    draw.rounded_rectangle((5, 5, 58, 58), radius=13, fill=(233, 240, 245, 255))
    draw.ellipse((18, 13, 46, 41), fill=(245, 245, 231, 255), outline=(64, 74, 84, 255), width=2)
    draw.ellipse((24, 22, 29, 29), fill=(45, 48, 52, 255))
    draw.ellipse((35, 22, 40, 29), fill=(45, 48, 52, 255))
    draw.line((24, 46, 40, 46), fill=(64, 74, 84, 255), width=3)
    return cell


def _render_export(sc: Any, textures: list[Image.Image | None], name: str) -> Image.Image | None:
    for frame_label in ("stop", None):
        image = sc.extract_sprite(name, textures, frame_label=frame_label)
        if image is not None:
            return image

    frame_info = sc.get_export_frame_info(name) or {}
    candidates: list[Image.Image] = []
    for frame_index in range(int(frame_info.get("frame_count") or 0)):
        image = sc.extract_sprite(name, textures, frame_index=frame_index)
        if image is not None:
            candidates.append(image)
    return max(
        candidates,
        key=lambda image: (
            0 if image.getbbox() is None else image.getbbox()[2] * image.getbbox()[3],
            image.width * image.height,
        ),
        default=None,
    )


def build_atlas(apk_path: Path, catalog_path: Path, output_root: Path) -> dict[str, Any]:
    try:
        from sc5_parser.parser import SC5File
        from sc5_parser.sctx import decode_sctx
    except ImportError as exc:
        raise RuntimeError(
            "需要安装 sc5-parser：https://github.com/obus-globus/sc5-parser"
        ) from exc

    entries = _load_catalog(catalog_path)
    by_sc_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in entries:
        by_sc_file[Path(item["sc_file"]).name].append(item)

    rendered: list[tuple[dict[str, Any], Image.Image, str]] = []
    failures: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="nrpp-emote-atlas-") as directory:
        source_root = Path(directory)
        _extract_sources(apk_path, set(by_sc_file), source_root)
        parsed_files = {}
        for sc_filename, items in by_sc_file.items():
            try:
                parsed_files[sc_filename] = SC5File(source_root / sc_filename)
            except Exception as exc:
                failures.append(
                    {
                        "sc_file": sc_filename,
                        "ids": [item["id"] for item in items],
                        "error": str(exc),
                    }
                )
        texture_files = {
            texture["external"]
            for sc in parsed_files.values()
            for texture in sc.textures
            if texture.get("external")
        }
        _extract_sources(apk_path, texture_files, source_root)
        for sc_filename, items in by_sc_file.items():
            if sc_filename not in parsed_files:
                continue
            try:
                sc = parsed_files[sc_filename]
                textures = []
                for texture in sc.textures:
                    texture_path = source_root / texture["external"]
                    textures.append(
                        decode_sctx(str(texture_path)) if texture_path.is_file() else None
                    )
                exports_by_casefold = {
                    name.casefold(): name for name in sc.exports
                }
                for item in items:
                    slot = item.get("index_lo")
                    slot = slot + 1 if isinstance(slot, int) and slot >= 0 else 1
                    requested = [f"icon{slot}", f"emote{slot}"]
                    if len(items) == 1 or slot == 1:
                        requested.extend(("icon", "emote"))
                    image = None
                    export_name = None
                    for candidate in requested:
                        actual = exports_by_casefold.get(candidate.casefold())
                        if actual is None:
                            continue
                        candidate_image = _render_export(sc, textures, actual)
                        if candidate_image is not None:
                            image = candidate_image
                            export_name = actual
                            break
                    if image is None or export_name is None:
                        failures.append(
                            {
                                "sc_file": sc_filename,
                                "ids": [item["id"]],
                                "error": (
                                    "无法渲染缩略图；尝试了 "
                                    + ", ".join(requested)
                                ),
                            }
                        )
                        continue
                    rendered.append((item, _fit_to_cell(image), export_name))
            except Exception as exc:  # keep a complete audit instead of hiding gaps
                failures.append(
                    {
                        "sc_file": sc_filename,
                        "ids": [item["id"] for item in items],
                        "error": str(exc),
                    }
                )

    rendered_ids = {item["id"] for item, _image, _export in rendered}
    legacy_root = output_root / "legacy"
    for item in entries:
        if item["id"] in rendered_ids or item.get("available") is False:
            continue
        fallback = legacy_root / f"{item['id']}.png"
        if fallback.is_file():
            image = _fit_to_cell(Image.open(fallback))
            export_name = "legacy-image"
        else:
            image = _placeholder_cell()
            export_name = "generated-placeholder"
        rendered.append((item, image, export_name))
        rendered_ids.add(item["id"])

    rows = math.ceil(len(rendered) / ATLAS_COLUMNS)
    atlas = Image.new(
        "RGBA", (ATLAS_COLUMNS * CELL_SIZE, max(1, rows) * CELL_SIZE)
    )
    manifest_entries: list[dict[str, Any]] = []
    for index, (item, image, export_name) in enumerate(rendered):
        column = index % ATLAS_COLUMNS
        row = index // ATLAS_COLUMNS
        position = (column * CELL_SIZE, row * CELL_SIZE)
        atlas.alpha_composite(image, position)
        manifest_entries.append(
            {
                "id": item["id"],
                "name": item.get("human_readable_name") or item.get("name") or item["id"],
                "family": item.get("family") or "",
                "available": item.get("available") is not False,
                "rect": [position[0], position[1], CELL_SIZE, CELL_SIZE],
                "sc_file": Path(item["sc_file"]).name,
                "export": export_name,
            }
        )

    output_root.mkdir(parents=True, exist_ok=True)
    atlas_path = output_root / "emotes_atlas.png"
    manifest_path = output_root / "emotes_atlas.json"
    atlas.save(atlas_path, optimize=True)
    manifest = {
        "schema_version": 1,
        "cell_size": CELL_SIZE,
        "columns": ATLAS_COLUMNS,
        "entries": manifest_entries,
        "failures": failures,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def supplement_existing_atlas(catalog_path: Path, output_root: Path) -> dict[str, Any]:
    atlas_path = output_root / "emotes_atlas.png"
    manifest_path = output_root / "emotes_atlas.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    atlas = Image.open(atlas_path).convert("RGBA")
    manifest_entries = manifest.get("entries", [])
    rendered_ids = {
        item.get("id") for item in manifest_entries if isinstance(item, dict)
    }
    additions: list[tuple[dict[str, Any], Image.Image, str]] = []
    legacy_root = output_root / "legacy"
    for item in _load_catalog(catalog_path):
        if item["id"] in rendered_ids or item.get("available") is False:
            continue
        fallback = legacy_root / f"{item['id']}.png"
        if fallback.is_file():
            image = _fit_to_cell(Image.open(fallback))
            export_name = "legacy-image"
        else:
            image = _placeholder_cell()
            export_name = "generated-placeholder"
        additions.append((item, image, export_name))

    final_count = len(manifest_entries) + len(additions)
    rows = max(1, math.ceil(final_count / ATLAS_COLUMNS))
    if atlas.height < rows * CELL_SIZE:
        expanded = Image.new("RGBA", (ATLAS_COLUMNS * CELL_SIZE, rows * CELL_SIZE))
        expanded.alpha_composite(atlas)
        atlas = expanded
    for item, image, export_name in additions:
        index = len(manifest_entries)
        position = (
            index % ATLAS_COLUMNS * CELL_SIZE,
            index // ATLAS_COLUMNS * CELL_SIZE,
        )
        atlas.alpha_composite(image, position)
        manifest_entries.append(
            {
                "id": item["id"],
                "name": item.get("human_readable_name") or item.get("name") or item["id"],
                "family": item.get("family") or "",
                "available": True,
                "rect": [position[0], position[1], CELL_SIZE, CELL_SIZE],
                "sc_file": Path(item["sc_file"]).name,
                "export": export_name,
            }
        )
    atlas.save(atlas_path, optimize=True)
    manifest["entries"] = manifest_entries
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the communication-page emote thumbnail atlas from a game APK"
    )
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    manifest = (
        build_atlas(args.apk, args.catalog, args.output_root)
        if args.apk is not None
        else supplement_existing_atlas(args.catalog, args.output_root)
    )
    print(
        json.dumps(
            {
                "entries": len(manifest["entries"]),
                "failures": len(manifest["failures"]),
                "output_root": str(args.output_root),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
