# Villager Pathing & Travel-Time MVP

Estimates villager travel times between key locations (TC ↔ resource clusters)
from a parsed AoE2 replay, using a flow-field approach.  Output is a dataset
enrichment — the goal is to test whether flow-field pathfinding is a viable
approximation of real in-game villager movement.

---

## Quick start

```bash
pip install numpy matplotlib  # mgz already in requirements.txt

# Run full pipeline (Phases 1–4), print validation table
python -m villager_pathing path/to/game.aoe2record

# With debug plots
python -m villager_pathing game.aoe2record \
    --debug-grid grid.png \
    --debug-ff   flow_field.png

# Player 2, save results to CSV
python -m villager_pathing game.aoe2record -p 2 -o results.csv --format csv
```

---

## Pipeline phases

| Phase | Module | What it does |
|-------|--------|--------------|
| 1 | `grid.py` | Builds a 2-D NumPy boolean passability grid from the replay map |
| 2 | `flow_field.py` | Dijkstra flood-fill from TC → cost-to-goal array + direction vectors |
| 3 | `travel_time.py` | `estimate_travel_time(start, flow_field, techs) → seconds` |
| 4 | `validate.py` | Compares estimated vs. observed travel times for up to 10 villager trips |
| 5 | `__main__.py` | Ties all phases together; CLI entry point |

---

## Assumptions and known approximations

### Grid construction

- **Impassable**: water terrain IDs `{1, 2, 22, 23, 26, 34}`, tree GAIA objects
  (matched by name keyword), and building footprints of all player-owned
  structures.
- **Cliffs**: AoE2 DE renders cliffs via elevation differences rather than
  dedicated terrain IDs; cliffs are therefore **not** blocked at the grid layer.
  Maps with prominent cliff terrain (e.g. Black Forest variants with cliff
  edges) will underestimate travel time.
- **TC passability**: the TC tile itself is kept passable so it can serve as a
  valid flow field destination. Units cannot actually walk through a TC in-game,
  but for the purpose of *arriving* at the TC this is the correct behaviour.
- **Building footprints**: TC = 4×4, Castle/Market = 4×4, most military
  buildings = 3×3, small dropoff buildings = 2×2. Centre-anchored.
- **Terrain ID coverage**: exact water terrain IDs vary between game builds and
  custom datasets. The set above covers standard AoE2 DE. Maps that use
  modded terrain palettes may require extending `WATER_TERRAIN_IDS` in
  `constants.py`.

### Flow field computation

- **Movement cost**: cardinal step = 1.0, diagonal step = √2 ≈ 1.414.
  This is an 8-directional grid approximation; real AoE2 pathfinding uses
  continuous 2-D steering and a hierarchical waypoint graph.
- **Single destination per field**: one flow field is computed per unique
  resource cluster centroid. Multiple resource clusters of the same type
  (e.g. two separate wood lines) use the same field (the nearest cluster).
- **Static grid**: the flow field is computed once from the initial map state.
  Trees cut during the game, farms placed, and buildings constructed are not
  reflected in the grid. This is the largest source of growing inaccuracy
  in late-game comparisons.

### Travel time estimation

- **Villager speed**: base 0.96 tiles/s (AoE2 wiki, Villager stats).
  Wheelbarrow (+0.05 tiles/s, tech ID 65), Hand Cart (+0.05 tiles/s,
  tech ID 249) are applied if the relevant tech was researched before
  the trip started.
- **No villager collision / density penalty**: real AoE2 villagers slow
  down in tight groups (especially in forest corridors). This model assumes
  free movement at base speed.

### Validation matching

- A villager "exit time" is approximated as `Queue_timestamp + training_time`.
  Because the game engine does not expose the new unit's instance ID in the
  Queue action, trips are matched greedily by closest unmatched exit time
  within 60 s of the Gather action.
- Only the **first** Gather action per villager instance is used. Subsequent
  reassignments are not tracked.
- Trips with an observed travel time outside (0, 60 s) are discarded as
  likely re-assignments or late-game matches.

---

## What Phase 2 (next iteration) would add

- **Incremental flow field recompute** as buildings are placed and trees are
  cut, so that mid-game and late-game estimates stay accurate.
- **Multiple resource cluster destinations**: one flow field per cluster
  per resource type, so the nearest *available* cluster is always used.
- **Depletion-aware reassignment**: when a gold mine or stone mine runs out
  the model re-routes to the next nearest cluster.
- **Local avoidance / density blending**: penalise tiles with high villager
  occupancy to model the slow-down in crowded passages (especially relevant
  for wood-line access in early Feudal Age).

---

## Module structure

```
villager_pathing/
├── __init__.py        Public API exports
├── __main__.py        CLI entry point (python -m villager_pathing)
├── constants.py       Terrain IDs, object IDs, speed values, tech IDs
├── grid.py            MapGrid – Phase 1
├── flow_field.py      FlowField, compute_flow_field – Phase 2
├── travel_time.py     estimate_travel_time, tech timeline – Phase 3
└── validate.py        extract_villager_trips, validate_trips – Phase 4
README_pathing.md      This file
```

---

## Dependencies

| Package | Purpose | Already in repo? |
|---------|---------|-----------------|
| `numpy` | Grid array, flow field maths | Add to requirements |
| `matplotlib` | Debug plots only | Add to requirements (optional) |
| `mgz` | Replay parsing | Yes (`requirements.txt`) |

The core pipeline (Phases 1–4) requires only `numpy` and the standard
library.  `matplotlib` is imported lazily inside `debug_plot()` so it
is not required to run the CLI without `--debug-grid` / `--debug-ff`.
