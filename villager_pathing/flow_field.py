"""
Phase 2 – Flow field computation via Dijkstra flood-fill.

Given a MapGrid and a single destination tile, computes:
  - cost_to_goal : (dim, dim) float32 array — minimum path cost from every
    tile to the destination (tiles/second units, accounting for diagonal moves).
  - direction_x / direction_y : (dim, dim) float32 arrays — unit step vector
    pointing from each tile toward the destination.

Movement model
--------------
8-directional movement on a uniform-cost grid:
  cardinal step  → cost 1.0
  diagonal step  → cost √2 ≈ 1.414
All impassable tiles remain at cost=inf and direction=(0, 0).

Performance
-----------
Target: <200 ms on a 240×240 map using plain Python + NumPy.
Dijkstra with a binary heap: O(N log N) where N ≤ 57 600.  Measured well
under 200 ms on CPython 3.11 without compiled extensions.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import numpy as np

from .grid import MapGrid

SQRT2: float = math.sqrt(2)

# (dx, dy, transition cost)
_DIRECTIONS: list[tuple[int, int, float]] = [
    (-1,  0, 1.0),
    ( 1,  0, 1.0),
    ( 0, -1, 1.0),
    ( 0,  1, 1.0),
    (-1, -1, SQRT2),
    (-1,  1, SQRT2),
    ( 1, -1, SQRT2),
    ( 1,  1, SQRT2),
]

# Offsets only (used for direction-vector computation)
_OFFSETS: list[tuple[int, int]] = [(dx, dy) for dx, dy, _ in _DIRECTIONS]


@dataclass
class FlowField:
    """Result of a single-destination flow field computation.

    Attributes
    ----------
    cost : np.ndarray (dim, dim) float32
        Minimum path cost (in tiles, cardinal=1, diagonal=√2) from each tile
        to *destination*.  Unreachable / impassable tiles hold np.inf.
    direction_x : np.ndarray (dim, dim) float32
    direction_y : np.ndarray (dim, dim) float32
        Integer-valued step vectors (each component ∈ {-1, 0, 1}) pointing
        toward the destination.  Zero vector on destination tile and on
        impassable / unreachable tiles.
    destination : tuple[int, int]
        The (x, y) tile this field was computed from.
    dimension : int
        Map side length.
    """

    cost: np.ndarray
    direction_x: np.ndarray
    direction_y: np.ndarray
    destination: tuple[int, int]
    dimension: int


def compute_flow_field(grid: MapGrid, destination_tile: tuple[int, int]) -> FlowField:
    """Compute a flow field from *destination_tile* across *grid*.

    Uses Dijkstra's algorithm (binary heap) for correctness with 8-directional
    diagonal movement (non-uniform costs).

    Parameters
    ----------
    grid : MapGrid
    destination_tile : (x, y)
        Target tile — must be within map bounds.  Does not need to be
        passable; if impassable, the nearest passable tile is used
        automatically.

    Returns
    -------
    FlowField
    """
    dim = grid.dimension
    passable = grid.passable

    # Resolve destination to nearest passable tile
    dest_x, dest_y = destination_tile
    if not (0 <= dest_x < dim and 0 <= dest_y < dim and passable[dest_y, dest_x]):
        resolved = grid.find_nearest_passable(destination_tile)
        if resolved is None:
            raise ValueError("No passable tile found near destination")
        dest_x, dest_y = resolved

    # ------------------------------------------------------------------
    # Dijkstra flood-fill from destination
    #
    # Uses a flat Python list for cost storage rather than indexing into
    # a NumPy array element-by-element.  Single-element numpy access has
    # boxing/unboxing overhead in tight Python loops; a flat list avoids
    # this and brings a 240×240 map well under the 200 ms target.
    # ------------------------------------------------------------------
    INF = float("inf")
    flat_pass = passable.ravel().tolist()   # Python booleans, O(1) lookup
    flat_cost = [INF] * (dim * dim)
    start_idx = dest_y * dim + dest_x
    flat_cost[start_idx] = 0.0

    # heap entries: (cost, flat_index)
    heap: list[tuple[float, int]] = [(0.0, start_idx)]

    while heap:
        c, idx = heapq.heappop(heap)
        if c > flat_cost[idx]:
            continue  # stale entry
        cy, cx = divmod(idx, dim)
        for dx, dy, dc in _DIRECTIONS:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < dim and 0 <= ny < dim:
                nidx = ny * dim + nx
                if flat_pass[nidx]:
                    nc = c + dc
                    if nc < flat_cost[nidx]:
                        flat_cost[nidx] = nc
                        heapq.heappush(heap, (nc, nidx))

    cost = np.array(flat_cost, dtype=np.float32).reshape(dim, dim)

    # ------------------------------------------------------------------
    # Derive direction vectors (vectorised with NumPy padding)
    # ------------------------------------------------------------------
    direction_x, direction_y = _compute_directions(cost, passable, dim)

    return FlowField(
        cost=cost,
        direction_x=direction_x,
        direction_y=direction_y,
        destination=(dest_x, dest_y),
        dimension=dim,
    )


def _compute_directions(
    cost: np.ndarray,
    passable: np.ndarray,
    dim: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised direction-vector derivation.

    For each passable cell, finds the neighbour with minimum cost and stores
    the (dx, dy) step vector pointing toward it.

    Uses a padded cost array so all 8 neighbour lookups are simple slices —
    no explicit boundary checks needed.
    """
    dx_out = np.zeros((dim, dim), dtype=np.float32)
    dy_out = np.zeros((dim, dim), dtype=np.float32)
    # Initialize to current cell's own cost so the destination (cost=0)
    # only updates when a neighbour has cost < 0 — which never happens,
    # keeping its direction vector at (0,0) as required.
    min_nbr = cost.copy()

    # Pad cost with inf so out-of-bounds neighbours never win
    padded = np.pad(cost, 1, constant_values=np.inf)

    for odx, ody in _OFFSETS:
        # Neighbour of cell (x, y) at offset (odx, ody) sits at (x+odx, y+ody).
        # In the padded array, (x, y) maps to (y+1, x+1), so the neighbour is
        # at padded[y+ody+1, x+odx+1] = padded[1+ody : dim+1+ody, 1+odx : dim+1+odx].
        #
        # For negative offsets Python's slice arithmetic still works correctly:
        #   ody=-1 → padded[0:dim, ...]   (row above, padded row is +inf)
        #   odx= 1 → padded[..., 2:dim+2] (column to the right)
        row_start = 1 + ody
        row_end   = dim + 1 + ody  # if ody=0 → 1:dim+1 (all rows, correct)
        col_start = 1 + odx
        col_end   = dim + 1 + odx

        nbr_cost = padded[row_start:row_end, col_start:col_end]
        better = nbr_cost < min_nbr
        dx_out = np.where(better, float(odx), dx_out)
        dy_out = np.where(better, float(ody), dy_out)
        min_nbr = np.minimum(min_nbr, nbr_cost)

    # Zero out impassable and unreachable cells
    mask = np.isinf(cost) | ~passable
    dx_out[mask] = 0.0
    dy_out[mask] = 0.0

    return dx_out.astype(np.float32), dy_out.astype(np.float32)


