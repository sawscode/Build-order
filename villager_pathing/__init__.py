"""
villager_pathing – AoE2 villager travel-time estimation via flow fields.

Public API
----------
MapGrid            – passability grid from a parsed mgz replay
FlowField          – result of compute_flow_field()
compute_flow_field – Dijkstra flood-fill from a destination tile
estimate_travel_time – path cost → game-seconds
"""

from .grid import MapGrid
from .flow_field import FlowField, compute_flow_field, walk_path
from .travel_time import estimate_travel_time, get_villager_speed, extract_tech_timeline, get_tech_state_at
from .validate import extract_villager_trips, validate_trips, find_resource_cluster

__all__ = [
    "MapGrid",
    "FlowField",
    "compute_flow_field",
    "walk_path",
    "estimate_travel_time",
    "get_villager_speed",
    "extract_tech_timeline",
    "get_tech_state_at",
    "extract_villager_trips",
    "validate_trips",
    "find_resource_cluster",
]
