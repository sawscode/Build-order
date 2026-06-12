"""
Phase 4 – Validation: compare estimated vs. observed villager travel times.

A "villager trip" is:
  1. A villager finishes training at the TC (Queue timestamp + training time).
  2. It walks to a resource node and begins gathering (first Gather timestamp
     for that villager's instance ID).
  3. Observed travel time = gather_timestamp − exit_timestamp.
  4. Estimated travel time = flow field path cost / villager speed.

Matching approach
-----------------
Because the game engine does not expose the instance ID of a newly-queued
unit in the Queue action itself, we use a greedy interval match:

  - Collect all villager Queue timestamps and compute their expected TC-exit
    times (Queue time + training time).
  - Collect the first Gather event per villager instance ID.
  - For each Gather event, find the unmatched exit time that most plausibly
    precedes it (closest exit time ≤ gather time, within 60 s).

This is an approximation; the documentation in README_pathing.md covers the
known limitations.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from .constants import (
    TC_OBJECT_IDS,
    VILLAGER_UNIT_ID,
    VILLAGER_TRAIN_MS,
    RESOURCE_NAME_KEYWORDS,
)
from .flow_field import FlowField, compute_flow_field
from .grid import MapGrid
from .travel_time import estimate_travel_time, get_tech_state_at


# Maximum plausible travel time in milliseconds (60 s).
# Trips where the gap between exit and gather exceeds this are discarded
# as likely to be villager reassignments rather than fresh TC exits.
_MAX_TRIP_MS: int = 60_000


def find_resource_cluster(
    gaia: list,
    tc_pos: tuple[int | float, int | float],
    resource_type: str,
    n_nearest: int = 7,
) -> tuple[int, int] | None:
    """Find the centroid of the nearest resource cluster to the TC.

    Scans GAIA objects for the given resource type, sorts by distance from
    *tc_pos*, takes the *n_nearest* closest, and returns their integer
    centroid tile.

    Returns None if no matching GAIA objects are found.
    """
    keywords = RESOURCE_NAME_KEYWORDS.get(resource_type, frozenset())
    if not keywords:
        return None

    candidates: list[tuple[float, float]] = []
    for obj in gaia:
        if obj.name and any(kw in obj.name.lower() for kw in keywords):
            candidates.append((float(obj.position.x), float(obj.position.y)))

    if not candidates:
        return None

    tc_x, tc_y = float(tc_pos[0]), float(tc_pos[1])
    candidates.sort(key=lambda p: (p[0] - tc_x) ** 2 + (p[1] - tc_y) ** 2)

    nearby = candidates[:n_nearest]
    cx = sum(p[0] for p in nearby) / len(nearby)
    cy = sum(p[1] for p in nearby) / len(nearby)
    return (int(round(cx)), int(round(cy)))


def _infer_resource_from_param(param: str | None) -> str | None:
    """Map a Gather action param string to a resource type."""
    if not param:
        return None
    p = param.lower()
    for rtype, keywords in RESOURCE_NAME_KEYWORDS.items():
        if any(kw in p for kw in keywords):
            return rtype
    if "farm" in p or "mill" in p:
        return "food"
    if "lumber" in p:
        return "wood"
    if "mining" in p:
        return "gold"
    return None


def extract_villager_trips(
    inputs: list,
    player_number: int,
    speed_mult: float = 1.0,
    max_trips: int = 10,
) -> list[dict]:
    """Extract villager trip events from the input stream.

    Returns
    -------
    list of dict, each containing:
      - villager_id : int (instance ID)
      - exit_time_ms : int  (when villager left the TC)
      - gather_time_ms : int  (timestamp of first Gather action)
      - resource_type : str
      - observed_travel_s : float  (gather_time_ms − exit_time_ms, in seconds)
    """
    train_ms = int(VILLAGER_TRAIN_MS / speed_mult)

    # Collect TC-exit times from Queue actions (villager training)
    exit_times: list[int] = []
    for inp in inputs:
        if inp.player is None or inp.player.number != player_number:
            continue
        if inp.type != "Queue":
            continue
        if inp.payload.get("unit_id") != VILLAGER_UNIT_ID:
            continue
        t_ms = int(inp.timestamp.total_seconds() * 1000)
        amount = int(inp.payload.get("amount") or 1)
        # Approximate: each queued villager exits one training-time slot later
        for i in range(amount):
            exit_times.append(t_ms + train_ms * (i + 1))

    exit_times.sort()

    # First Gather event per villager instance ID
    first_gather: dict[int, dict] = {}
    for inp in inputs:
        if inp.player is None or inp.player.number != player_number:
            continue
        if inp.type != "Gather":
            continue
        t_ms = int(inp.timestamp.total_seconds() * 1000)
        resource = _infer_resource_from_param(inp.param)
        if resource is None:
            continue
        for vid in inp.payload.get("object_ids") or []:
            if vid not in first_gather:
                first_gather[vid] = {"time_ms": t_ms, "resource_type": resource}

    # Greedy match: for each first-gather, find the best unmatched exit time
    used: set[int] = set()
    trips: list[dict] = []

    for vid, g_info in sorted(first_gather.items(), key=lambda kv: kv[1]["time_ms"]):
        gather_ms = g_info["time_ms"]
        resource = g_info["resource_type"]

        best_idx: int | None = None
        best_diff = float("inf")
        for i, exit_ms in enumerate(exit_times):
            if i in used:
                continue
            if 0 < (gather_ms - exit_ms) < _MAX_TRIP_MS:
                diff = gather_ms - exit_ms
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i

        if best_idx is None:
            continue

        used.add(best_idx)
        exit_ms = exit_times[best_idx]
        trips.append(
            {
                "villager_id": vid,
                "exit_time_ms": exit_ms,
                "gather_time_ms": gather_ms,
                "resource_type": resource,
                "observed_travel_s": round((gather_ms - exit_ms) / 1000.0, 3),
            }
        )

        if len(trips) >= max_trips:
            break

    return trips


def validate_trips(
    trips: list[dict],
    grid: MapGrid,
    tc_tile: tuple[int, int],
    gaia: list,
    tech_timeline: list,
) -> list[dict]:
    """Compare estimated vs. observed travel times for each trip.

    For each trip, a flow field is computed from the nearest resource cluster
    of the matching resource type to the TC.  Flow fields for the same
    destination are cached so each unique destination is computed only once.

    Parameters
    ----------
    trips : output of extract_villager_trips
    grid : MapGrid
    tc_tile : (x, y) start tile (TC position)
    gaia : match.gaia
    tech_timeline : output of travel_time.extract_tech_timeline

    Returns
    -------
    list of dict, each containing all trip fields plus:
      - resource_tile : (x, y)
      - estimated_travel_s : float
      - deviation_pct : float  (|est − obs| / obs × 100)
      - within_20pct : bool
    """
    ff_cache: dict[tuple[int, int], FlowField | None] = {}
    results: list[dict] = []

    for trip in trips:
        resource_type = trip["resource_type"]

        cluster = find_resource_cluster(gaia, tc_tile, resource_type)
        if cluster is None:
            continue

        # Resolve cluster to nearest passable tile
        cluster_passable = grid.find_nearest_passable(cluster)
        if cluster_passable is None:
            continue

        if cluster_passable not in ff_cache:
            ff_cache[cluster_passable] = compute_flow_field(grid, cluster_passable)

        ff = ff_cache[cluster_passable]
        if ff is None:
            continue

        # Tech state at villager exit time
        techs = get_tech_state_at(tech_timeline, trip["exit_time_ms"])
        estimated_s = estimate_travel_time(tc_tile, ff, techs)
        if estimated_s is None:
            continue

        observed_s = trip["observed_travel_s"]
        deviation_pct = (
            abs(estimated_s - observed_s) / max(observed_s, 0.1) * 100.0
        )

        results.append(
            {
                **trip,
                "resource_tile": cluster_passable,
                "estimated_travel_s": round(estimated_s, 3),
                "deviation_pct": round(deviation_pct, 1),
                "within_20pct": deviation_pct <= 20.0,
            }
        )

    return results


def save_results(
    results: list[dict],
    output_path: str,
    fmt: str = "json",
) -> None:
    """Persist validation results to *output_path*.

    Parameters
    ----------
    results : output of validate_trips
    output_path : str
    fmt : "json" or "csv"
    """
    path = Path(output_path)
    if fmt == "csv":
        if not results:
            path.write_text("")
            return
        # Flatten tuples so CSV stays flat
        flat = []
        for r in results:
            row = dict(r)
            if isinstance(row.get("resource_tile"), tuple):
                row["resource_tile_x"], row["resource_tile_y"] = row.pop("resource_tile")
            if isinstance(row.get("tc_tile"), tuple):
                row["tc_tile_x"], row["tc_tile_y"] = row.pop("tc_tile")
            flat.append(row)
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=flat[0].keys())
            writer.writeheader()
            writer.writerows(flat)
    else:
        # JSON – tuples become lists, that's fine
        path.write_text(json.dumps(results, indent=2))
