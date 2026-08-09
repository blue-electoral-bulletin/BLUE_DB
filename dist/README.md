# BLUE_DB Distribution

**BLUE** (*Electoral Bulletins of the European Union*) is a journal published
by the Groupe d'études géopolitiques. **BLUE_DB** is the associated electoral
database, providing municipality-level results for national legislative
elections and European Parliament elections across European countries.

## Folder structure

```
dist/
  geo/
    laus.csv       – LAU-level geographic units with GISCO IDs, names,
                     NUTS3 parents, and historical name variants.
    nuts.csv       – NUTS regions (levels 0–3) with GISCO IDs, names,
                     parent regions, and historical name variants.
    special.csv    – Aggregate / special electoral units (postal votes,
                     overseas voters, electoral constituencies, …).
    geo_lookup.csv – Crosswalk: the polygon (source layer + feature ID) that
                     draws each unit of each election.
  parties/
    parties.csv    – Party metadata: names, abbreviations, ideology,
                     European party affiliation, Wikipedia/Wikidata links.
    eu_parties.csv – European-level party federations referenced in the
                     dataset (EPP, PES, ALDE, EGP, EL, ECR, …).
    ep_groups.csv  – European Parliament political groups referenced in
                     the dataset (EPP, S&D, RE, ECR, GUE/NGL, …).
  elections/
    AT/
      2002.csv     – Austrian national election, 2002.
      2006.csv
      …
      EU_2004.csv  – Austrian EP election, 2004.
      EU_2009.csv
      …
    BE/
      …            – One sub-folder per country.
```

## Election files

Each `elections/{CC}/{YEAR}.csv` (national) or `elections/{CC}/EU_{YEAR}.csv`
(European Parliament) contains:

| Column | Description |
|---|---|
| `gisco_id` | Geographic unit identifier (links to geo files) |
| `name` | Unit name as in the source |
| `registered` | Registered voters (where available) |
| `turnout` | Votes cast (where available) |
| `invalid` | Invalid votes (where available) |
| `blank` | Blank votes (where available) |
| *party columns* | Vote totals per party |

For countries with multiple separate ballots in the same year
(e.g. Germany: Erststimme / Zweitstimme), files are named
`{YEAR}_1.csv`, `{YEAR}_2.csv`, etc.

## Geographic files

All geo files share the columns: `gisco_id`, `id`, `name_or`, `level`,
`start_date`, `end_date`, `predecessors`, `parent`, `names`.

`nuts.csv` additionally covers all NUTS levels 0–3 in a single file; the
`level` field takes values `NUTS 0`–`NUTS 3` and `parent` points to the
containing NUTS unit one level up.

`special.csv` additionally has a `type` column (e.g. *Postal Vote*,
*Electoral Constituency*, *Abroad Voters*).

### Mapping the data: `geo_lookup.csv`

A `gisco_id` identifies a unit; it does not by itself say which polygon draws
it, and for older elections the two often diverge — boundaries were redrawn,
codes renumbered, whole levels renamed. `geo_lookup.csv` closes that gap with
one row per *election unit*:

| Column | Description |
|---|---|
| `country`, `file`, `year`, `election_type` | The election |
| `gisco_id` | The unit, as in the election file |
| `source_kind` | `lau` (GISCO municipal tile), `nuts` (GISCO NUTS tile), `cntr` (GISCO world countries), `cntrset` (a union of countries), `maps` (a shapefile shipped with the project) |
| `source_layer` | The tile vintage (`2021`), vintage and level (`2021:3`), or file name |
| `source_id` | The identifier of the polygon *inside that layer*; for `cntrset`, the country codes to dissolve, joined by `+` |

`cntr` and `cntrset` cover voters abroad. A unit hosted in one country is drawn
as that country; a constituency spanning several — France's eleven
*circonscriptions des Français de l'étranger*, Portugal's continental groupings,
or a plain "abroad" unit — is drawn as the union of its members, listed in
full in `source_id`. Portugal's "remaining countries" groups are resolved per
election, since what remains depends on which countries were counted separately
that year.

The geometries themselves are not redistributed here: GISCO's boundaries carry
their own licence and are freely downloadable from
[gisco-services.ec.europa.eu](https://gisco-services.ec.europa.eu/distribution/v2/).
The crosswalk is what turns them into a map of this data, and the `blue_maps`
library performs exactly this join.

The same unit may be drawn from different layers in different years — that is
deliberate, and is how boundary changes over time are preserved.

Empty source columns mean the unit has no polygon. A `status` of
`no geometry (postal vote)`, `no geometry (ships at sea)` or
`no geometry (e-voting)` is not a gap: those units have no territory to draw.

## Parties files

`parties.csv` columns: `party_id`, `name_native`, `name_english`, `abbreviation`,
`coalition_members`, `other_names`, `individual_candidate`, `region`,
`founded_year`, `dissolved_year`, `eu_party`, `ep_group`, `colors`,
`wikipedia_page`, `wikidata_id`, `party_facts_id`, `ideology`.
The `eu_party` and `ep_group` fields are JSON-encoded lists; the
`party_id` subfield in each entry links to `eu_parties.csv` and
`ep_groups.csv` respectively.

`eu_parties.csv` columns: `party_id`, `organization_key`, `name`, `abbreviation`,
`founded_year`, `dissolved_year`, `ep_group`, `ideology`, `colors`,
`wikipedia_page`, `wikidata_id`.

`ep_groups.csv` columns: `group_id`, `name`, `abbreviation`, `start_year`,
`end_year`, `eu_party`, `parties_key`, `colors`, `wikipedia_page`,
`wikidata_id`.
