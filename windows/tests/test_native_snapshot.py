from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT / "tools"))

from capture_native_entity_stream import (  # noqa: E402
    BattleStateCoordinator,
    DEFAULT_STREAM_INTERVAL_MS,
    normalize_snapshot,
)


class NativeSnapshotTests(unittest.TestCase):
    def test_default_stream_interval_is_low_latency(self) -> None:
        self.assertLessEqual(DEFAULT_STREAM_INTERVAL_MS, 20)

    def test_maps_side_elixir_to_local_and_opponent(self) -> None:
        snapshot = normalize_snapshot(
            {
                "battle_active": True,
                "local_side": 1,
                "side_elixir": [7, 4],
                "own_elixir": 4,
            }
        )

        self.assertEqual(snapshot["own_elixir"], 4)
        self.assertEqual(snapshot["opponent_elixir"], 7)
        self.assertEqual(snapshot["side_elixir"], [7, 4])

    def test_uses_player_resource_when_ui_elixir_is_unavailable(self) -> None:
        snapshot = normalize_snapshot(
            {
                "battle_active": True,
                "local_side": 0,
                "side_elixir": [6, 2],
                "own_elixir": -1,
            }
        )

        self.assertEqual(snapshot["own_elixir"], 6)
        self.assertEqual(snapshot["opponent_elixir"], 2)

    def test_rejects_invalid_resource_values(self) -> None:
        snapshot = normalize_snapshot(
            {"side_elixir": [-1, 11], "own_elixir": -1}
        )

        self.assertIsNone(snapshot["own_elixir"])
        self.assertIsNone(snapshot["opponent_elixir"])

    def test_battle_end_discards_previous_state(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter([{"own_elixir": 4, "battle_clock": 31.0}]).__next__
        )

        coordinator.set_active(True)
        coordinator.poll()
        first = coordinator.merge(
            normalize_snapshot(
                {"battle_active": True, "side_elixir": [5, 4]}
            )
        )
        self.assertEqual(first["own_elixir"], 4)

        coordinator.set_active(False)
        between = coordinator.merge(
            normalize_snapshot(
                {"battle_active": False, "side_elixir": [-1, -1]}
            )
        )
        self.assertIsNone(between["own_elixir"])
        self.assertIsNone(between["opponent_elixir"])
        self.assertIsNone(between["battle_clock"])

    def test_second_battle_uses_a_new_reader(self) -> None:
        readers = iter(
            [
                iter([{"own_elixir": 4}, {"own_elixir": 0}]).__next__,
                iter([{"own_elixir": 6, "battle_clock": 5.0}]).__next__,
            ]
        )
        coordinator = BattleStateCoordinator(lambda: next(readers))
        coordinator.set_active(True)
        coordinator.poll()
        coordinator.set_active(False)
        coordinator.set_active(True)
        coordinator.poll()
        second = coordinator.merge(
            normalize_snapshot(
                {"battle_active": True, "side_elixir": [3, 6]}
            )
        )
        self.assertEqual(second["own_elixir"], 6)
        self.assertEqual(second["opponent_elixir"], 3)
        self.assertEqual(second["battle_clock"], 5.0)


if __name__ == "__main__":
    unittest.main()
