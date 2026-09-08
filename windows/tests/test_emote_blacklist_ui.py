from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT / "deploy"))

from emote_blacklist_ui import (  # noqa: E402
    EmoteEntry,
    filter_entries,
    load_blacklist,
    load_catalog,
    save_blacklist,
)


class EmoteBlacklistModelTests(unittest.TestCase):
    def test_catalog_contains_animated_and_localized_text_entries(self) -> None:
        entries = load_catalog()

        self.assertGreater(len(entries), 600)
        self.assertTrue(
            any(entry.identifier == "1-0" and entry.kind == "animated" for entry in entries)
        )
        good_luck = next(entry for entry in entries if entry.identifier == "Taunt1")
        self.assertIn("祝你好运", good_luck.name)
        self.assertIn("Good luck", good_luck.name)

    def test_search_matches_name_id_family_and_multiple_words(self) -> None:
        entries = [
            EmoteEntry("1-0", "animated", "King Thumbs Up", "King"),
            EmoteEntry("1-99", "animated", "Minion Giant Burp", "Minion Giant"),
        ]

        self.assertEqual(filter_entries(entries, "burp"), [entries[1]])
        self.assertEqual(filter_entries(entries, "1-0"), [entries[0]])
        self.assertEqual(filter_entries(entries, "minion giant"), [entries[1]])
        self.assertEqual(filter_entries(entries, "missing"), [])

    def test_blacklist_round_trip_uses_configuration_only_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blacklist.json"
            save_blacklist({"1-2", "1-0"}, {"Taunt4"}, path)

            animated, text = load_blacklist(path)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(animated, {"1-0", "1-2"})
        self.assertEqual(text, {"Taunt4"})
        self.assertEqual(payload["mode"], "configuration_only")
        self.assertEqual(payload["animated_emote_ids"], ["1-0", "1-2"])


if __name__ == "__main__":
    unittest.main()
