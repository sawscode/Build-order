"""
Phase 1 – Passability grid construction.

Builds a 2-D NumPy boolean array from a parsed mgz Match object, marking
tiles as passable (True) or impassable (False).  Impassable tiles are:
  - Water / deep-water terrain
  - Tree GAIA objects (1×1 footprint)
  - Building footprints of all player-owned structures

Cliff passability is not modelled at this layer; AoE2 DE uses
elevation-based cliff rendering rather than dedicated terrain IDs.
See README_pathing.md for a full list of known approximations.
"""

from __future__ import annotations

import numpy as np

from .constants import (
    WATER_TERRAIN_IDS,
    TC_OBJECT_IDS,
    TREE_NAME_KEYWORDS,
    BUILDING_FOOTPRINTS,
)


class MapGrid:
    """2-D passability grid for one AoE2 replay map.

    Attributes
    ----------
    dimension : int
        Number of tiles along each axis (map is square).
    passable : np.ndarray of bool, shape (dimension, dimension)
        True = land-unit can enter this tile.
    """

    def __init__(
        self,
        dimension: int,
        tiles: list,
        gaia: list,
        players: list,
        *,
        exclude_tc: bool = True,
    ) -> None:
        """
        Parameters
        ----------
        dimension : int
            Map side length in tiles.
        tiles : list[mgz Tile]
            Flat list of Tile objects from match.map.tiles.
        gaia : list[mgz Object]
            GAIA objects from match.gaia.
        players : list[mgz Player]
            Player objects from match.players.
        exclude_tc : bool
            When True, the Town Center tiles for each player are NOT marked
            as impassable so that the TC position can serve as a valid flow
            field destination.  Defaults to True.
        """
        self.dimension = dimension
        self.passable = np.ones((dimension, dimension), dtype=bool)

        self._mark_water_terrain(tiles)
        self._mark_tree_obstacles(gaia)
        self._mark_building_footprints(players, exclude_tc=exclude_tc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _mark_water_terrain(self, tiles: list) -> None:
        dim = self.dimension
        for tile in tiles:
            if tile.terrain in WATER_TERRAIN_IDS:
                x, y = int(tile.position.x), int(tile.position.y)
                if 0 <= x < dim and 0 <= y < dim:
                    self.passable[y, x] = False

    def _mark_tree_obstacles(self, gaia: list) -> None:
        dim = self.dimension
        for obj in gaia:
            if obj.name and any(kw in obj.name.lower() for kw in TREE_NAME_KEYWORDS):
                x, y = int(obj.position.x), int(obj.position.y)
                if 0 <= x < dim and 0 <= y < dim:
                    self.passable[y, x] = False

    def _mark_building_footprints(self, players: list, *, exclude_tc: bool) -> None:
        dim = self.dimension
        tc_tiles: set[tuple[int, int]] = set()

        if exclude_tc:
            for player in players:
                for obj in player.objects:
                    if obj.object_id in TC_OBJECT_IDS:
                        tc_tiles.add((int(obj.position.x), int(obj.position.y)))

        for player in players:
            for obj in player.objects:
                fp = BUILDING_FOOTPRINTS.get(obj.object_id)
                if fp is None:
                    continue
                cx, cy = int(obj.position.x), int(obj.position.y)
                half = fp // 2
                for dy in range(fp):
                    for dx in range(fp):
                        nx = cx - half + dx
                        ny = cy - half + dy
                        if not (0 <= nx < dim and 0 <= ny < dim):
                            continue
                        if exclude_tc and (nx, ny) in tc_tiles:
                            continue
                        self.passable[ny, nx] = False

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_match(cls, match) -> "MapGrid":
        """Construct a MapGrid directly from a parsed mgz Match object."""
        return cls(
            dimension=match.map.dimension,
            tiles=match.map.tiles,
            gaia=match.gaia,
            players=match.players,
        )

    # ------------------------------------------------------------------
    # Debug visualisation (Phase 1 output)
    # ------------------------------------------------------------------

    def debug_plot(
        self,
        output_path: str | None = None,
        title: str = "Map Passability Grid",
        player_positions: dict | None = None,
    ):
        """Render a matplotlib heatmap of the passability grid.

        Parameters
        ----------
        output_path : str or None
            If given, save the figure to this path instead of showing it.
        title : str
            Figure title.
        player_positions : dict {label: (x, y)} or None
            Optional points of interest to annotate on the plot.
        """
        try:
            import matplotlib.pyplot as plt
        except ImportError as exc:
            raise ImportError("matplotlib is required for debug plots") from exc

        fig, ax = plt.subplots(figsize=(10, 10))
        ax.imshow(
            self.passable,
            origin="lower",
            cmap="RdYlGn",
            vmin=0,
            vmax=1,
            aspect="equal",
            interpolation="nearest",
        )
        ax.set_title(title)
        ax.set_xlabel("Tile X")
        ax.set_ylabel("Tile Y")

        if player_positions:
            for label, (px, py) in player_positions.items():
                ax.plot(px, py, "b^", markersize=10)
                ax.annotate(
                    label,
                    (px, py),
                    textcoords="offset points",
                    xytext=(5, 5),
                    color="white",
                    fontsize=8,
                )

        if output_path:
            fig.savefig(output_path, dpi=100, bbox_inches="tight")
            plt.close(fig)
        else:
            plt.show()

        return fig

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def find_nearest_passable(self, tile: tuple[int, int]) -> tuple[int, int] | None:
        """Return the closest passable tile to *tile* using BFS.

        Returns *tile* itself if it is already passable, otherwise returns
        the nearest passable tile, or None if the entire map is impassable.
        """
        from collections import deque

        dim = self.dimension
        x, y = tile
        if 0 <= x < dim and 0 <= y < dim and self.passable[y, x]:
            return tile

        queue: deque[tuple[int, int]] = deque([(x, y)])
        visited: set[tuple[int, int]] = {(x, y)}

        while queue:
            cx, cy = queue.popleft()
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) not in visited and 0 <= nx < dim and 0 <= ny < dim:
                    visited.add((nx, ny))
                    if self.passable[ny, nx]:
                        return (nx, ny)
                    queue.append((nx, ny))

        return None
