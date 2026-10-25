# BLUE_DB

**BLUE_DB** (Basic Local Units Election Database) is a local-level election
database of national and European Parliament elections across Europe, built
on a typology of local administrative units (LAUs) and of parties,
coalitions, and candidates. Results are joinable to Eurostat geodata via GISCO identifiers and
to the wider party-politics literature via Party Facts identifiers.

- **Web interface & maps:** https://electionatlas.eu
- **Dataset archive (Zenodo):** https://doi.org/10.5281/zenodo.21434111
- **Source repository:** https://github.com/blue-electoral-bulletin/BLUE_DB

This repository holds the **Python library** and the **evaluation,
distribution, and codebook-generation scripts**, bundled with the dataset
(`dist/`) so everything runs out of the box. The dataset is also archived on
Zenodo, except for the Jersey and Guernsey results (see *Licensing* below), and
an interactive map is available at the web interface above.

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

With the Zenodo copy of the data, the Jersey and Guernsey results are not on
disk: the library downloads them from this repository the first time they are
requested (pinned to the release tag, cached locally) and prints their
publishers' terms. `BlueDB(external=False)` leaves them out altogether.

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
`maps/` for constituencies and territories GISCO does not publish and otherwise downloads the
matching GISCO LAU/NUTS vintage on demand (cached under `.cache/`).

## Repository layout

| Path | Contents |
|---|---|
| `blue_db/` | The Python library (installable package). |
| `r/` | The R package `bluedb`, functionally identical (`remotes::install_github(..., subdir = "r")`). |
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

## Licensing

- **Code** in this repository is under the MIT License (`LICENSE`).
- The **dataset** (`dist/`) is released under CC BY 4.0. Credit it as:

  > Data: BLUE_DB (Hublet & Chiecchio 2026), https://doi.org/10.5281/zenodo.21434111, CC BY 4.0, modified from official sources listed in ATTRIBUTION.md. Contains Parliamentary information licensed under the Open Parliament Licence v3.0 and public sector information licensed under the Open Government Licence.

  `ATTRIBUTION.md` lists every source and its required attribution;
  `SOURCE_LICENCES.md` records, for every country, the source used and what
  its publisher says about reuse.
- The **Jersey and Guernsey results** (`dist/elections/JE/`,
  `dist/elections/GG/` and their raw files in `output/`) are covered by
  neither licence. They are © States of Jersey and © States of Guernsey and
  reusable for research and non-commercial purposes only; see the `NOTICE.md`
  in each folder.
- **Map geometries** fetched from Eurostat/GISCO are subject to Eurostat's data
  licence (attribution required, commercial reuse restricted), and the
  shapefiles in `maps/` keep their sources' licences (`maps/README.md`).

## Changelog

### v2026.2 -- October 2026

#### Added

- 19 countries outside the EU and EFTA, 112 national elections in total:
  Albania, Andorra, Armenia, Bosnia and Herzegovina, the Faroe Islands,
  Georgia, Gibraltar, Greenland, Guernsey, the Isle of Man, Jersey, Kosovo,
  Moldova, Monaco, Montenegro, North Macedonia, San Marino, Serbia and Türkiye.
  Their reporting units are listed in a new registry alongside the LAU, with
  boundaries from GISCO, geoBoundaries and OpenStreetMap, and are placed in
  the NUTS 2024 regions where Eurostat defines them (AL, ME, MK, RS, TR, XK),
  in a pseudo-NUTS hierarchy of entities and cantons for Bosnia and
  Herzegovina and of marzer / regions for Armenia and Georgia, or in a single
  country-wide region otherwise.  Unit names are in each country's own
  script and spelling.
- The United Kingdom: the seven House of Commons elections 2001–2024 by
  Westminster constituency, with ONS boundaries for each boundary set and the
  NUTS 2021 regions.
- Ukraine: the 2012, 2014 and 2019 party-list votes by hromada, placed from
  the CVK's polling-station results, and 2006 and 2007 by territorial
  constituency, with outlines rebuilt from the pre-2020 raions; a pseudo-NUTS
  hierarchy of oblasts and 2020 raions.  The 2002 election is not included.
