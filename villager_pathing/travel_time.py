"""
Phase 3 – Travel time estimation.

Given a flow field and a start tile, returns the estimated travel time in
game-seconds for a villager to walk from that tile to the flow field
destination.

The path cost stored in the flow field is already the Dijkstra-optimal sum
of per-step distances (cardinal=1, diagonal=√2).  Dividing by the villager's
move speed (tiles/second) converts that to seconds.

Villager move speed
-------------------
Base:                 0.96 tiles/second   (AoE2 wiki – Villager unit stats)
+ Wheelbarrow (65):  +0.05 tiles/second  (AoE2 wiki – Wheelbarrow tech)
+ Hand Cart   (249): +0.05 tiles/second  (AoE2 wiki – Hand Cart tech)

These are well-documented values from AoE2 DE; note that the in-game speed
is displayed in different units in some UI mods.
"""

from __future__ import annotations

import numpy as np

from .constants import (
    VILLAGER_BASE_SPEED,
    WHEELBARROW_TECH_ID,
    HAND_CART_TECH_ID,
    WHEELBARROW_SPEED_BONUS,
    HAND_CART_SPEED_BONUS,
)
from .flow_field import FlowField


def get_villager_speed(researched_tech_ids: set[int] | frozenset[int] | None = None) -> float:
    """Return villager move speed in tiles/second.

    Parameters
    ----------
    researched_tech_ids : set of int or None
        Technology IDs already researched at the moment of travel.
        Wheelbarrow (65) and Hand Cart (249) each add 0.05 tiles/second.
    """
    if researched_tech_ids is None:
        researched_tech_ids = frozenset()
    speed = VILLAGER_BASE_SPEED
    if WHEELBARROW_TECH_ID in researched_tech_ids:
        speed += WHEELBARROW_SPEED_BONUS
    if HAND_CART_TECH_ID in researched_tech_ids:
        speed += HAND_CART_SPEED_BONUS
    return speed


def estimate_travel_time(
    start_tile: tuple[int, int],
    flow_field: FlowField,
    researched_tech_ids: set[int] | frozenset[int] | None = None,
) -> float | None:
    """Estimate villager travel time (seconds) from *start_tile* to the destination.

    Parameters
    ----------
    start_tile : (x, y)
    flow_field : FlowField
        Pre-computed flow field with the destination as its origin.
    researched_tech_ids : set of int or None
        Tech IDs researched at the time of travel; affects villager speed.

    Returns
    -------
    float or None
        Estimated seconds, or None if *start_tile* is unreachable.
    """
    x, y = start_tile
    path_cost = float(flow_field.cost[y, x])
    if np.isinf(path_cost) or np.isnan(path_cost):
        return None
    speed = get_villager_speed(researched_tech_ids)
    return path_cost / speed


def extract_tech_timeline(inputs: list, player_number: int) -> list[tuple[int, frozenset[int]]]:
    """Build a chronological list of (time_ms, frozenset_of_tech_ids).

    Scans the match input stream for Research actions by the specified player,
    accumulating the set of researched technology IDs over time.

    Parameters
    ----------
    inputs : list[mgz Input]
        match.inputs from parse_match.
    player_number : int

    Returns
    -------
    list of (time_ms: int, tech_ids: frozenset[int])
        Sorted by time.  The first entry is always (0, frozenset()) so that
        get_tech_state_at() returns an empty set before any research.
    """
    techs: set[int] = set()
    timeline: list[tuple[int, frozenset[int]]] = [(0, frozenset())]

    for inp in inputs:
        if inp.player is None or inp.player.number != player_number:
            continue
        if inp.type != "Research":
            continue
        tech_id = inp.payload.get("technology_id")
        if tech_id is not None:
            techs.add(int(tech_id))
            t_ms = int(inp.timestamp.total_seconds() * 1000)
            timeline.append((t_ms, frozenset(techs)))

    return timeline


def get_tech_state_at(
    timeline: list[tuple[int, frozenset[int]]],
    time_ms: int,
) -> frozenset[int]:
    """Return the set of researched tech IDs at a given game time.

    Parameters
    ----------
    timeline : output of extract_tech_timeline
    time_ms : int

    Returns
    -------
    frozenset[int]
    """
    result: frozenset[int] = frozenset()
    for t, techs in timeline:
        if t <= time_ms:
            result = techs
        else:
            break
    return result
