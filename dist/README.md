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