- Denmark: the Folketing results of the Faroe Islands (2001–2026) and
  Greenland (2007–2026) are part of the Danish election files, by Faroese and
  Greenlandic municipality.
- About 1,230 parties of these countries in `parties.csv`.
- An R package, `bluedb` (in `r/`), with the same functionality as the
  Python library: `BlueDB()` and `BlueGeo()` with the same methods and
  arguments, the same command line, and results identical value for value.

#### Changed

- The Jersey and Guernsey results are no longer part of the Zenodo record:
  their publishers restrict reuse to research and non-commercial purposes,
  which the record's CC BY 4.0 licence does not allow. They stay in the
  GitHub repository with their publishers' notices, and the Python and R
  libraries download them on demand (`external=False` leaves them out).
  `elections/index.csv` gains `distribution` and `licence` columns.
- `ATTRIBUTION.md` lists every source and the attribution it requires, with
  a one-line credit for users of the dataset.
- Irish constituencies of the 2002–2011 elections: drawn from the CSO's
  Census 2011 boundary files (CC BY 4.0) instead of CLEA GRED, which carries
  no licence. Same constituencies, polygons within 0.2% of the old ones.
- Malta's electoral districts are no longer shipped: their source (MSDI,
  CC BY-NC-ND) does not allow a derivative to be shared. The original
  localities are in `maps/sources/MT/`, and the libraries dissolve the
  districts from them on first use; the viewer merges them in the browser.

#### Fixed

- Python library: national and European elections held on the same day
  (Belgium 2014–2024, Bulgaria 2024, Luxembourg 2004 and 2009) were merged
  into one when no election type was given, so one of them was silently
  dropped.  Andorra's national list is now preferred over its parish seats.
- Python library: 191 Croatian municipalities (920,000 voters) were left out
  of NUTS 0–2 aggregates, because one region's parent is recorded under a
  versioned id.
- Python library: `BlueGeo.attach` now draws each election at its own
  boundary vintage when a table mixes vintages, and
  `all_geometries(election_type="european")` no longer returns a country's
  constituencies once for each earlier boundary file.
- The source and licence of every shipped boundary file are documented, and
  the viewer credits OpenStreetMap, geoBoundaries, the ONS and OCHA.
  `SOURCE_LICENCES.md` records, for each country, the source of the results
  and what its publisher says about reusing them.
- Romania, 2009 EP: every party's votes were under another party's name,
  because the official workbook's headers are misaligned (PSD+PC's 31.1%
  appeared as UDMR's). The columns are now matched to the official totals.
  Elena Băsescu is reported as the independent candidate she was, rather
  than as the PD-L.
- Malta's electoral districts and Iceland's constituencies now have their
  NUTS 3 parents (Malta / Gozo; capital region / rest), so Malta appears in
  NUTS aggregates and Reykjavík is no longer counted outside the capital
  region.
- Albania, North Macedonia, Serbia and Greece's *dímoi* are drawn from the
  GISCO layers on demand, like the EU countries; the copies of GISCO
  boundaries are no longer shipped in `maps/`.
- `laus.csv` now also lists the reporting units of the new countries.
- Ideologies of four parties (BG БНО, CY Δ.Ε.Κ., HR HDZ-HSP-BUZ, IE IHA)
  corrected.
- Sweden 2022: Moderaterna was missing.
- Lithuania: in 2016 the party columns held the postal and early votes only
  (polling-station votes were lost); in 2020 and 2024 polling districts with
  a supplementary protocol kept only one of the two.  All three now match
  the official results.

### v2026.1 -- August 2026

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

> Hublet, F., & Chiecchio, N. (2026). BLUE_DB — Basic Local Units Election Database. Paris: Groupe d'études géopolitiques. https://doi.org/10.5281/zenodo.21434111

BLUE_DB is a project of the *Electoral Bulletins of the European Union* (BLUE),
a journal published by the Groupe d'études géopolitiques.
