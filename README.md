# BLUE_DB

**BLUE_DB** (Basic Local Units Election Database) is a local-level election
database covering 334 national and European Parliament elections across 31
European countries between 2001 and 2025, built on a typology of over 150,000
local administrative units (LAUs) and ~3,900 parties, coalitions, and
candidates. Results are joinable to Eurostat geodata via GISCO identifiers and
to the wider party-politics literature via Party Facts identifiers.

- **Web interface & maps:** https://electionatlas.eu
- **Dataset archive (Zenodo):** https://doi.org/10.5281/zenodo.21434112
- **Source repository:** https://github.com/blue-electoral-bulletin/BLUE_DB

This repository holds the **Python library** and the **evaluation,
distribution, and codebook-generation scripts**, bundled with the dataset
(`dist/`) so everything runs out of the box. The full dataset is also archived
on Zenodo, and an interactive map is available at the web interface above.

## Installation

```bash
pip install -e .            # library only (pandas, numpy)
pip install -e ".[maps]"    # + geometry resolution & plotting (geopandas, matplotlib, ...)
```

or simply `pip install -r requirements.txt`.

## Quick start

```python
from blue_db import BlueDB

db = BlueDB()   # reads ./dist

# Country-level ideological breakdown of the latest national election per country
df = db.results(election_type="national", latest_per_country=True,
                geo_level="NUTS0", aggregate_by="ideology")

# Municipality-level results of the 2022 Portuguese election
res = db.results(countries="PT", election_type="national",
                 years=2022, geo_level="LAU")
```

`db.results(...)` returns one row per (election, geographic unit) and one column
per party. Aggregate on the fly to any level with `geo_level` (`LAU`, `NUTS3`,
`NUTS2`, `NUTS1`, `NUTS0`) and collapse parties with `aggregate_by`
(`ideology`, `ep_group`, `eu_party`). Helpers: `vote_shares()`, `fill_forward()`.

### Mapping

```python
from blue_db import BlueDB, BlueGeo

db, geo = BlueDB(), BlueGeo()
res = db.results(countries="PT", election_type="national", years=2022, geo_level="LAU")
gdf = geo.geometries(res["geo_id"], year=2022, election_type="national", country_code="PT")
gdf.merge(res, left_on="gisco_id", right_on="geo_id").plot(column="PS")
```

`BlueGeo` returns polygons in EPSG:3035; it uses the curated shapefiles in
`maps/` for special constituencies (IE, IS, MT, SI) and otherwise downloads the
matching GISCO LAU/NUTS vintage on demand (cached under `.cache/`).

## Repository layout

| Path | Contents |
|---|---|
| `blue_db/` | The Python library (installable package). |
| `dist/` | The released dataset: `geo/`, `parties/`, `elections/`. |
| `examples/` | Runnable usage examples. |
| `output/` | Per-country raw result files (input to `build_distribution.py`). |
| `resources/` | Reference files the scripts read (typologies, EU-NED). |
| `maps/` | Curated special-constituency shapefiles. |
| `codebook/templates/` | Editable codebook fragments. |

## Scripts

All scripts are run from the repository root.

- **`examples/*.py`** — start here; each is self-contained.
- **`make_summary_tables.py`** — per-country coverage table from `dist/`.
- **`eval_ned.py`** — cross-validate against EU-NED; writes
  `eval_ned_results.csv/.xlsx` and a summary. Needs the `[eval]` extra.
- **`build_distribution.py`** — rebuild `dist/` from `output/` + `resources/`.
- **`build_codebook.py`** — regenerate the codebook PDF (requires a `xelatex`
  installation).

```bash
python examples/01_ideology_by_country.py
python make_summary_tables.py
python eval_ned.py
```

## Data provenance & licensing

Electoral results were collected from official national sources. The geographic
layer is aligned on Eurostat's LAU/NUTS typology. **Code** in this repository is
under the MIT License (`LICENSE`). **Map geometries** fetched from Eurostat/GISCO
are subject to Eurostat's data licence (attribution required, commercial reuse
restricted).

## Citation

If you use BLUE_DB, please cite:

> Hublet, F., & Chiecchio, N. (2026). BLUE_DB — Basic Local Units Election Database. Paris: Groupe d'études géopolitiques. https://doi.org/10.5281/zenodo.21434112

BLUE_DB is a project of the *Electoral Bulletins of the European Union* (BLUE),
a journal published by the Groupe d'études géopolitiques.
