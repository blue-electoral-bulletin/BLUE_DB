# blue_db

Python API for **BLUE_DB** (Basic Local Units Election Database): municipality-level
results of 334 national parliamentary and European Parliament elections across 31
European countries, 2001–2025, built on a typology of over 150,000 local
administrative units (LAUs) and ~3,900 parties, coalitions, and candidates.
Results are joinable to Eurostat geodata via GISCO identifiers and to the wider
party-politics literature via Party Facts identifiers.

- **Web interface & maps:** <https://electionatlas.eu>
- **Dataset archive (Zenodo):** <https://doi.org/10.5281/zenodo.21434112>
- **Source repository:** <https://github.com/blue-electoral-bulletin/BLUE_DB>

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

The dataset is **not** bundled with the wheel (`dist/` is ~150 MB and the
curated shapefiles in `maps/` another ~420 MB). Download the archive from
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
`maps/` for special constituencies (IE, IS, MT, SI) and otherwise downloads the
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
rebuilt by `make_maps.py`). These cover units GISCO does not publish —
electoral constituencies, cantons, and territories outside the European layers.
The right-hand column is the redistribution position: *verified* means the
licence was read and permits it; *unverified* means the publisher's terms have
not been confirmed and should be before the next release.

| File | Source / publisher | Redistribution |
|---|---|---|
| `SI.shp` | `VDV_VO` (volilni okraji), Geodetska uprava RS via [stefanb/gurs-rpe](https://github.com/stefanb/gurs-rpe) | **CC BY 4.0** — permitted with attribution |
| `NL.shp` | `bonaire`, `saba`, `sint_eustatius` from [georgique/world-geojson](https://github.com/georgique/world-geojson), OpenStreetMap-derived | Repository **GPL-3.0**; underlying data **ODbL** (© OpenStreetMap contributors) — permitted with attribution and share-alike |
| `FR.shp` | `communes-1000m.geojson`, [Etalab *contours administratifs*](https://www.data.gouv.fr/datasets/contours-administratifs) (IGN ADMIN-EXPRESS + INSEE) | Etalab **Licence Ouverte 2.0** — permitted with attribution |
| `IE_1997/2011.shp` | `GRED_20190215_Ireland_2011`, CLEA GeoReferenced Electoral Districts (Kollman et al. 2019), Univ. of Michigan | No explicit licence |
| `IE_2013/2017/2023.shp`, `EU_IE_*.shp` | Constituency Boundaries / National Statutory Boundaries, [Tailte Éireann](https://www.tailte.ie/) (formerly OSi) | **CC BY 4.0** — permitted with attribution |
| `BE_2003.shp`, `BE_2024.shp` | [Electoral cantons](https://www.geo.be/catalog/details/daca9ccf-a9e1-11ec-a672-186571a04de2), [geo.be](https://www.geo.be/) — Belgian federal geoportal | **CC BY 4.0** — permitted with attribution |
| `EE.shp` | `maakond` (counties), [Maa-amet](https://geoportaal.maaamet.ee/) — Estonian Land Board | "use … is not restricted, but the reference to the data source … and validity date must be made" |
| `IS.shp` | `is_au_lmi_epsg_3057`, Náttúrufræðistofnun Íslands (Landmælingar Íslands) | **CC BY 4.0** — permitted with attribution |
| `MT.shp` | `AdministrativeUnitsAndBoundaries` (INSPIRE GML), [Malta MSDI](https://msdi.data.gov.mt/) | **CC BY-NC-ND** — non-commercial reuse permitted with attribution and share-alike |
| `EL.shp` | GISCO municipal units aggregated via ELSTAT table `A1202_SKA01` ([ELSTAT](https://www.statistics.gr/)) | GISCO terms apply to the geometry; the ELSTAT table is a correspondence list, not geometry |

Two files are reconstructions rather than published boundaries, and are marked
as such in `make_maps.py`: the pre-2007 Irish constituencies, rebuilt from
statutory county boundaries (three of them as a county minus its neighbouring
constituency, which is approximate), and Greece's *dímoi*, aggregated from the
municipal units GISCO publishes.

GRED should be cited as: Kollman, K., Hicken, A., Caramani, D., Backer, D.,
Lublin, D., Selway, J., & Vasselai, F. (2019). *GeoReferenced Electoral
Districts Datasets*. Ann Arbor, MI: Center for Political Studies, University of
Michigan. <https://electiondataarchive.org/>

## Command line

```bash
blue-db info                                        # version, data locations, dataset size
blue-db elections --country FR --type national      # the election index as CSV
blue-db results -c PT -y 2022 --shares -o pt.csv    # results as CSV
blue-db results --latest --level NUTS0 --by ideology
```

## Licence and citation

The code is released under the MIT License. The dataset is released separately
(CC BY 4.0); map geometries fetched from Eurostat/GISCO are subject to
Eurostat's data licence, which requires attribution and restricts commercial
reuse.

> Hublet, F., & Chiecchio, N. (2026). BLUE_DB — Basic Local Units Election
> Database. Paris: Groupe d'études géopolitiques.
> <https://doi.org/10.5281/zenodo.21434112>

BLUE_DB is a project of the *Electoral Bulletins of the European Union* (BLUE),
a journal published by the Groupe d'études géopolitiques.
