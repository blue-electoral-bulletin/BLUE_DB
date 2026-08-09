"""Country-level ideological breakdown of the latest national election.

Run from anywhere:  python examples/01_ideology_by_country.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))            # use the in-repo blue_db package
from blue_db import BlueDB


def main() -> None:
    db = BlueDB(ROOT / "dist")

    # One row per country: its most recent national election, votes collapsed
    # into ideological families and expressed as shares of the valid vote.
    df = db.results(election_type="national", latest_per_country=True,
                    geo_level="NUTS0", aggregate_by="ideology")
    df = db.vote_shares(df)

    pct = [c for c in df.columns if c.endswith("_pct")]
    table = df[["country_code", "year"] + pct].round(1)

    print(table.to_string(index=False))
    out = ROOT / "examples" / "ideology_by_country.csv"
    table.to_csv(out, index=False)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
