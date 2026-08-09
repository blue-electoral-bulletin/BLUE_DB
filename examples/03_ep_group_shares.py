"""European Parliament group shares at the national level (EP 2019).

Run from anywhere:  python examples/03_ep_group_shares.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))            # use the in-repo blue_db package
from blue_db import BlueDB


def main() -> None:
    db = BlueDB(ROOT / "dist")

    # 2019 EP election, votes collapsed into the European Parliament groups the
    # parties sat in that year, aggregated to the national (NUTS0) level.
    df = db.results(election_type="european", years=2019,
                    geo_level="NUTS0", aggregate_by="ep_group")
    df = db.vote_shares(df)

    pct = [c for c in df.columns if c.endswith("_pct")]
    table = df[["country_code", "year"] + pct].round(1)
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
