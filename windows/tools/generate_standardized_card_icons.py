"""Generate the production-v2 standardized Clash Royale card-control icon library.

The module exposes one deep interface, ``generate_library``. It expands the
audited catalog, renders every state, writes master/runtime assets, and returns
the complete output manifest plus validation report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


SCHEMA_VERSION = 1
PRODUCTION_VERSION = "production-v2"
CANVAS = (384, 512)
RUNTIME_SIZE = (104, 139)
SOURCE_RECT = (27, 13, 357, 499)
COST_CENTER = (192, 456)
COST_BADGE_SIZE = (112, 112)
COST_BADGE_TOP_OFFSET = 57
COST_TEMPLATE_NAMES = tuple(f"cost_{value}.png" for value in range(1, 10)) + ("cost_unknown.png",)
EVOLUTION_TEMPLATE_NAMES = tuple(
    f"tray_{charge}_of_{cycles}.png"
    for cycles in (1, 2)
    for charge in range(cycles + 1)
)
TRAY_SIZES = {"standard": (252, 104), "legendary": (250, 121)}
TRAY_Y = {"standard": 12, "legendary": 16}
EVOLUTION_SLOT_DIAMETER = 58
EVOLUTION_SLOT_OFFSET = 42
EVOLUTION_SLOT_SIZE = (70, 64)

# Fixed silhouettes make the luminous frame independent of the source PNG's
# transparent padding.  In particular, Firecracker and Skeleton Army must
# render with exactly the same ready-state border geometry.
STANDARD_READY_RECT = (57, 108, 327, 442)
STANDARD_READY_RADIUS = 20
LEGENDARY_READY_POLYGON = [
    (192, 94), (323, 134), (313, 394),
    (192, 445), (71, 394), (61, 134),
]
LEGENDARY_ARTWORK_CROP = (84, 148, 300, 410)
STANDARD_ARTWORK_RECT = (69, 122, 315, 425)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (Path("C:/Windows/Fonts/arialbd.ttf"), Path("C:/Windows/Fonts/impact.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fit_source(path: Path) -> tuple[Image.Image, tuple[int, int]]:
    with Image.open(path) as image:
        source = image.convert("RGBA")
    maximum = (SOURCE_RECT[2] - SOURCE_RECT[0], SOURCE_RECT[3] - SOURCE_RECT[1])
    source.thumbnail(maximum, Image.Resampling.LANCZOS)
    return source, (
        SOURCE_RECT[0] + (maximum[0] - source.width) // 2,
        SOURCE_RECT[1] + (maximum[1] - source.height) // 2,
    )


def _source_canvas(path: Path) -> Image.Image:
    source, position = _fit_source(path)
    canvas = Image.new("RGBA", CANVAS)
    canvas.alpha_composite(source, position)
    return canvas


def _interior_geometry(frame_class: str) -> tuple[list[tuple[int, int]] | None, tuple[int, int, int, int]]:
    if frame_class in {"legendary", "champion"}:
        polygon = [
            (92, 150), (192, 126), (292, 150), (325, 158), (310, 389),
            (270, 430), (114, 430), (74, 389), (59, 158),
        ]
        return polygon, (59, 126, 325, 430)
    return None, (57, 110, 327, 441)


def _interior_mask(frame_class: str, variant_kind: str) -> Image.Image:
    polygon, destination = _interior_geometry(frame_class)
    mask = Image.new("L", CANVAS)
    draw = ImageDraw.Draw(mask)
    if polygon is None:
        if variant_kind in {"hero", "evolution"}:
            destination = STANDARD_ARTWORK_RECT
        draw.rounded_rectangle(destination, radius=18, fill=255)
    else:
        if variant_kind == "hero":
            polygon = [
                (92, 150), (192, 126), (292, 150), (325, 158), (310, 389),
                (270, 430), (114, 430), (74, 389), (59, 158),
            ]
        draw.polygon(polygon, fill=255)
    return mask


def _header_ui_masks(source: Image.Image, position: tuple[int, int]) -> tuple[Image.Image, Image.Image]:
    """Return separate masks for the external tray and overlapping diamond.

    API Evolution/Hero art contains a tray behind the native card frame plus a
    central diamond that overlaps the artwork.  Treating the whole upper card
    as one repair band samples transparent black and destroys the portrait's
    top edge.  The tray can be restored from the normal card; only the diamond
    needs local inpainting.
    """
    x, y = position
    width, height = source.size
    base = Image.new("L", CANVAS)
    ImageDraw.Draw(base).polygon(
        [
            (x + round(width * 0.14), y),
            (x + round(width * 0.86), y),
            (x + round(width * 0.93), y + round(height * 0.24)),
            (x + round(width * 0.07), y + round(height * 0.24)),
        ],
        fill=255,
    )
    center_x = x + round(width * 0.50)
    center_y = y + round(height * 0.21)
    radius_x = round(width * 0.19)
    radius_y = round(height * 0.15)
    diamond = Image.new("L", CANVAS)
    ImageDraw.Draw(diamond).polygon(
        [
            (center_x, center_y - radius_y),
            (center_x + radius_x, center_y),
            (center_x, center_y + radius_y),
            (center_x - radius_x, center_y),
        ],
        fill=255,
    )
    return base, diamond


def _repair_diamond_horizontally(image: Image.Image, mask: Image.Image) -> Image.Image:
    """Fill each masked row from clean pixels on its left and right edges."""
    pixels = np.asarray(image.convert("RGB")).copy()
    mask_pixels = np.asarray(mask, dtype=np.uint8)
    for y in range(mask_pixels.shape[0]):
        xs = np.flatnonzero(mask_pixels[y])
        if xs.size == 0:
            continue
        left, right = int(xs[0]), int(xs[-1])
        sample_left = max(0, left - 10)
        sample_right = min(pixels.shape[1] - 1, right + 10)
        left_color = pixels[y, max(0, sample_left - 2):sample_left + 3].mean(axis=0)
        right_color = pixels[y, max(0, sample_right - 2):sample_right + 3].mean(axis=0)
        weights = np.linspace(0.0, 1.0, right - left + 1, dtype=np.float32)[:, None]
        fill = left_color[None, :] * (1.0 - weights) + right_color[None, :] * weights
        pixels[y, left:right + 1] = np.clip(fill, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels, mode="RGB").convert("RGBA")


def _composite_variant_body(normal_path: Path, variant_path: Path, frame_class: str, variant_kind: str) -> Image.Image:
    normal = _source_canvas(normal_path)
    variant = Image.new("RGBA", CANVAS)
    with Image.open(variant_path) as source_file:
        original_source = source_file.convert("RGBA")
    exceptional_standard_source = frame_class == "standard" and original_source.size != (285, 420)
    if exceptional_standard_source:
        # Some corrected API assets (notably Skeleton Army) are already framed
        # ready-state crops with different dimensions. Extract their artwork
        # instead of nesting the baked luminous frame inside the normal frame.
        width, height = original_source.size
        crop = (
            round(width * 0.13), round(height * 0.17),
            round(width * 0.87), round(height * 0.92),
        )
        artwork = original_source.crop(crop).resize(
            (STANDARD_ARTWORK_RECT[2] - STANDARD_ARTWORK_RECT[0], STANDARD_ARTWORK_RECT[3] - STANDARD_ARTWORK_RECT[1]),
            Image.Resampling.LANCZOS,
        )
        variant.alpha_composite(artwork, STANDARD_ARTWORK_RECT[:2])
        variant_source = artwork
        variant_position = STANDARD_ARTWORK_RECT[:2]
    else:
        variant_source, variant_position = _fit_source(variant_path)
        variant.alpha_composite(variant_source, variant_position)

    interior = _interior_mask(frame_class, variant_kind)
    _base_mask, diamond_mask = _header_ui_masks(variant_source, variant_position)
    if exceptional_standard_source:
        diamond_mask = Image.new("L", CANVAS)
        ImageDraw.Draw(diamond_mask).polygon(
            [(192, 105), (240, 155), (192, 205), (144, 155)],
            fill=255,
        )
    # The tray is behind the source card frame and disappears naturally when
    # variant content is clipped to the frame interior.  Only its central
    # diamond overlaps the artwork and requires reconstruction.
    repair_mask = ImageChops.multiply(interior, diamond_mask)
    repaired = _repair_diamond_horizontally(variant, repair_mask)
    repaired.putalpha(variant.getchannel("A"))
    repaired.putalpha(ImageChops.multiply(interior, repaired.getchannel("A")))

    body_canvas = repaired
    normal.alpha_composite(body_canvas)
    return normal


def _ready_silhouette_mask(frame_class: str) -> Image.Image:
    mask = Image.new("L", CANVAS)
    draw = ImageDraw.Draw(mask)
    if frame_class == "legendary":
        draw.polygon(LEGENDARY_READY_POLYGON, fill=255)
    else:
        draw.rounded_rectangle(STANDARD_READY_RECT, radius=STANDARD_READY_RADIUS, fill=255)
    return mask


def _legendary_ready_body(variant_path: Path) -> Image.Image:
    source, position = _fit_source(variant_path)
    variant = Image.new("RGBA", CANVAS)
    variant.alpha_composite(source, position)
    _base_mask, diamond_mask = _header_ui_masks(source, position)
    repaired = _repair_diamond_horizontally(variant, diamond_mask)
    repaired.putalpha(variant.getchannel("A"))
    target_mask = _ready_silhouette_mask("legendary")
    target_bbox = target_mask.getbbox()
    if target_bbox is None:
        raise ValueError("Empty Legendary ready-state silhouette")
    artwork = repaired.crop(LEGENDARY_ARTWORK_CROP).convert("RGB").resize(
        (target_bbox[2] - target_bbox[0], target_bbox[3] - target_bbox[1]),
        Image.Resampling.LANCZOS,
    )
    body = Image.new("RGBA", CANVAS)
    body.alpha_composite(artwork.convert("RGBA"), target_bbox[:2])
    body.putalpha(target_mask)
    return body


def _resize_tray_preserving_slots(sprite: Image.Image, family: str, cycles: int) -> Image.Image:
    """Resize tray flanks while keeping every diamond uniformly scaled.

    The approved one-slot Standard artwork is narrower than the two-slot
    artwork. Stretching the whole bitmap flattened its diamond. This nine-slice
    style resize expands only the left and right base flanks.
    """
    target_width, target_height = TRAY_SIZES[family]
    scale = target_height / sprite.height
    scaled = sprite.resize((round(sprite.width * scale), target_height), Image.Resampling.LANCZOS)
    if scaled.width == target_width:
        return scaled

    protected_width = min(
        scaled.width,
        round(target_height * (0.68 if cycles == 1 else 1.58)),
    )
    source_left = (scaled.width - protected_width) // 2
    source_right = source_left + protected_width
    destination_left = (target_width - protected_width) // 2
    destination_right = destination_left + protected_width

    output = Image.new("RGBA", (target_width, target_height))
    if destination_left:
        left = scaled.crop((0, 0, source_left, target_height)).resize(
            (destination_left, target_height), Image.Resampling.LANCZOS
        )
        output.alpha_composite(left, (0, 0))
    output.alpha_composite(scaled.crop((source_left, 0, source_right, target_height)), (destination_left, 0))
    if destination_right < target_width:
        right = scaled.crop((source_right, 0, scaled.width, target_height)).resize(
            (target_width - destination_right, target_height), Image.Resampling.LANCZOS
        )
        output.alpha_composite(right, (destination_right, 0))
    return output


def _slot_centers(width: int, cycles: int) -> list[tuple[int, int]]:
    center = width // 2
    xs = [center] if cycles == 1 else [center - EVOLUTION_SLOT_OFFSET, center + EVOLUTION_SLOT_OFFSET]
    return [(x, 52) for x in xs]


def _diamond_mask(size: tuple[int, int], center: tuple[int, int], radius: int) -> Image.Image:
    x, y = center
    mask = Image.new("L", size)
    ImageDraw.Draw(mask).polygon([(x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y)], fill=255)
    return mask.filter(ImageFilter.GaussianBlur(0.7))


def _raw_tray(template_root: Path, family: str, cycles: int, charge: int) -> Image.Image:
    path = template_root / "evolution" / family / f"tray_{charge}_of_{cycles}.png"
    with Image.open(path) as image:
        sprite = image.convert("RGBA")
    bbox = sprite.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError(f"Empty Evolution tray template: {path}")
    return _resize_tray_preserving_slots(sprite.crop(bbox), family, cycles)


def _canonical_slot_components(template_root: Path) -> tuple[Image.Image, Image.Image]:
    """Load the user-approved active/inactive slots at one exact size."""
    components = []
    for name in ("slot_active.png", "slot_inactive.png"):
        path = template_root / "evolution" / "slots" / name
        if not path.is_file():
            raise FileNotFoundError(f"Missing standardized Evolution slot: {path}")
        with Image.open(path) as file:
            component = file.convert("RGBA")
        if component.size != EVOLUTION_SLOT_SIZE or component.getchannel("A").getextrema() != (0, 255):
            raise ValueError(f"Evolution slot must be 70x64 RGBA with transparency: {path}")
        components.append(component)
    return components[0], components[1]


def _normalize_tray_slots(
    tray: Image.Image,
    template_root: Path,
    cycles: int,
    charge: int,
) -> Image.Image:
    active, inactive = _canonical_slot_components(template_root)
    centers = _slot_centers(tray.width, cycles)
    removal = Image.new("L", tray.size)
    for center in centers:
        removal = ImageChops.lighter(removal, _diamond_mask(tray.size, center, EVOLUTION_SLOT_DIAMETER // 2 + 5))
    cleaned = _repair_diamond_horizontally(tray, removal)
    cleaned.putalpha(tray.getchannel("A"))
    half_width, half_height = EVOLUTION_SLOT_SIZE[0] // 2, EVOLUTION_SLOT_SIZE[1] // 2
    for index, center in enumerate(centers):
        component = active if index < charge else inactive
        cleaned.alpha_composite(component, (center[0] - half_width, center[1] - half_height))
    return cleaned


def _approved_slot_centers(image: Image.Image, cycles: int) -> list[tuple[int, int]]:
    rgba = np.asarray(image.convert("RGBA"))
    red, green, blue, alpha = (rgba[..., index] for index in range(4))
    yy, xx = np.indices(red.shape)
    gold = (red > 180) & (green > 65) & (green < 235) & (blue < 145) & (alpha > 80) & (yy < 115)
    ranges = [(95, 190), (194, 289)] if cycles == 2 else [(135, 249)]
    centers = []
    for left, right in ranges:
        points = np.argwhere(gold[:, left:right])
        if points.size == 0:
            raise ValueError("Could not locate active diamond in approved ready-state master")
        top, local_left = points.min(axis=0)
        bottom, local_right = points.max(axis=0)
        centers.append((left + int((local_left + local_right) // 2), int((top + bottom) // 2)))
    return centers


def _normalize_approved_ready_slots(image: Image.Image, template_root: Path, cycles: int) -> Image.Image:
    active, _inactive = _canonical_slot_components(template_root)
    centers = _approved_slot_centers(image, cycles)
    removal = Image.new("L", CANVAS)
    for center in centers:
        removal = ImageChops.lighter(removal, _diamond_mask(CANVAS, center, 39))
    cleaned = _repair_diamond_horizontally(image, removal)
    cleaned.putalpha(image.getchannel("A"))
    half_width, half_height = EVOLUTION_SLOT_SIZE[0] // 2, EVOLUTION_SLOT_SIZE[1] // 2
    for center in centers:
        cleaned.alpha_composite(active, (center[0] - half_width, center[1] - half_height))
    return cleaned


def _tray_layer(template_root: Path, frame_class: str, cycles: int, charge: int) -> Image.Image:
    family = "legendary" if frame_class == "legendary" else "standard"
    sprite = _normalize_tray_slots(_raw_tray(template_root, family, cycles, charge), template_root, cycles, charge)
    layer = Image.new("RGBA", CANVAS)
    y = TRAY_Y[family]
    layer.alpha_composite(sprite, ((CANVAS[0] - sprite.width) // 2, y))
    return layer


def _ready_border_layers(frame_class: str) -> tuple[Image.Image, Image.Image]:
    """Return the approved soft outer glow and thin purple-white core edge."""
    silhouette = _ready_silhouette_mask(frame_class)
    expanded = silhouette.filter(ImageFilter.MaxFilter(15))
    contracted = silhouette.filter(ImageFilter.MinFilter(11))
    broad_ring = ImageChops.subtract(expanded, contracted)

    glow_alpha = broad_ring.filter(ImageFilter.GaussianBlur(12)).point(lambda value: min(88, round(value * 0.42)))
    glow = Image.new("RGBA", CANVAS, (237, 24, 255, 0))
    glow.putalpha(glow_alpha)

    purple_ring = ImageChops.subtract(
        silhouette.filter(ImageFilter.MaxFilter(9)),
        silhouette.filter(ImageFilter.MinFilter(9)),
    )
    core_ring = ImageChops.subtract(
        silhouette.filter(ImageFilter.MaxFilter(5)),
        silhouette.filter(ImageFilter.MinFilter(5)),
    )
    outline = Image.new("RGBA", CANVAS, (228, 18, 255, 0))
    outline.putalpha(purple_ring.point(lambda value: round(value * 0.95)))
    core = Image.new("RGBA", CANVAS, (255, 236, 255, 0))
    core.putalpha(core_ring)
    outline.alpha_composite(core)
    return glow, outline


def _cost_template_name(cost: int | None) -> str:
    return f"cost_{cost}.png" if isinstance(cost, int) and 1 <= cost <= 9 else "cost_unknown.png"


def _load_cost_templates(template_root: Path) -> dict[str, Image.Image]:
    templates: dict[str, Image.Image] = {}
    for name in COST_TEMPLATE_NAMES:
        path = template_root / name
        if not path.is_file():
            raise FileNotFoundError(f"Missing formal cost-badge template: {path}")
        with Image.open(path) as source:
            if source.mode != "RGBA" or source.size != COST_BADGE_SIZE:
                raise ValueError(
                    f"Cost-badge template must be {COST_BADGE_SIZE[0]}x{COST_BADGE_SIZE[1]} RGBA: {path}"
                )
            badge = source.copy()
        if badge.getchannel("A").getextrema() != (0, 255):
            raise ValueError(f"Cost-badge template must contain real transparency: {path}")
        templates[name] = badge
    return templates


def _cost_layer(templates: dict[str, Image.Image], cost: int | None) -> Image.Image:
    layer = Image.new("RGBA", CANVAS)
    cx, cy = COST_CENTER
    badge = templates[_cost_template_name(cost)]
    layer.alpha_composite(badge, (cx - badge.width // 2, cy - COST_BADGE_TOP_OFFSET))
    return layer


class _Renderer:
    def __init__(self, standardized_root: Path, template_root: Path, cost_template_root: Path | None = None) -> None:
        self.standardized_root = standardized_root
        self.template_root = template_root
        self.cost_template_root = cost_template_root or standardized_root / "templates" / "production-v2" / "cost"
        self.cost_templates = _load_cost_templates(self.cost_template_root)
        self.approved_ready_root = standardized_root / "templates" / "production-v2" / "approved-ready"

    def _source_path(self, card: dict[str, Any], variant: str) -> Path:
        source = card["sources"][variant]
        return self.standardized_root / source["path"]

    def render(self, card: dict[str, Any], state: dict[str, Any]) -> Image.Image:
        form = state["form"]
        normal_path = self._source_path(card, "normal")
        cycles = state.get("cycles")
        charge = state.get("charge")
        ready = form == "evolution" and charge == cycles

        if ready:
            approved = self.approved_ready_root / f"{int(card['canonicalCardId'])}.png"
            if approved.is_file():
                with Image.open(approved) as source:
                    if source.mode != "RGBA" or source.size != CANVAS:
                        raise ValueError(f"Approved ready-state master must be 384x512 RGBA: {approved}")
                    return _normalize_approved_ready_slots(source.copy(), self.template_root, int(cycles))

        if form == "hero":
            body = _composite_variant_body(normal_path, self._source_path(card, "hero"), card["frameClass"], "hero")
        elif ready:
            if card["frameClass"] == "legendary":
                body = _legendary_ready_body(self._source_path(card, "evolution"))
            else:
                body = _composite_variant_body(normal_path, self._source_path(card, "evolution"), card["frameClass"], "evolution")
        else:
            body = _source_canvas(normal_path)

        canvas = Image.new("RGBA", CANVAS)
        if ready:
            glow, outline = _ready_border_layers(card["frameClass"])
            canvas.alpha_composite(glow)
        if form == "evolution":
            canvas.alpha_composite(_tray_layer(self.template_root, card["frameClass"], int(cycles), int(charge)))
        canvas.alpha_composite(body)
        if ready:
            canvas.alpha_composite(outline)
        canvas.alpha_composite(_cost_layer(self.cost_templates, card["elixirCost"]))
        return canvas


def _expanded_states(card: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for form_spec in card["states"]:
        form = form_spec["form"]
        if form == "normal":
            result.append({"form": "normal", "key": "normal", "filename": "normal.png"})
        elif form == "hero":
            result.append({"form": "hero", "key": "hero", "filename": "hero.png"})
        elif form == "evolution":
            cycles = int(form_spec["cycles"])
            for state in form_spec["states"]:
                key = str(state["key"])
                result.append(
                    {
                        "form": "evolution",
                        "key": key,
                        "charge": int(state["charge"]),
                        "cycles": cycles,
                        "filename": f"{key}.png",
                    }
                )
        else:
            raise ValueError(f"Unsupported form {form!r} for card {card['canonicalCardId']}")
    return result


def _save_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def _contact_sheet(
    entries: list[dict[str, Any]],
    output_root: Path,
    destination: Path,
    title: str,
    columns: int = 10,
) -> None:
    cell_width, cell_height, header_height = 142, 195, 52
    rows = max(1, (len(entries) + columns - 1) // columns)
    sheet = Image.new("RGBA", (columns * cell_width, header_height + rows * cell_height), (20, 27, 40, 255))
    draw = ImageDraw.Draw(sheet)
    draw.text((18, 12), title, font=_font(23), fill="white")
    label_font = _font(11)
    for index, entry in enumerate(entries):
        col, row = index % columns, index // columns
        x = col * cell_width + (cell_width - RUNTIME_SIZE[0]) // 2
        y = header_height + row * cell_height
        with Image.open(output_root / entry["runtimePath"]) as image:
            icon = image.convert("RGBA")
        sheet.alpha_composite(icon, (x, y))
        label = f"{entry['cardId']}\n{entry['state']}"
        draw.multiline_text((col * cell_width + 5, y + 142), label, font=label_font, fill=(230, 236, 246), spacing=1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, format="PNG", optimize=True)


def _write_specs(output_root: Path, template_root: Path, cost_template_root: Path) -> None:
    spec_root = output_root / "spec"
    spec_root.mkdir(parents=True, exist_ok=True)
    geometry = {
        "status": PRODUCTION_VERSION,
        "masterCanvas": {"width": 384, "height": 512, "mode": "RGBA", "colorSpace": "sRGB"},
        "runtime": {"width": 104, "height": 139, "resampling": "Lanczos"},
        "sourceRect": {"left": 27, "top": 13, "right": 357, "bottom": 499},
        "costCenter": list(COST_CENTER),
        "costBadge": {
            "width": COST_BADGE_SIZE[0],
            "height": COST_BADGE_SIZE[1],
            "templateDirectory": "templates/cost",
            "unknownFallback": "cost_unknown.png",
        },
        "evolutionTray": {
            "templateDirectory": "templates/evolution",
            "standardSize": list(TRAY_SIZES["standard"]),
            "legendarySize": list(TRAY_SIZES["legendary"]),
            "oneAndTwoSlotSameWidth": True,
            "standardTop": TRAY_Y["standard"],
            "legendaryTop": TRAY_Y["legendary"],
            "slotSize": list(EVOLUTION_SLOT_SIZE),
            "slotGeometryVariesByFamilyOrCharge": False,
        },
        "readyBorder": {
            "standardRect": list(STANDARD_READY_RECT),
            "standardRadius": STANDARD_READY_RADIUS,
            "legendaryPolygon": [list(point) for point in LEGENDARY_READY_POLYGON],
            "legendaryInnerFrame": "none",
            "geometryVariesByCard": False,
        },
        "reviewedDeckLayout": {
            "panelWidth": 380,
            "columnX": [6, 94, 182, 270],
            "columnStride": 88,
            "minimumVisibleGap": 5,
            "columns": 4,
        },
    }
    (spec_root / f"geometry.{PRODUCTION_VERSION}.json").write_text(json.dumps(geometry, indent=2) + "\n", encoding="utf-8")
    visual_rules = f"""# Card icon visual rules — {PRODUCTION_VERSION}

