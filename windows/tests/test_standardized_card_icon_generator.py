from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPOSITORY_ROOT / "windows" / "tools" / "generate_standardized_card_icons.py"
SPEC = importlib.util.spec_from_file_location("standardized_card_icons", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StandardizedCardIconGeneratorTests(unittest.TestCase):
    def _source(self, path: Path, frame: str, artwork: tuple[int, int, int], header: str | None = None) -> None:
        image = Image.new("RGBA", (285, 420))
        draw = ImageDraw.Draw(image)
        if header:
            color = (235, 170, 25, 255) if header == "hero" else (110, 35, 190, 255)
            draw.polygon([(65, 5), (220, 5), (245, 85), (40, 85)], fill=color)
        if frame == "legendary":
            polygon = [(52, 55), (233, 55), (270, 95), (252, 355), (215, 402), (70, 402), (33, 355), (15, 95)]
            draw.polygon(polygon, fill=(40, 48, 60, 255))
            inner = [(62, 70), (223, 70), (250, 100), (235, 345), (205, 385), (80, 385), (50, 345), (35, 100)]
            draw.polygon(inner, fill=artwork + (255,))
        else:
            draw.rounded_rectangle((30, 55, 255, 400), radius=20, fill=(35, 38, 42, 255))
            draw.rounded_rectangle((42, 68, 243, 386), radius=14, fill=artwork + (255,))
            if header == "hero":
                # A scale-sensitive band: direct aligned compositing keeps this
                # cyan at master y=220; crop-and-stretch moves the lower band up.
                draw.rectangle((42, 68, 243, 190), fill=(10, 190, 235, 255))
                draw.rectangle((42, 191, 243, 386), fill=artwork + (255,))
        if header:
            # Bright synthetic source-header diamond. Production output must
            # remove it rather than map its lower half into the card artwork.
            cx, cy, radius = 142, 88, 42
            draw.polygon([(cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy)], fill=(255, 0, 255, 255))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)

    def _tray(self, path: Path, cycles: int, charge: int, color: tuple[int, int, int] | None = None) -> None:
        image = Image.new("RGBA", (250 if cycles == 2 else 200, 100))
        draw = ImageDraw.Draw(image)
        fill = color or (90 + charge * 30, 25, 160)
        draw.polygon([(20, 5), (image.width - 20, 5), (image.width - 2, 98), (2, 98)], fill=fill + (255,))
        centers = [image.width // 2] if cycles == 1 else [image.width * 3 // 8, image.width * 5 // 8]
        for center in centers:
            draw.polygon([(center, 30), (center + 20, 50), (center, 70), (center - 20, 50)], fill=(255, 255, 255, 255))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)

    def _cost_badge(self, path: Path, color: tuple[int, int, int]) -> None:
        image = Image.new("RGBA", (112, 112))
        ImageDraw.Draw(image).ellipse((2, 2, 109, 109), fill=color + (255,))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)

    def _slot(self, path: Path, color: tuple[int, int, int]) -> None:
        image = Image.new("RGBA", (70, 64))
        ImageDraw.Draw(image).polygon([(35, 1), (68, 32), (35, 62), (2, 32)], fill=color + (255,))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)

    def _fixture(self, root: Path) -> None:
        cards = [
            {
                "canonicalCardId": 1,
                "cardIds": [1],
                "name": "Standard",
                "rarity": "common",
                "frameClass": "standard",
                "elixirCost": 3,
                "dynamicElixirCost": False,
                "sources": {
                    "normal": {"path": "source/normal/1.png"},
                    "evolution": {"path": "source/evolution/1.png"},
                    "hero": {"path": "source/hero/1.png"},
                },
                "states": [
                    {"form": "normal", "key": "normal"},
                    {
                        "form": "evolution",
                        "cycles": 2,
                        "states": [
                            {"charge": 0, "key": "evolution_0_of_2"},
                            {"charge": 1, "key": "evolution_1_of_2"},
                            {"charge": 2, "key": "evolution_2_of_2"},
                        ],
                    },
                    {"form": "hero", "key": "hero"},
                ],
            },
            {
                "canonicalCardId": 2,
                "cardIds": [2],
                "name": "Legendary",
                "rarity": "legendary",
                "frameClass": "legendary",
                "elixirCost": 4,
                "dynamicElixirCost": False,
                "sources": {
                    "normal": {"path": "source/normal/2.png"},
                    "evolution": {"path": "source/evolution/2.png"},
                },
                "states": [
                    {"form": "normal", "key": "normal"},
                    {
                        "form": "evolution",
                        "cycles": 1,
                        "states": [
                            {"charge": 0, "key": "evolution_0_of_1"},
                            {"charge": 1, "key": "evolution_1_of_1"},
                        ],
                    },
                ],
            },
            {
                "canonicalCardId": 3,
                "cardIds": [3, 30],
                "name": "Dynamic",
                "rarity": "epic",
                "frameClass": "standard",
                "elixirCost": None,
                "dynamicElixirCost": True,
                "sources": {"normal": {"path": "source/normal/3.png"}},
                "states": [{"form": "normal", "key": "normal"}],
            },
        ]
        catalog = {
            "summary": {
                "uniqueCards": 3,
                "expectedOutputs": 9,
                "expectedOutputsByForm": {"normal": 3, "evolution": 5, "hero": 1},
            },
            "cards": cards,
        }
        catalog_path = root / "catalog" / "card_icon_manifest.json"
        catalog_path.parent.mkdir(parents=True)
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        self._source(root / "source" / "normal" / "1.png", "standard", (30, 90, 160))
        self._source(root / "source" / "evolution" / "1.png", "standard", (90, 220, 80), "evolution")
        self._source(root / "source" / "hero" / "1.png", "standard", (240, 180, 20), "hero")
        self._source(root / "source" / "normal" / "2.png", "legendary", (20, 120, 90))
        self._source(root / "source" / "evolution" / "2.png", "legendary", (210, 60, 200), "evolution")
        self._source(root / "source" / "normal" / "3.png", "standard", (80, 40, 130))
        for cycles in (1, 2):
            for charge in range(cycles + 1):
                self._tray(root / "templates" / "production-v2" / "evolution" / "standard" / f"tray_{charge}_of_{cycles}.png", cycles, charge)
                self._tray(
                    root / "templates" / "production-v2" / "evolution" / "legendary" / f"tray_{charge}_of_{cycles}.png",
                    cycles,
                    charge,
                    color=(10, 210, 240),
                )
        self._slot(root / "templates" / "production-v2" / "evolution" / "slots" / "slot_active.png", (255, 220, 20))
        self._slot(root / "templates" / "production-v2" / "evolution" / "slots" / "slot_inactive.png", (70, 55, 110))
        cost_root = root / "templates" / "production-v2" / "cost"
        for value in range(1, 10):
            self._cost_badge(cost_root / f"cost_{value}.png", (value * 20, 10, 200))
        self._cost_badge(cost_root / "cost_unknown.png", (250, 180, 20))

    def test_generates_complete_library_through_one_interface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "standardized"
            output = Path(directory) / "production"
            self._fixture(root)
            manifest, report = MODULE.generate_library(root, output)

            self.assertEqual("pass", report["status"])
            self.assertEqual({"cards": 3, "assets": 9, "forms": {"normal": 3, "evolution": 5, "hero": 1}}, manifest["summary"])
            self.assertEqual(9, len(list(output.glob("cards/*/master/*.png"))))
            self.assertEqual(9, len(list(output.glob("cards/*/runtime/*.png"))))
            with Image.open(output / "cards" / "1" / "runtime" / "hero.png") as image:
                self.assertEqual((104, 139), image.size)
                self.assertEqual("RGBA", image.mode)
            self.assertTrue((output / "cards" / "3" / "card.json").is_file())
            self.assertTrue((output / "previews" / "evolution_contact_sheet.png").is_file())

    def test_refuses_nonempty_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "standardized"
            output = Path(directory) / "production"
            self._fixture(root)
            output.mkdir(parents=True)
            (output / "owned.txt").write_text("do not overwrite", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                MODULE.generate_library(root, output)

    def test_removes_embedded_header_preserves_hero_scale_and_uses_legendary_tray(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "standardized"
            output = Path(directory) / "production"
            self._fixture(root)
            MODULE.generate_library(root, output)

            def marker_pixels(path: Path) -> int:
                with Image.open(path) as image:
                    pixels = image.convert("RGB").crop((120, 75, 264, 185)).getdata()
                return sum(1 for red, green, blue in pixels if red > 235 and green < 35 and blue > 235)

            def hero_base_pixels(path: Path) -> int:
                with Image.open(path) as image:
                    pixels = image.convert("RGB").crop((70, 70, 314, 150)).getdata()
                return sum(1 for red, green, blue in pixels if red > 190 and 110 < green < 205 and blue < 70)

            self.assertLess(marker_pixels(output / "cards" / "1" / "master" / "hero.png"), 8)
            self.assertLess(hero_base_pixels(output / "cards" / "1" / "master" / "hero.png"), 8)
            self.assertLess(marker_pixels(output / "cards" / "1" / "master" / "evolution_2_of_2.png"), 8)

            with Image.open(output / "cards" / "1" / "master" / "hero.png") as hero:
                red, green, blue, alpha = hero.convert("RGBA").getpixel((100, 220))
            self.assertGreater(blue, 180, "Hero artwork was vertically cropped and stretched.")
            self.assertGreater(green, 140, "Hero artwork lost its top safe area.")
            self.assertEqual(255, alpha)

            with Image.open(output / "cards" / "2" / "master" / "evolution_0_of_1.png") as legendary:
                red, green, blue, alpha = legendary.convert("RGBA").getpixel((192, 30))
            self.assertGreater(green, 170, "Legendary card did not use its dedicated tray family.")
            self.assertGreater(blue, 190, "Legendary card did not use its dedicated tray family.")
            self.assertEqual(255, alpha)

    def test_uses_formal_cost_templates_and_unknown_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "standardized"
            output = Path(directory) / "production"
            self._fixture(root)
            MODULE.generate_library(root, output)

            with Image.open(output / "cards" / "1" / "master" / "normal.png") as fixed:
                self.assertEqual((60, 10, 200, 255), fixed.convert("RGBA").getpixel(MODULE.COST_CENTER))
            with Image.open(output / "cards" / "3" / "master" / "normal.png") as dynamic:
                self.assertEqual((250, 180, 20, 255), dynamic.convert("RGBA").getpixel(MODULE.COST_CENTER))

            copied = output / "templates" / "cost" / "cost_unknown.png"
            self.assertTrue(copied.is_file())
            with Image.open(copied) as template:
                self.assertEqual((112, 112), template.size)
                self.assertEqual("RGBA", template.mode)

    def test_one_slot_and_two_slot_trays_have_the_same_outer_width(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "standardized"
            self._fixture(root)
            template_root = root / "templates" / "production-v2"
            for frame_class in ("standard", "legendary"):
                one = MODULE._tray_layer(template_root, frame_class, 1, 0).getchannel("A").getbbox()
                two = MODULE._tray_layer(template_root, frame_class, 2, 0).getchannel("A").getbbox()
                self.assertIsNotNone(one)
                self.assertIsNotNone(two)
                self.assertEqual(one[2] - one[0], two[2] - two[0])

    def test_tray_resize_does_not_flatten_single_slot_diamond(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "standardized"
            self._fixture(root)
            template_root = root / "templates" / "production-v2"
            bboxes = []
            for frame_class in ("standard", "legendary"):
                layer = MODULE._tray_layer(template_root, frame_class, 1, 1).convert("RGB")
                marker = Image.new("L", (84, 150))
                pixels = marker.load()
                for y in range(150):
                    for x in range(84):
                        red, green, blue = layer.getpixel((x + 150, y))
                        if red > 240 and green > 180 and blue < 80:
                            pixels[x, y] = 255
                bbox = marker.getbbox()
                self.assertIsNotNone(bbox)
                bboxes.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))
            self.assertEqual(bboxes[0], bboxes[1])

    def test_ready_border_geometry_is_fixed_for_each_frame_family(self) -> None:
        for frame_class in ("standard", "legendary"):
            glow_a, outline_a = MODULE._ready_border_layers(frame_class)
            glow_b, outline_b = MODULE._ready_border_layers(frame_class)
            self.assertEqual(glow_a.getchannel("A").getbbox(), glow_b.getchannel("A").getbbox())
            self.assertEqual(outline_a.getchannel("A").getbbox(), outline_b.getchannel("A").getbbox())
            self.assertIsNotNone(outline_a.getchannel("A").getbbox())

    def test_legendary_ready_state_removes_native_inner_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "standardized"
            self._fixture(root)
            renderer = MODULE._Renderer(root, root / "templates" / "production-v2")
            card = json.loads((root / "catalog" / "card_icon_manifest.json").read_text(encoding="utf-8"))["cards"][1]
            state = next(item for item in MODULE._expanded_states(card) if item["key"] == "evolution_1_of_1")
            ready = renderer.render(card, state).convert("RGBA")
            # This point lies in the synthetic native frame but inside the
            # approved ready silhouette. It must now contain artwork or glow,
            # not the original dark frame color (40, 48, 60).
            self.assertNotEqual((40, 48, 60), ready.getpixel((82, 220))[:3])
            self.assertEqual(6, len(MODULE.LEGENDARY_READY_POLYGON))

    def test_hero_artwork_reaches_the_normal_top_interior(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "standardized"
            self._fixture(root)
            renderer = MODULE._Renderer(root, root / "templates" / "production-v2")
            card = json.loads((root / "catalog" / "card_icon_manifest.json").read_text(encoding="utf-8"))["cards"][0]
            state = next(item for item in MODULE._expanded_states(card) if item["key"] == "hero")
            hero = renderer.render(card, state).convert("RGBA")
            normal = renderer.render(card, {"form": "normal"}).convert("RGBA")
            self.assertEqual(normal.getpixel((192, 114)), hero.getpixel((192, 114)))


if __name__ == "__main__":
    unittest.main()
