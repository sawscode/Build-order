"""
Shared constants for the villager pathing module.

Terrain IDs, object IDs, speed values, and tech IDs sourced from
AoE2 DE dataset documentation and the AoE2 community modding wiki.
"""

# ---------------------------------------------------------------------------
# Terrain passability
# ---------------------------------------------------------------------------

# Terrain IDs that are impassable for land units (water / deep water).
# AoE2 DE uses these IDs in its built-in dataset; exact enumeration varies
# slightly between game builds, so this list is deliberately conservative.
# Source: AoE2 terrain atlas (community wiki) + mgz dataset inspection.
WATER_TERRAIN_IDS: frozenset[int] = frozenset({
    1,   # Water (medium depth)
    2,   # Water (shallow) – sometimes also used for beach in older builds
    22,  # Ocean / Deep Water
    23,  # Mediterranean Sea
    26,  # Atlantic Ocean (used on Water maps)
    34,  # Shallow coast water
})

# AoE2 DE uses elevation-based cliff rendering rather than dedicated cliff
# terrain IDs; as a result cliffs are not blocked at this layer. This is a
# known approximation documented in the README.

# ---------------------------------------------------------------------------
# Object / unit IDs
# ---------------------------------------------------------------------------

# Town Center variants (object_id values)
TC_OBJECT_IDS: frozenset[int] = frozenset({71, 109, 141, 142})

# Resource object IDs (GAIA)
GOLD_MINE_OBJECT_IDS: frozenset[int] = frozenset({66})
STONE_MINE_OBJECT_IDS: frozenset[int] = frozenset({102})
DEER_OBJECT_IDS: frozenset[int] = frozenset({65, 1060})
SHEEP_OBJECT_IDS: frozenset[int] = frozenset({594, 1060})
BOAR_OBJECT_IDS: frozenset[int] = frozenset({48, 822})

# Keywords used to identify tree objects from their dataset name string.
# Mirrors the logic in the existing app.py infer_gather_resource helper.
TREE_NAME_KEYWORDS: frozenset[str] = frozenset({
    "tree", "pine", "oak", "jungle", "bamboo", "palm", "stump",
    "birch", "spruce", "acacia", "mangrove", "snow pine", "dead tree",
})

# Resource type keywords used to match GAIA object names to resource types.
RESOURCE_NAME_KEYWORDS: dict[str, frozenset[str]] = {
    "wood":  frozenset({"tree", "pine", "oak", "jungle", "bamboo", "palm",
                         "stump", "birch", "spruce", "acacia", "mangrove",
                         "snow pine", "dead tree"}),
    "gold":  frozenset({"gold mine", "gold"}),
    "stone": frozenset({"stone mine", "stone"}),
    "food":  frozenset({"sheep", "deer", "boar", "forage", "berry", "bush",
                         "turkey", "pig", "cow"}),
}

# ---------------------------------------------------------------------------
# Building footprints (tile side-length, buildings are square)
# ---------------------------------------------------------------------------

# Used to mark building tiles as impassable.
# AoE2 building sizes sourced from the in-game editor and wiki.
BUILDING_FOOTPRINTS: dict[int, int] = {
    71:  4,   # Town Center (standard)
    109: 4,   # Town Center (variant)
    141: 4,   # Town Center (variant)
    142: 4,   # Town Center (variant)
    70:  2,   # House
    50:  3,   # Farm
    12:  3,   # Barracks
    10:  3,   # Archery Range
    86:  3,   # Stable
    562: 2,   # Lumber Camp
    584: 2,   # Mining Camp
    68:  2,   # Mill
    84:  4,   # Market
    82:  4,   # Castle
    30:  3,   # Monastery
    45:  3,   # Dock
    18:  3,   # Blacksmith
    598: 1,   # Outpost
}

# ---------------------------------------------------------------------------
# Villager movement speed
# ---------------------------------------------------------------------------

# Villager base move speed in tiles/second.
# Source: AoE2 wiki – Villager unit stats.
VILLAGER_BASE_SPEED: float = 0.96

# Technology IDs that affect villager speed.
# Source: AoE2 tech tree IDs (standard DE dataset).
WHEELBARROW_TECH_ID: int = 65
HAND_CART_TECH_ID: int = 249

# Speed added per technology in tiles/second.
# Source: AoE2 wiki – Wheelbarrow / Hand Cart technology pages.
WHEELBARROW_SPEED_BONUS: float = 0.05
HAND_CART_SPEED_BONUS: float = 0.05

# ---------------------------------------------------------------------------
# Unit training
# ---------------------------------------------------------------------------

# Villager base training time at 1.0× speed (milliseconds).
VILLAGER_TRAIN_MS: int = 25_000

# Villager object ID
VILLAGER_UNIT_ID: int = 83
