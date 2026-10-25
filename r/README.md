# bluedb (R)

R interface to **BLUE_DB** (Basic Local Units Election Database): local-level
results of national parliamentary and European Parliament elections across
Europe, joinable to Eurostat geodata via GISCO identifiers and to the wider
party-politics literature via Party Facts identifiers.

The package is functionally identical to the [`blue_db` Python
package](../README.md): the same objects, methods, arguments and results,
checked value by value against it. Method calls use `$` instead of `.`.

- **Web interface & maps:** <https://electionatlas.eu>
- **Dataset archive (Zenodo):** <https://doi.org/10.5281/zenodo.21434111>
- **Source repository:** <https://github.com/blue-electoral-bulletin/BLUE_DB>

## Installation

```r
# install.packages("remotes")
remotes::install_github("blue-electoral-bulletin/BLUE_DB", subdir = "r")
```

or, from a checkout, `R CMD INSTALL r`. The only hard dependency is
`jsonlite`. Geometries (`BlueGeo`) also need
[`sf`](https://r-spatial.github.io/sf/), which is suggested rather than
required, because it needs the GDAL/GEOS/PROJ system libraries.

## Getting the data

The dataset is not bundled with the package. Download the archive from Zenodo
and either unpack it into the per-user data directory, which is the same one
the Python package uses (`~/.local/share/blue_db/` on Linux,
`~/Library/Application Support/blue_db/` on macOS, `%LOCALAPPDATA%\blue_db\`
on Windows), or point the package at it:

```r
Sys.setenv(BLUE_DB_HOME = "/path/to/BLUE_DB")   # folder holding dist/ and maps/
bluedb::blue_info()                              # shows what was found where
```

The other options are the same as in Python. `BLUE_DB_DIST` and `BLUE_DB_MAPS`
set the individual folders, `BLUE_DB_CACHE` sets the GISCO download cache, and
`BlueDB("/path/to/dist")` takes an explicit path. Without any of these, the
package looks for a `dist/` folder in the working directory or any parent. The
download cache is shared with the Python package, so tiles fetched by one are
reused by the other.

## Quick start

```r
library(bluedb)

db <- BlueDB()

# Country-level ideological breakdown of the latest national election per country
df <- db$results(election_type = "national", latest_per_country = TRUE,
                 geo_level = "NUTS0", aggregate_by = "ideology")

# Municipality-level results of the 2022 Portuguese election
res <- db$results(countries = "PT", election_type = "national",
                  years = 2022, geo_level = "LAU")
```

`db$results(...)` returns one row per (election, geographic unit) and one
column per party. It aggregates on the fly to any level with `geo_level`
(`"LAU"`, `"NUTS3"`, `"NUTS2"`, `"NUTS1"`, `"NUTS0"`) and collapses parties
with `aggregate_by` (`"ideology"`, `"ep_group"`, `"eu_party"`). There are two
helpers, `db$vote_shares()` and `db$fill_forward()`. The other methods are
`db$elections()` and `db$canonical_elections()`. The reference tables are
fields: `db$index`, `db$parties`, `db$ep_groups`, `db$eu_parties`, `db$nuts`,
`db$laus`, `db$special` and `db$geo_chain`.

### Mapping

```r
db <- BlueDB(); geo <- BlueGeo()
res <- db$results(countries = "PT", election_type = "national", years = 2022)
m <- geo$attach(db$vote_shares(res))        # an sf object in EPSG:3035
plot(m["PS_pct"], border = NA)
```

`geo$attach()` resolves each election at its own boundary vintage. The lower
level calls are `geo$geometries(ids, year, election_type, country_code)`,
`geo$resolve_sources(...)` (which polygon each unit is drawn with, as in
`dist/geo/geo_lookup.csv`), `geo$geometry_of(ref)` and
`geo$all_geometries(year, ...)`. Polygons come from the curated shapefiles in
`maps/` where they exist, and otherwise from GISCO layers that are downloaded
on demand and cached. The licences of both are listed in the [Python package
README](../README.md#where-the-geometries-come-from).

## Command line

The package installs an `Rscript` front end with the same commands as the
Python `blue-db`:

```bash
BLUE_DB=$(Rscript -e 'cat(system.file("exec", "blue-db", package = "bluedb"))')
$BLUE_DB info
$BLUE_DB results -c PT -y 2022 --shares -o pt.csv
$BLUE_DB results --latest --level NUTS0 --by ideology
```

Inside R, call `bluedb::cli_main(c("results", "-c", "PT"))`.

## Jersey and Guernsey

The Jersey and Guernsey results are not under CC BY 4.0 and are not part of the
Zenodo archive. As in Python, `BlueDB()` downloads them from the GitHub
repository the first time they are requested (pinned to the release tag,
cached in `external_dir()`) and shows their publishers' terms once per session.
`BlueDB(external = FALSE)`, or `--no-external` on the command line, leaves them
out.

## Licence and citation

The code is released under the MIT License. The dataset is released under
CC BY 4.0, except for the Jersey and Guernsey results; the sources and their
required attributions are listed in `ATTRIBUTION.md` at the repository root.
Map geometries fetched from Eurostat/GISCO are subject to Eurostat's data
licence. Cite the dataset as
`bluedb::BLUE_DB_CITATION`:

> Hublet, F., & Chiecchio, N. (2026). BLUE_DB — Basic Local Units Election
> Database. Paris: Groupe d'études géopolitiques.
> <https://doi.org/10.5281/zenodo.21434111>