1. Normal uses the audited `medium` source and preserves its native frame.
2. Standard, Legendary, and Champion silhouettes are never replaced by a generic frame.
3. Evolution charging states use normal artwork plus the approved one- or two-slot purple tray.
4. Evolution ready states preserve evolved artwork, remove the embedded source-header diamond, and use the approved thin purple-white luminous boundary plus a restrained outer glow.
5. The `1/2` tray is visibly divided at the center: left active, right inactive.
6. One-slot and two-slot Evolution trays use the same reviewed outer width within their frame family.
7. Hero preserves the full top edge of `heroMedium` artwork inside the normal card's native frame; the source's gold Hero header and diamond are not retained.
8. Fixed elixir costs use the approved hand-drawn `1`–`9` PNG templates. Mirror uses the approved `?` template and requires runtime replacement.
9. Artwork is never regenerated or repainted; rendering is deterministic resizing and compositing only.
10. Legendary Evolution uses a dedicated six-edge peaked tray family; it never uses the Standard trapezoid tray.
11. Legendary ready-state artwork reaches the luminous six-sided boundary directly: no black inner ring and no silver inner frame.
12. Standard ready-state luminous geometry is fixed across cards, including Firecracker and Skeleton Army.
13. The tray overlaps the card's top luminous edge; no transparent gap is allowed between them.
14. Champion normal cards preserve their native Champion frame without an added Hero header.
15. Runtime icons are 104×139. The reviewed four-column layout uses fixed aligned slot centers and an 88 px stride.
"""
    (spec_root / f"visual_rules.{PRODUCTION_VERSION}.md").write_text(visual_rules, encoding="utf-8")
    asset_spec = f"""# Card icon asset specification — {PRODUCTION_VERSION}

