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
pip install -e .
```

This pulls in everything the library needs, geometry stack included
(pandas, numpy, geopandas, shapely, matplotlib). Equivalently:
`pip install -r requirements.txt`.

Installing also provides a `blue-db` command:

```bash
blue-db info                                     # version and where the data was found
blue-db results -c PT -y 2022 --shares -o pt.csv # results as CSV
```

Run from this repository the library finds `dist/` and `maps/` automatically.
Elsewhere, set `BLUE_DB_HOME=/path/to/BLUE_DB` (or `BLUE_DB_DIST`), or pass the
path explicitly: `BlueDB("/path/to/dist")`.

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

## Changelog

### v2026.2 -- August 2026

#### Added

- 2026 national elections in Bulgaria (19 Apr), Cyprus (24 May), Denmark (24 Mar), Hungary (12 Apr), Malta (30 May), and Slovenia (22 Mar).
- `geo/geo_lookup.csv`: a crosswalk naming, for every unit of every election,
   the source layer and feature that provides their polygon.
- Start and end dates for alternative party names.	


#### Fixed

- Constituencies that span several countries are now mapped as the union of
  those countries: France's eleven "circonscriptions des Français établis hors
  de France", Portugal's continental emigrant groupings, and the single
  abroad-voter units of Greece, Poland, Latvia, Estonia, Italy, Croatia, Hungary,
  Slovakia, Switzerland and Romania.
- Portugal: 38 voter-abroad units of the 2009 and 2014 EP elections were
  pooled under a catch-all code due to an encoding error. They now carry
  their own country codes.
- Romania: fixed abroad units written without their country prefix and
  missing dates of EP elections.
- Portugal 2024–2025, three Danish EP elections: added missing `name`
  column.
- Bulgaria 2017: the *Не подкрепям никого* ("none of the above") votes were
  missing. They are now correctly reported as `blank`.
- Bulgaria, 2014 EP: invalid votes were undercounted by 39,617 and
  three separate *Инициативен комитет* lists shared one column. Both issues were fixed.
- Corrected two Bulgarian candidate names incorrectly transliterated.
- Regenerated main party color from Wikidata's P465 whenever available.

## Citation

If you use BLUE_DB, please cite:

> Hublet, F., & Chiecchio, N. (2026). BLUE_DB — Basic Local Units Election Database. Paris: Groupe d'études géopolitiques. https://doi.org/10.5281/zenodo.21434112

BLUE_DB is a project of the *Electoral Bulletins of the European Union* (BLUE),
a journal published by the Groupe d'études géopolitiques.
