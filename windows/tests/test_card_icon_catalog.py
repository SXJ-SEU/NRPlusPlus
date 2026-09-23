from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "build_card_icon_catalog.py"
SPEC = importlib.util.spec_from_file_location("build_card_icon_catalog", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
catalog_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog_module)


class CardIconCatalogTests(unittest.TestCase):
    def _build(self, items: list[dict]) -> tuple[dict, list[dict]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cards_path = root / "cards.json"
            cards_path.write_text(json.dumps({"items": items}), encoding="utf-8")
            return catalog_module.build_catalog(cards_path, root / "output")

    def test_builds_normal_evolution_and_hero_states(self) -> None:
        catalog, issues = self._build(
            [
                {
                    "id": 1,
                    "name": "Example",
                    "rarity": "legendary",
                    "elixirCost": 4,
                    "iconUrls": {
                        "medium": "https://example.test/normal.png",
                        "evolutionMedium": "https://example.test/evolution.png",
                        "heroMedium": "https://example.test/hero.png",
                    },
                    "forms": {"evolution": {"cycles": 2}},
                }
            ]
        )

        self.assertEqual([], issues)
        self.assertEqual(5, catalog["summary"]["expectedOutputs"])
        self.assertEqual("legendary", catalog["cards"][0]["frameClass"])
        self.assertEqual(
            {"normal": 1, "evolution": 3, "hero": 1},
            catalog["summary"]["expectedOutputsByForm"],
        )

    def test_aliases_identical_display_records(self) -> None:
        items = [
            {
                "id": 28000025,
                "name": "Spirit Empress",
                "rarity": "legendary",
                "elixirCost": 6,
                "iconUrls": {"medium": "https://example.test/spirit.png"},
            },
            {
                "id": 26000105,
                "name": "Spirit Empress",
                "rarity": "legendary",
                "elixirCost": 6,
                "iconUrls": {"medium": "https://example.test/spirit.png"},
            },
        ]
        catalog, issues = self._build(items)

        self.assertEqual([], issues)
        self.assertEqual(1, catalog["summary"]["uniqueCards"])
        self.assertEqual(28000025, catalog["idAliases"]["26000105"])
        self.assertEqual([28000025, 26000105], catalog["cards"][0]["cardIds"])

    def test_missing_cost_is_dynamic_and_champion_frame_is_distinct(self) -> None:
        catalog, issues = self._build(
            [
                {
                    "id": 6,
                    "name": "Mirror",
                    "rarity": "champion",
                    "iconUrls": {"medium": "https://example.test/mirror.png"},
                }
            ]
        )

        self.assertEqual([], issues)
        card = catalog["cards"][0]
        self.assertTrue(card["dynamicElixirCost"])
        self.assertEqual("champion", card["frameClass"])

    def test_evolution_source_requires_cycle_metadata(self) -> None:
        _, issues = self._build(
            [
                {
                    "id": 1,
                    "name": "Broken",
                    "rarity": "common",
                    "elixirCost": 3,
                    "iconUrls": {
                        "medium": "https://example.test/normal.png",
                        "evolutionMedium": "https://example.test/evolution.png",
                    },
                }
            ]
        )

        self.assertEqual("invalid_evolution_cycles", issues[0]["code"])

    def test_runtime_hero_metadata_does_not_expand_available_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cards_path = root / "cards.json"
            cards_path.write_text(
                json.dumps(
                    {
                        "formMetadata": {"overlapSummary": {"heroCards": 113}},
                        "items": [
                            {
                                "id": 1,
                                "name": "Example",
                                "rarity": "common",
                                "elixirCost": 3,
                                "iconUrls": {"medium": "https://example.test/normal.png"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            catalog, issues = catalog_module.build_catalog(cards_path, root / "output")

        self.assertEqual(0, catalog["summary"]["heroSources"])
        self.assertEqual("hero_metadata_source_scope_mismatch", issues[0]["code"])

    def test_repository_catalog_has_expected_phase_one_scope(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        catalog, issues = catalog_module.build_catalog(
            repository_root / "deploy" / "cards.json",
            repository_root / "unused-test-output",
        )

        self.assertEqual(
            {
                "sourceRecords": 123,
                "uniqueCards": 122,
                "cardIds": 123,
                "normalSources": 122,
                "evolutionSources": 42,
                "heroSources": 16,
                "expectedOutputs": 251,
                "expectedOutputsByForm": {
                    "evolution": 113,
                    "hero": 16,
                    "normal": 122,
                },
                "rarities": {
                    "champion": 8,
                    "common": 29,
                    "epic": 33,
                    "legendary": 22,
                    "rare": 30,
                },
                "frameClasses": {
                    "champion": 8,
                    "legendary": 22,
                    "standard": 92,
                },
            },
            catalog["summary"],
        )
        self.assertEqual(
            ["hero_metadata_source_scope_mismatch"],
            [issue["code"] for issue in issues],
        )


if __name__ == "__main__":
    unittest.main()