def walk_path(
    flow_field: FlowField,
    start_tile: tuple[int, int],
) -> list[tuple[int, int]]:
    """Trace the greedy path from *start_tile* to the flow field destination.

    Follows direction vectors step by step.  Returns a list of (x, y) tiles
    including start and destination.  Detects cycles (shouldn't occur on a
    valid flow field but included as a safety guard).
    """
    path = [start_tile]
    x, y = start_tile
    dim = flow_field.dimension
    visited: set[tuple[int, int]] = {(x, y)}

    for _ in range(dim * dim):
        dx = int(flow_field.direction_x[y, x])
        dy = int(flow_field.direction_y[y, x])
        if dx == 0 and dy == 0:
            break
        x += dx
        y += dy
        if (x, y) in visited:
            break
        visited.add((x, y))
        path.append((x, y))

    return path


# ------------------------------------------------------------------
# Debug visualisation (Phase 2 output)
# ------------------------------------------------------------------

def debug_plot(
    grid: MapGrid,
    flow_field: FlowField,
    output_path: str | None = None,
    player_positions: dict | None = None,
    resource_tiles: dict | None = None,
    title: str = "Flow Field",
):
    """Render a quiver/vector plot of the flow field overlaid on the grid.

    Parameters
    ----------
    grid : MapGrid
    flow_field : FlowField
    output_path : str or None
        If given, save the figure rather than displaying it.
    player_positions : dict {label: (x, y)} or None
        Points of interest (e.g. start positions) to annotate.
    resource_tiles : dict {resource_type: (x, y)} or None
        Resource cluster centroids to annotate.
    title : str
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ImportError("matplotlib is required for debug plots") from exc

    dim = flow_field.dimension
    fig, ax = plt.subplots(figsize=(12, 12))

    # Background: passability heat map
    ax.imshow(
        grid.passable,
        origin="lower",
        cmap="RdYlGn",
        vmin=0,
        vmax=1,
        aspect="equal",
        interpolation="nearest",
        alpha=0.35,
    )

    # Cost heat map (log-scaled for contrast)
    finite_cost = np.where(np.isinf(flow_field.cost), np.nan, flow_field.cost)
    im = ax.imshow(
        finite_cost,
        origin="lower",
        cmap="plasma_r",
        aspect="equal",
        interpolation="nearest",
        alpha=0.45,
    )
    fig.colorbar(im, ax=ax, fraction=0.03, label="Cost to destination (tiles)")

    # Quiver: sample every ~6 tiles to avoid visual clutter
    step = max(1, dim // 40)
    ys, xs = np.mgrid[0:dim:step, 0:dim:step]
    U = flow_field.direction_x[::step, ::step]
    V = flow_field.direction_y[::step, ::step]
    finite = ~np.isinf(flow_field.cost[::step, ::step])
    ax.quiver(
        xs[finite], ys[finite],
        U[finite], V[finite],
        color="steelblue",
        scale=30,
        alpha=0.6,
        width=0.002,
    )

    # Destination marker
    dx, dy = flow_field.destination
    ax.plot(dx, dy, "r*", markersize=16, zorder=10, label=f"Destination {flow_field.destination}")

    if player_positions:
        for label, (px, py) in player_positions.items():
            ax.plot(px, py, "b^", markersize=10, zorder=9)
            ax.annotate(label, (px, py), textcoords="offset points", xytext=(6, 4), fontsize=7)

    if resource_tiles:
        colours = {"wood": "green", "gold": "gold", "stone": "grey", "food": "orange"}
        for rtype, (rx, ry) in resource_tiles.items():
            colour = colours.get(rtype, "cyan")
            ax.plot(rx, ry, "o", color=colour, markersize=10, zorder=9)
            ax.annotate(rtype, (rx, ry), textcoords="offset points", xytext=(4, 4), fontsize=7)

    ax.set_title(title)
    ax.set_xlabel("Tile X")
    ax.set_ylabel("Tile Y")
    ax.legend(loc="upper right", fontsize=8)

    if output_path:
        fig.savefig(output_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
    else:
        plt.show()

    return fig
