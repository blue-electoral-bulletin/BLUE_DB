# blue_db

Python API for **BLUE_DB** (Basic Local Units Election Database): municipality-level
results of national parliamentary and European Parliament elections across
Europe, built on a typology of local administrative units (LAUs) and of
parties, coalitions, and candidates. `blue-db info` reports the size of the
release you have installed.
Results are joinable to Eurostat geodata via GISCO identifiers and to the wider
party-politics literature via Party Facts identifiers.

- **Web interface & maps:** <https://electionatlas.eu>
- **Dataset archive (Zenodo):** <https://doi.org/10.5281/zenodo.21434111>
- **Source repository:** <https://github.com/blue-electoral-bulletin/BLUE_DB>
- **R package:** `bluedb`, with the same functionality, in [`r/`](../r/README.md)

## Installation

```bash
pip install blue_db
```

This installs everything the library needs, including the geometry stack
(`geopandas`, `shapely`, `requests`, `matplotlib`) used by `BlueGeo`.

From a checkout of the repository:

```bash
pip install -e ".[dev]"
```

## Getting the data

The dataset is **not** bundled with the wheel: the distribution (`dist/`) and
the curated shapefiles (`maps/`) are far too large for it. Download the archive from
Zenodo and either unpack it into the per-user data directory
(`~/.local/share/blue_db/` on Linux, `~/Library/Application Support/blue_db/`
on macOS, `%LOCALAPPDATA%\blue_db\` on Windows) or point the library at it:

```bash
export BLUE_DB_HOME=/path/to/BLUE_DB     # folder holding dist/ and maps/
blue-db info                             # shows what was found where
```

Alternatives: `BLUE_DB_DIST` / `BLUE_DB_MAPS` for the individual folders,
`BLUE_DB_CACHE` for the GISCO download cache, or an explicit path in code —
`BlueDB("/path/to/dist")`. Without any of these, the library also finds a
`dist/` folder in the working directory (or any parent), and in the repository
checkout it was installed from.

### Jersey and Guernsey

The Jersey and Guernsey results are not under CC BY 4.0: their publishers
restrict reuse to research and non-commercial purposes, so they are not part
of the Zenodo archive. They are kept in the GitHub repository, and the library
downloads them from there the first time they are requested, pinned to the
release tag and cached next to the GISCO tiles (`blue-db info` shows where).
A `BlueExternalDataNotice` warning gives the publisher's terms once per
session. `elections()` reports each file's `distribution` (`included` or
`external`) and `licence`. To stay strictly within CC BY 4.0, leave them out:

```python
db = BlueDB(external=False)        # or: blue-db --no-external results ...
```

## Quick start

```python
from blue_db import BlueDB

db = BlueDB()

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

`BlueGeo` is imported lazily, so the cost of loading geopandas is paid only
when you ask for geometries. It returns polygons in EPSG:3035 and uses the curated shapefiles in
`maps/` for constituencies and territories GISCO does not publish (listed below) and otherwise downloads the
matching GISCO LAU/NUTS vintage on demand, cached on disk.

### Where the geometries come from

Two different things happen here, and they carry different obligations.
GISCO's layers are **fetched at run time** and never redistributed. The files
under `maps/` and `maps/input/`, by contrast, **are** shipped with the
repository, so their licences have to permit redistribution. What is
established is recorded below; `dist/geo/geo_lookup.csv` records, for each unit
of each election, which layer and which feature draws it.

**Downloaded on demand (GISCO, © European Union).** Reuse is permitted with
attribution to Eurostat/GISCO; boundaries are for illustration and imply no
recognition of frontiers.

| Layer | Used for | Vintages |
|---|---|---|
| [LAU](https://gisco-services.ec.europa.eu/distribution/v2/lau/) | municipalities from 2011 | 2011–2024 |
| [Communes](https://gisco-services.ec.europa.eu/distribution/v2/communes/) | municipalities before 2011 | 2001, 2004, 2006, 2008, 2010 |
| [NUTS](https://gisco-services.ec.europa.eu/distribution/v2/nuts/) | regions, and units reported above municipal level | 2003–2024 |
| [Countries](https://gisco-services.ec.europa.eu/distribution/v2/countries/) | voters abroad, drawn as their host country | 2001–2024 |

**Shipped in the repository** (`maps/input/` as obtained, `maps/*.shp` as
rebuilt by `make_maps.py`, `extra_geo.py`, `build_extra_crosswalk_osm.py` and
`ua_constituencies.py`). These cover units GISCO does not publish: electoral
constituencies, cantons, and the countries and territories outside the
European layers. The right-hand column is the redistribution position.
*Verified* means the licence was read and permits it. *Unverified* means the
publisher's terms have not been confirmed and should be before the next
release. For geoBoundaries layers the source and licence are those
geoBoundaries records for that country and level, because it republishes
other datasets under their own terms.

*EU and EFTA*

| File | Source / publisher | Redistribution |
|---|---|---|
| `SI.shp` | `VDV_VO` (volilni okraji), Geodetska uprava RS via [stefanb/gurs-rpe](https://github.com/stefanb/gurs-rpe) | **CC BY 4.0**, permitted with attribution |
| `NL.shp` | `bonaire`, `saba`, `sint_eustatius` from [georgique/world-geojson](https://github.com/georgique/world-geojson), OpenStreetMap-derived | Repository **GPL-3.0**; underlying data **ODbL** (© OpenStreetMap contributors), permitted with attribution and share-alike |
| `FR.shp` | `communes-1000m.geojson`, [Etalab *contours administratifs*](https://www.data.gouv.fr/datasets/contours-administratifs) (IGN ADMIN-EXPRESS + INSEE) | Etalab **Licence Ouverte 2.0**, permitted with attribution |
| `IE_1997/2011.shp` | 2009-Act constituencies (`Constituency_Boundaries_SAPS2011`), [CSO Census 2011 boundary files](https://www.cso.ie/en/census/census2011boundaryfiles/), Central Statistics Office; the pre-2007 constituencies in `IE_1997.shp` rebuilt from Tailte Éireann county boundaries | **CC BY 4.0**, permitted with attribution |
| `IE_2013/2017/2023.shp`, `EU_IE_*.shp` | Constituency Boundaries / National Statutory Boundaries, [Tailte Éireann](https://www.tailte.ie/) (formerly OSi) | **CC BY 4.0**, permitted with attribution |
| `BE_2003.shp`, `BE_2024.shp` | [Electoral cantons](https://www.geo.be/catalog/details/daca9ccf-a9e1-11ec-a672-186571a04de2), [geo.be](https://www.geo.be/), the Belgian federal geoportal | **CC BY 4.0**, permitted with attribution |
| `EE.shp` | `maakond` (counties), [Maa-amet](https://geoportaal.maaamet.ee/), the Estonian Land Board | "use … is not restricted, but the reference to the data source … and validity date must be made" |
| `IS.shp` | `is_au_lmi_epsg_3057`, Náttúrufræðistofnun Íslands (Landmælingar Íslands) | **CC BY 4.0**, permitted with attribution |
| `MT.shp` (built on your machine) | `AdministrativeUnitsAndBoundaries` (INSPIRE GML), [Malta MSDI](https://msdi.data.gov.mt/), shipped unmodified in `maps/sources/MT/` | Recorded as **CC BY-NC-ND**: non-commercial reuse with attribution, no derivatives. The 13 electoral districts are a derivative, so they are not shipped: `BlueGeo` dissolves them from the original file the first time Malta is drawn and caches them |

*Other countries and territories*

| File | Source / publisher | Redistribution |
|---|---|---|
| `UK_1997/2005/2010/2024.shp` | Westminster Parliamentary Constituencies, [ONS Open Geography Portal](https://geoportal.statistics.gov.uk/): `PCON_DEC_2001_GB_BFE`, `PCON_DEC_2005_GB_BFE`, December 2017 and July 2024 UK BGC | **Open Government Licence v3.0**, permitted with attribution ("Source: Office for National Statistics licensed under the Open Government Licence v.3.0. Contains OS data © Crown copyright and database right") |
| `UA.shp`, `UA_2012.shp` | Hromadas (`ukr_admin3`), [Ukraine Subnational Administrative Boundaries](https://data.humdata.org/dataset/cod-ab-ukr), OCHA FISS on HDX (source: SSPE "Kartographia"), COD-AB v05 | **CC BY-IGO**, permitted with attribution |
| `UA_2006.shp` | Reconstructed 2006/2007 territorial constituencies: the hromadas above, split by the 2012 CVK polling-station addresses ([cvk.gov.ua](https://cvk.gov.ua/)), with Kyiv and other city districts from OpenStreetMap | **CC BY-IGO** and **ODbL** (© OpenStreetMap contributors), permitted with attribution and share-alike |
| `AD.shp`, `BA.shp` | [geoBoundaries](https://www.geoboundaries.org/) ADM1 / ADM3 (source: Wikimedia Commons) | **Public domain** |
| `GE.shp` | geoBoundaries ADM2 (source: Wikimedia Commons); Batumi, Kutaisi, Poti and Rustavi cut in from OpenStreetMap (admin_level 6) | **Public domain** and **ODbL** (© OpenStreetMap contributors), permitted with attribution and share-alike |
| `MD.shp` | geoBoundaries ADM1 (source: UNHCR, OCHA FISS) | **CC BY 3.0 IGO**, permitted with attribution |
| `ME.shp`, `SM.shp`, `MC.shp` | geoBoundaries ADM1 / ADM1 / ADM0 (source: OpenStreetMap, Wambacher) | **ODbL 1.0**, permitted with attribution and share-alike |
| `XK.shp`, `TR.shp` | geoBoundaries ADM2 / ADM1 (source: OpenStreetMap, Wambacher / OpenStreetMap) | Recorded by geoBoundaries as **CC BY-SA 2.0** (the data are OpenStreetMap-derived, so ODbL attribution applies as well), permitted with attribution and share-alike |
| `GL.shp` | geoBoundaries ADM1 (source: Wikipedia) | **CC BY-SA 3.0**, permitted with attribution and share-alike |
| `GI.shp` | geoBoundaries ADM0 (Sentinel-2 10 m land cover 2021, processed by IMB) | **CC BY 4.0**, permitted with attribution |
| `AM.shp` | Communities after the 2021 reform, OpenStreetMap (admin_level 5) | **ODbL** (© OpenStreetMap contributors), permitted with attribution and share-alike |
| `FO.shp`, `GG.shp`, `IM.shp`, `JE.shp` | Kommunur (admin_level 7), parishes and local authorities (admin_level 8) and, for Jersey, vingtaines (admin_level 9), OpenStreetMap | **ODbL** (© OpenStreetMap contributors), permitted with attribution and share-alike |

The OpenStreetMap-derived files are made available under the Open Database
License (ODbL 1.0); `maps/README.md` lists them and carries the licence
notice. Albania, North Macedonia and Serbia are drawn from the GISCO LAU layer
on demand, like the EU countries. So are Greece's *dímoi*, as unions of the
communities GISCO publishes: `resources/geo_overrides.csv` lists each dímos's
communities after the ELSTAT table `A1202_SKA01`
([ELSTAT](https://www.statistics.gr/)), and the polygons are dissolved at run
time.

Cite geoBoundaries as: Runfola, D. et al. (2020). *geoBoundaries: A global
database of political administrative boundaries*. PLoS ONE 15(4): e0231866.

Several files are reconstructions rather than published boundaries, and are
marked as such in the scripts that build them and in the codebook:

- The pre-2007 Irish constituencies are rebuilt from statutory county
  boundaries, three of them as a county minus its neighbouring constituency,
  which is approximate.
- Greece's *dímoi* are dissolved from the communities GISCO publishes.
- Ukraine's 2006/2007 territorial constituencies are rebuilt from polling-station
  addresses (`UA_2006.shp`).
- Northern Ireland's 2001 and 2005 seats are drawn with their 2010 boundaries.
- Jersey's split-parish districts are drawn as unions of vingtaines.

The Irish constituencies used for the 2002, 2007 and 2011 elections are from the Central Statistics Office's
Census 2011 boundary files (© CSO, CC BY 4.0).

## Command line

```bash
blue-db info                                        # version, data locations, dataset size
blue-db elections --country FR --type national      # the election index as CSV
blue-db results -c PT -y 2022 --shares -o pt.csv    # results as CSV
blue-db results --latest --level NUTS0 --by ideology
```

## Licence and citation

The code is released under the MIT License. The dataset is released separately
(CC BY 4.0), except for the Jersey and Guernsey results (see above); the
sources and their required attributions are listed in
[`ATTRIBUTION.md`](../ATTRIBUTION.md). Map geometries fetched from
Eurostat/GISCO are subject to Eurostat's data licence, which requires
attribution and restricts commercial reuse. What each country's original
publisher says about reusing its election results is recorded in
[`SOURCE_LICENCES.md`](../SOURCE_LICENCES.md).

> Hublet, F., & Chiecchio, N. (2026). BLUE_DB — Basic Local Units Election
> Database. Paris: Groupe d'études géopolitiques.
> <https://doi.org/10.5281/zenodo.21434111>

BLUE_DB is a project of the *Electoral Bulletins of the European Union* (BLUE),
a journal published by the Groupe d'études géopolitiques.
