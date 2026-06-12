"""
Phase 5 – End-to-end entry point.

Usage
-----
    python -m villager_pathing <replay_file> [options]

    python -m villager_pathing game.aoe2record -p 1 -o results.json
    python -m villager_pathing game.aoe2record --debug-grid grid.png --debug-ff ff.png
    python -m villager_pathing game.zip -p 2 -o results.csv --format csv

Options
-------
    replay          Path to .aoe2record or .zip containing one.
    -p / --player   Player number to analyse (default: 1).
    -o / --output   Output file (.json or .csv).
    --format        json | csv  (default: json).
    --debug-grid    Save passability grid PNG to this path.
    --debug-ff      Save flow field quiver PNG to this path.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import numpy as np


def _load_replay(path: str) -> io.BytesIO:
    data = Path(path).read_bytes()
    if len(data) >= 4 and data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            name = next(
                (
                    n
                    for n in zf.namelist()
                    if n.lower().endswith(".aoe2record")
                    or n.lower().endswith(".mgz")
                ),
                None,
            )
            if name is None:
                raise ValueError("ZIP contains no .aoe2record / .mgz file")
            data = zf.read(name)
    return io.BytesIO(data)


def _find_tc_tile(player) -> tuple[int, int] | None:
    """Resolve player TC position to a tile coordinate."""
    from .constants import TC_OBJECT_IDS

    if player.position and player.position.x is not None:
        return (int(player.position.x), int(player.position.y))
    for obj in player.objects:
        if obj.object_id in TC_OBJECT_IDS:
            return (int(obj.position.x), int(obj.position.y))
    return None


def run(
    replay_path: str,
    *,
    player_number: int = 1,
    output_path: str | None = None,
    output_format: str = "json",
    debug_grid: str | None = None,
    debug_ff: str | None = None,
) -> dict:
    """Execute the full pipeline and return the results dict."""
    from mgz.model import parse_match

    from .flow_field import compute_flow_field
    from .flow_field import debug_plot as ff_debug_plot
    from .grid import MapGrid
    from .travel_time import extract_tech_timeline
    from .validate import (
        extract_villager_trips,
        find_resource_cluster,
        save_results,
        validate_trips,
    )

    # ------------------------------------------------------------------
    print(f"Loading replay: {replay_path}")
    handle = _load_replay(replay_path)
    match = parse_match(handle)

    player = next((p for p in match.players if p.number == player_number), None)
    if player is None:
        raise ValueError(
            f"Player {player_number} not found. Available: "
            + ", ".join(str(p.number) for p in match.players)
        )

    speed_mult = (match.speed_id or 100) / 100.0
    print(f"Map: {match.map.name}  Dimension: {match.map.dimension}  Speed: {match.speed}")
    print(f"Player {player_number}: {player.name} ({player.civilization})")

    # ------------------------------------------------------------------
    # Phase 1 – Passability grid
    # ------------------------------------------------------------------
    print("\n[Phase 1] Building passability grid …")
    t0 = time.perf_counter()
    grid = MapGrid.from_match(match)
    t1 = time.perf_counter()
    passable_pct = grid.passable.sum() / grid.passable.size * 100
    print(
        f"  {grid.dimension}×{grid.dimension} tiles, "
        f"{passable_pct:.1f}% passable  [{(t1-t0)*1000:.0f} ms]"
    )

    if debug_grid:
        grid.debug_plot(
            output_path=debug_grid,
            title=f"Passability – {match.map.name}",
            player_positions={
                f"P{p.number} TC": (_find_tc_tile(p) or (0, 0))
                for p in match.players
                if _find_tc_tile(p)
            },
        )
        print(f"  Grid plot saved → {debug_grid}")

    # ------------------------------------------------------------------
    # Phase 2 – Flow field from TC
    # ------------------------------------------------------------------
    tc_tile = _find_tc_tile(player)
    if tc_tile is None:
        raise RuntimeError("Could not determine TC tile for the selected player.")

    # Resolve to nearest passable tile (TC area may be marked as building)
    tc_passable = grid.find_nearest_passable(tc_tile)
    if tc_passable is None:
        raise RuntimeError("TC tile and surroundings are all impassable — map extraction issue.")

    print(f"\n[Phase 2] Computing flow field from TC at {tc_tile} …")
    t0 = time.perf_counter()
    flow_field = compute_flow_field(grid, tc_passable)
    t1 = time.perf_counter()
    elapsed_ms = (t1 - t0) * 1000
    print(f"  Completed in {elapsed_ms:.0f} ms  (target: <200 ms)")

    if debug_ff:
        resource_tiles = {}
        for rtype in ("wood", "gold", "stone", "food"):
            ct = find_resource_cluster(match.gaia, tc_tile, rtype)
            if ct:
                resource_tiles[rtype] = ct
        ff_debug_plot(
            grid,
            flow_field,
            output_path=debug_ff,
            player_positions={f"P{player_number} TC": tc_passable},
            resource_tiles=resource_tiles,
            title=f"Flow Field – TC at {tc_passable} – {match.map.name}",
        )
        print(f"  Flow field plot saved → {debug_ff}")

    # ------------------------------------------------------------------
    # Phase 3 & 4 – Extract trips and validate
    # ------------------------------------------------------------------
    print("\n[Phase 3–4] Extracting villager trips and validating …")
    tech_timeline = extract_tech_timeline(match.inputs, player_number)
    trips = extract_villager_trips(
        match.inputs,
        player_number,
        speed_mult=speed_mult,
        max_trips=10,
    )
    print(f"  Found {len(trips)} matchable villager trip(s)")

    results = validate_trips(trips, grid, tc_passable, match.gaia, tech_timeline)

    if results:
        header = f"  {'Resource':<8} {'Observed':>10} {'Estimated':>10} {'Dev%':>7} {'OK':>4}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for r in results:
            ok = "yes" if r["within_20pct"] else "no"
            print(
                f"  {r['resource_type']:<8} "
                f"{r['observed_travel_s']:>9.1f}s "
                f"{r['estimated_travel_s']:>9.1f}s "
                f"{r['deviation_pct']:>6.1f}%  {ok}"
            )
        avg_dev = sum(r["deviation_pct"] for r in results) / len(results)
        within_20 = sum(1 for r in results if r["within_20pct"])
        print(f"\n  Average deviation: {avg_dev:.1f}%   Within 20%: {within_20}/{len(results)}")
    else:
        print("  No validatable trips found (see README_pathing.md for possible reasons).")

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    output_data = {
        "map": match.map.name,
        "dimension": grid.dimension,
        "player_number": player_number,
        "player_name": player.name,
        "tc_tile": list(tc_passable),
        "flow_field_ms": round(elapsed_ms, 1),
        "trips_found": len(trips),
        "trips_validated": len(results),
        "results": results,
    }

    if output_path:
        save_results(results, output_path, fmt=output_format)
        print(f"\nResults saved → {output_path}")

    return output_data


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m villager_pathing",
        description="AoE2 villager pathing – travel-time estimation pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("replay", help="Path to .aoe2record or .zip replay file")
    parser.add_argument(
        "-p", "--player", type=int, default=1,
        help="Player number to analyse (default: 1)",
    )
    parser.add_argument("-o", "--output", help="Output file path (.json or .csv)")
    parser.add_argument(
        "--format", dest="fmt", choices=["json", "csv"], default="json",
        help="Output format when -o is given (default: json)",
    )
    parser.add_argument(
        "--debug-grid", metavar="PATH",
        help="Save passability grid plot to PATH (requires matplotlib)",
    )
    parser.add_argument(
        "--debug-ff", metavar="PATH",
        help="Save flow field quiver plot to PATH (requires matplotlib)",
    )

    args = parser.parse_args(argv)

    try:
        run(
            args.replay,
            player_number=args.player,
            output_path=args.output,
            output_format=args.fmt,
            debug_grid=args.debug_grid,
            debug_ff=args.debug_ff,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
