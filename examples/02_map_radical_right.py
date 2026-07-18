"""Radical-right vote share across three national elections, on one map.

Run from anywhere:  python examples/02_map_radical_right.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))            # use the in-repo blue_db package
import matplotlib
matplotlib.use("Agg")                    # headless-safe
import matplotlib.pyplot as plt

from blue_db import BlueDB, BlueGeo

COUNTRIES = ["FR", "DE", "IT"]
CMAP, VMIN, VMAX = "YlOrRd", 0, 50


def main() -> None:
    db, geo = BlueDB(ROOT / "dist"), BlueGeo()

    # A single call: the latest national election in each country (first round
    # by default for two-round systems), radical-right vote share per LAU.
    res = db.results(countries=COUNTRIES, election_type="national",
                     latest_per_country=True, geo_level="LAU",
                     aggregate_by="ideology")
    res = db.vote_shares(res)                     # add <ideology>_pct columns

    # Attach the matching municipal geometries in one call: each unit is
    # resolved at its own election-year vintage and joined onto the results;
    # overseas territories are excluded by default (continental=True).
    gdf = geo.attach(res)

    ax = gdf.plot(column="radical right_pct", cmap=CMAP,
                  vmin=VMIN, vmax=VMAX, legend=True, figsize=(8.5, 8.8),
                  edgecolor="0.6", linewidth=0.015,
                  legend_kwds={"label": "Radical-right vote share (%)",
                               "shrink": 0.5})
    ax.set_axis_off()
    ax.set_title("Municipality-level radical-right vote share")

    out = ROOT / "examples" / "map_radical_right.png"
    plt.savefig(out, dpi=200, bbox_inches="tight")
    print(f"Wrote {out}  ({len(gdf):,} municipalities)")


if __name__ == "__main__":
    main()