- Each card has one stable ID directory under `cards/`.
- `master/` contains 384×512 RGBA PNG files.
- `runtime/` contains 104×139 RGBA PNG files derived with Lanczos resampling.
- State filenames are `normal.png`, `hero.png`, or `evolution_<charge>_of_<cycles>.png`.
- Runtime lookup uses card ID plus state key, never an English display name.
- `manifest.{PRODUCTION_VERSION}.json` is the authoritative lookup and includes SHA-256 hashes.
- Approved Standard and Legendary Evolution tray families are copied to `templates/evolution/`.
- Approved active/inactive Evolution diamonds are independent `70×64` components shared by every tray family and charge count.
- User-approved ready-state masters are copied to `templates/approved-ready/` and take precedence over inferred geometry.
- Approved hand-drawn cost badges `1`–`9` and `?` are copied to `templates/cost/`.
"""
    (spec_root / f"icon_asset_spec.{PRODUCTION_VERSION}.md").write_text(asset_spec, encoding="utf-8")

    destination = output_root / "templates" / "evolution"
    for family in ("standard", "legendary"):
        for source in sorted((template_root / "evolution" / family).glob("tray_*.png")):
            with Image.open(source) as image:
                _save_png(image.convert("RGBA"), destination / family / source.name)
    for name in ("slot_active.png", "slot_inactive.png"):
        with Image.open(template_root / "evolution" / "slots" / name) as image:
            _save_png(image.convert("RGBA"), destination / "slots" / name)
    approved_destination = output_root / "templates" / "approved-ready"
    approved_source = template_root / "approved-ready"
    if approved_source.is_dir():
        for source in sorted(approved_source.glob("*.png")):
            with Image.open(source) as image:
                _save_png(image.convert("RGBA"), approved_destination / source.name)
    cost_destination = output_root / "templates" / "cost"
    for name in COST_TEMPLATE_NAMES:
        with Image.open(cost_template_root / name) as image:
            _save_png(image.copy(), cost_destination / name)


def _validate_output(
    output_root: Path,
    manifest: dict[str, Any],
    expected_cards: int,
    expected_assets: int,
    expected_counts: dict[str, int],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    entries = manifest["assets"]
    if len(manifest["cards"]) != expected_cards:
        issues.append({"severity": "error", "code": "card_count", "expected": expected_cards, "actual": len(manifest["cards"])})
    if len(entries) != expected_assets:
        issues.append({"severity": "error", "code": "asset_count", "expected": expected_assets, "actual": len(entries)})
    counts = Counter(entry["form"] for entry in entries)
    if dict(counts) != expected_counts:
        issues.append({"severity": "error", "code": "form_counts", "expected": expected_counts, "actual": dict(counts)})

    seen: set[tuple[int, str]] = set()
    entries_by_card: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        entries_by_card.setdefault(int(entry["cardId"]), []).append(entry)
        key = (int(entry["cardId"]), str(entry["state"]))
        if key in seen:
            issues.append({"severity": "error", "code": "duplicate_state", "key": list(key)})
        seen.add(key)
        for kind, expected_size in (("masterPath", CANVAS), ("runtimePath", RUNTIME_SIZE)):
            path = output_root / entry[kind]
            if not path.is_file():
                issues.append({"severity": "error", "code": "missing_file", "path": str(path)})
                continue
            with Image.open(path) as image:
                if image.mode != "RGBA" or image.size != expected_size:
                    issues.append(
                        {"severity": "error", "code": "invalid_png", "path": str(path), "mode": image.mode, "size": list(image.size)}
                    )
                if image.getchannel("A").getextrema()[0] != 0:
                    issues.append({"severity": "error", "code": "missing_transparency", "path": str(path)})
            if _sha256(path) != entry[kind.replace("Path", "Sha256")]:
                issues.append({"severity": "error", "code": "hash_mismatch", "path": str(path)})

        master_path = output_root / entry["masterPath"]
        runtime_path = output_root / entry["runtimePath"]
        if master_path.is_file() and runtime_path.is_file():
            with Image.open(master_path) as master_image, Image.open(runtime_path) as runtime_image:
                expected_runtime = master_image.convert("RGBA").resize(RUNTIME_SIZE, Image.Resampling.LANCZOS)
                if ImageChops.difference(expected_runtime, runtime_image.convert("RGBA")).getbbox() is not None:
                    issues.append({"severity": "error", "code": "runtime_derivation", "path": str(runtime_path)})

    for card_id, card_entries in entries_by_card.items():
        by_state = {str(entry["state"]): entry for entry in card_entries}
        normal_entry = by_state.get("normal")
        if normal_entry is None:
            issues.append({"severity": "error", "code": "missing_normal_state", "cardId": card_id})
            continue
        with Image.open(output_root / normal_entry["masterPath"]) as normal_image:
            normal = normal_image.convert("RGBA")
        for entry in card_entries:
            if entry["form"] == "normal":
                continue
            with Image.open(output_root / entry["masterPath"]) as state_image:
                state = state_image.convert("RGBA")
            interior_difference = ImageChops.difference(
                normal.convert("RGB").crop((60, 140, 324, 390)),
                state.convert("RGB").crop((60, 140, 324, 390)),
            )
            if entry["form"] == "hero":
                added_header_alpha = ImageChops.subtract(state.getchannel("A"), normal.getchannel("A")).crop((0, 0, 384, 75))
                if added_header_alpha.getbbox() is not None:
                    issues.append({"severity": "error", "code": "hero_header_retained", "cardId": card_id})
                if interior_difference.getbbox() is None:
                    issues.append({"severity": "error", "code": "hero_artwork_unchanged", "cardId": card_id})
            elif entry["form"] == "evolution":
                ready = entry["charge"] == entry["cycles"]
                if ready and interior_difference.getbbox() is None:
                    issues.append({"severity": "error", "code": "evolution_artwork_unchanged", "cardId": card_id})
                if not ready and interior_difference.getbbox() is not None:
                    issues.append(
                        {"severity": "error", "code": "charging_artwork_changed", "cardId": card_id, "state": entry["state"]}
                    )

    for path in sorted((output_root / "templates" / "evolution").glob("*/*.png")):
        with Image.open(path) as image:
            if image.mode != "RGBA" or image.getchannel("A").getextrema() != (0, 255):
                issues.append({"severity": "error", "code": "invalid_tray_template", "path": str(path)})
    for family in ("standard", "legendary"):
        for name in EVOLUTION_TEMPLATE_NAMES:
            path = output_root / "templates" / "evolution" / family / name
            if not path.is_file():
                issues.append({"severity": "error", "code": "missing_tray_template", "path": str(path)})
    for name in ("slot_active.png", "slot_inactive.png"):
        path = output_root / "templates" / "evolution" / "slots" / name
        if not path.is_file():
            issues.append({"severity": "error", "code": "missing_slot_template", "path": str(path)})
            continue
        with Image.open(path) as image:
            if image.mode != "RGBA" or image.size != EVOLUTION_SLOT_SIZE or image.getchannel("A").getextrema() != (0, 255):
                issues.append({"severity": "error", "code": "invalid_slot_template", "path": str(path)})
    for name in COST_TEMPLATE_NAMES:
        path = output_root / "templates" / "cost" / name
        if not path.is_file():
            issues.append({"severity": "error", "code": "missing_cost_template", "path": str(path)})
            continue
        with Image.open(path) as image:
            if image.mode != "RGBA" or image.size != COST_BADGE_SIZE or image.getchannel("A").getextrema() != (0, 255):
                issues.append({"severity": "error", "code": "invalid_cost_template", "path": str(path)})
    return issues


def generate_library(standardized_root: Path, output_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate a complete production-v2 library into a new, empty directory."""
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"Output directory must be empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    catalog_path = standardized_root / "catalog" / "card_icon_manifest.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    template_root = standardized_root / "templates" / "production-v2"
    cost_template_root = standardized_root / "templates" / "production-v2" / "cost"
    renderer = _Renderer(standardized_root, template_root, cost_template_root)

    assets: list[dict[str, Any]] = []
    card_summaries: list[dict[str, Any]] = []
    for card in sorted(catalog["cards"], key=lambda item: int(item["canonicalCardId"])):
        card_id = int(card["canonicalCardId"])
        states = _expanded_states(card)
        state_summaries: list[dict[str, Any]] = []
        for state in states:
            master = renderer.render(card, state)
            runtime = master.resize(RUNTIME_SIZE, Image.Resampling.LANCZOS)
            master_rel = Path("cards") / str(card_id) / "master" / state["filename"]
            runtime_rel = Path("cards") / str(card_id) / "runtime" / state["filename"]
            _save_png(master, output_root / master_rel)
            _save_png(runtime, output_root / runtime_rel)
            entry = {
                "cardId": card_id,
                "name": card["name"],
                "frameClass": card["frameClass"],
                "form": state["form"],
                "state": state["key"],
                "charge": state.get("charge"),
                "cycles": state.get("cycles"),
                "elixirCost": card["elixirCost"],
                "dynamicElixirCost": card["dynamicElixirCost"],
                "masterPath": master_rel.as_posix(),
                "runtimePath": runtime_rel.as_posix(),
                "masterSha256": _sha256(output_root / master_rel),
                "runtimeSha256": _sha256(output_root / runtime_rel),
            }
            assets.append(entry)
            state_summaries.append({"state": state["key"], "masterPath": entry["masterPath"], "runtimePath": entry["runtimePath"]})
        card_metadata = {
            "cardId": card_id,
            "aliases": card["cardIds"],
            "name": card["name"],
            "rarity": card["rarity"],
            "frameClass": card["frameClass"],
            "elixirCost": card["elixirCost"],
            "dynamicElixirCost": card["dynamicElixirCost"],
            "states": state_summaries,
        }
        metadata_path = output_root / "cards" / str(card_id) / "card.json"
        metadata_path.write_text(json.dumps(card_metadata, indent=2) + "\n", encoding="utf-8")
        card_summaries.append({**card_metadata, "metadataPath": metadata_path.relative_to(output_root).as_posix()})

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "status": PRODUCTION_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceCatalog": str(catalog_path.resolve()),
        "masterSize": list(CANVAS),
        "runtimeSize": list(RUNTIME_SIZE),
        "summary": {
            "cards": len(card_summaries),
            "assets": len(assets),
            "forms": dict(Counter(entry["form"] for entry in assets)),
        },
        "cards": card_summaries,
        "assets": assets,
    }
    manifest_path = output_root / f"manifest.{PRODUCTION_VERSION}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    _write_specs(output_root, template_root, cost_template_root)
    preview_root = output_root / "previews"
    _contact_sheet([entry for entry in assets if entry["form"] == "normal"], output_root, preview_root / "normal_contact_sheet.png", f"Normal states - {PRODUCTION_VERSION}")
    _contact_sheet([entry for entry in assets if entry["form"] == "evolution"], output_root, preview_root / "evolution_contact_sheet.png", f"Evolution states - {PRODUCTION_VERSION}")
    _contact_sheet([entry for entry in assets if entry["form"] == "hero"], output_root, preview_root / "hero_contact_sheet.png", f"Hero states - {PRODUCTION_VERSION}", columns=8)

    expected_cards = int(catalog["summary"]["uniqueCards"])
    expected_assets = int(catalog["summary"]["expectedOutputs"])
    expected_counts = {str(key): int(value) for key, value in catalog["summary"]["expectedOutputsByForm"].items()}
    issues = _validate_output(output_root, manifest, expected_cards, expected_assets, expected_counts)
    report = {
        "status": "pass" if not any(issue["severity"] == "error" for issue in issues) else "fail",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": manifest["summary"],
        "checks": {
            "expectedCards": expected_cards,
            "expectedAssets": expected_assets,
            "expectedForms": expected_counts,
            "masterFormat": {"size": list(CANVAS), "mode": "RGBA"},
            "runtimeFormat": {"size": list(RUNTIME_SIZE), "mode": "RGBA"},
        },
        "issues": issues,
    }
    (output_root / f"audit.{PRODUCTION_VERSION}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report_md = [
        f"# Standardized card icon {PRODUCTION_VERSION} audit",
        "",
        f"- Status: **{report['status'].upper()}**",
        f"- Card directories: {manifest['summary']['cards']}",
        f"- Rendered states: {manifest['summary']['assets']}",
        f"- Forms: {manifest['summary']['forms']}",
        f"- Master: {CANVAS[0]}×{CANVAS[1]} RGBA PNG",
        f"- Runtime: {RUNTIME_SIZE[0]}×{RUNTIME_SIZE[1]} RGBA PNG",
        f"- Issues: {len(issues)}",
        "",
        "Mirror uses a `?` cost badge and must receive its actual cost at runtime.",
    ]
    (output_root / f"audit.{PRODUCTION_VERSION}.md").write_text("\n".join(report_md) + "\n", encoding="utf-8")
    return manifest, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standardized-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest, report = generate_library(args.standardized_root, args.output)
    print(json.dumps({"summary": manifest["summary"], "status": report["status"], "output": str(args.output)}, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
