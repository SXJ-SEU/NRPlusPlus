from __future__ import annotations


# Current Null's Royale client values from csv_logic/spells_evolved.csv,
# DarkElixirCost. Keep this table synchronized when the game data changes.
EVOLUTION_CYCLES: dict[int, int] = {
    26_000_000: 2,  # Knight
    26_000_001: 2,  # Archers
    26_000_004: 1,  # P.E.K.K.A
    26_000_007: 1,  # Witch
    26_000_008: 1,  # Barbarians
    26_000_010: 2,  # Skeletons
    26_000_011: 2,  # Valkyrie
    26_000_012: 2,  # Skeleton Army
    26_000_013: 2,  # Bomber
    26_000_014: 2,  # Musketeer
    26_000_015: 2,  # Baby Dragon
    26_000_017: 1,  # Wizard
    26_000_022: 1,  # Minion Horde
    26_000_024: 1,  # Royal Giant
    26_000_026: 2,  # Princess
    26_000_030: 2,  # Ice Spirit
    26_000_035: 2,  # Lumberjack
    26_000_036: 2,  # Battle Ram
    26_000_037: 2,  # Inferno Dragon
    26_000_040: 2,  # Dart Goblin
    26_000_043: 1,  # Elite Barbarians
    26_000_044: 2,  # Hunter
    26_000_045: 1,  # Executioner
    26_000_047: 1,  # Royal Recruits
    26_000_049: 2,  # Bats
    26_000_050: 2,  # Royal Ghost
    26_000_055: 1,  # Mega Knight
    26_000_056: 2,  # Skeleton Barrel
    26_000_058: 2,  # Wall Breakers
    26_000_059: 2,  # Royal Hogs
    26_000_060: 1,  # Goblin Giant
    26_000_063: 1,  # Electro Dragon
    26_000_064: 2,  # Firecracker
    27_000_000: 2,  # Cannon
    27_000_002: 2,  # Mortar
    27_000_006: 2,  # Tesla
    27_000_010: 2,  # Furnace
    27_000_012: 2,  # Goblin Cage
    27_000_013: 2,  # Goblin Drill
    28_000_004: 2,  # Goblin Barrel
    28_000_008: 2,  # Zap
    28_000_017: 2,  # Giant Snowball
}
