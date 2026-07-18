"""
build_distribution.py
=====================
Assembles the BLUE_DB distribution folder (dist/).

Layout produced
---------------
dist/
  README.md
  geo/
    communes.csv        – all LAU communes (from resources/communes.csv)
    special.csv         – all special / aggregate units (from resources/special_raw.csv)
  parties/
    parties.csv         – party metadata (from parties_final.csv)
  elections/
    AT/
      2002.csv          – Austrian national election 2002
      2006.csv
      …
      EU_2004.csv       – Austrian EP election 2004
      EU_2009.csv
      …
    BE/
      …
    …

Each elections/{CC}/{YEAR}.csv (national) or elections/{CC}/EU_{YEAR}.csv (EP)
has the columns:
    gisco_id, name, <party1>, <party2>, …

For countries with multiple rounds/ballots in the same election year the
filenames are {YEAR}_1.csv, {YEAR}_2.csv, … / EU_{YEAR}_1.csv, …
"""

from __future__ import annotations

import json
import os
import re
import shutil
import warnings
from pathlib import Path
from typing import NamedTuple

import pandas as pd

# ── paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
RESOURCES = ROOT / "resources"
DIST = ROOT / "dist"

COMMUNES_SRC = RESOURCES / "communes.csv"
SPECIAL_SRC = RESOURCES / "special_raw.csv"
PARTIES_SRC = ROOT / "parties_final.csv"
EU_PARTIES_SRC = RESOURCES / "eu_parties.csv"
EP_GROUPS_SRC = RESOURCES / "ep_groups.csv"
NUTS_SRCS = [RESOURCES / f"nuts_{i}.csv" for i in range(4)]
DATA_FILES_REF_SRC = ROOT / "data_files_reference.csv"
MISSING_PARTIES_FN = "missing_parties.csv"

# Regex for data filenames: 2024.csv  2024_1.csv  2024_2.csv
_YEAR_RE = re.compile(r"^(\d{4})(?:_(\d+))?\.csv$")

# Columns in election files that are NOT party vote columns
_META_COLS = {"gisco_id", "name", "registered", "turnout", "invalid", "blank"}

# Legacy / non-standard abroad host codes -> project convention, applied to the
# ZZ-host segment of a gisco_id so election files match the geographic typology
# (mirrors resources normalisation in collect_special.py). E.g. a CZ diaspora
# unit coded CZ_CZZZKO (Kosovo) becomes CZ_CZZZXK.
_HOST_CODE_REMAP = {"ZZGR": "ZZEL", "ZZKO": "ZZXK"}


def _normalize_gisco_id(gid: str) -> str:
    for bad, good in _HOST_CODE_REMAP.items():
        gid = gid.replace(bad, good)
    return gid


# ── party-ID helpers ───────────────────────────────────────────────────────────

class PartyMap(NamedTuple):
    """All party-ID data derived from parties_final.csv."""
    # index (int) → party_id string
    id_by_index: dict[int, str]
    # (relative_file_path, column_name_in_file) → party_id
    alias_lookup: dict[tuple[str, str], str]


def _make_party_id(abbreviation: str, name_native: str,
                   founded_year: str | float, n_same_abbrev: int) -> str:
    """Return a unique string key for a party.

    Priority:
      1. abbreviation              – if it is unique within the country
      2. abbreviation (year)       – if founded_year is available
      3. abbreviation [name_native]
    """
    abbrev = str(abbreviation).strip() if pd.notna(abbreviation) else "?"
    if n_same_abbrev == 1:
        return abbrev
    try:
        year = int(float(founded_year))
        return f"{abbrev} ({year})"
    except (ValueError, TypeError):
        pass
    native = str(name_native).strip() if pd.notna(name_native) else ""
    return f"{abbrev} [{native}]" if native else abbrev


def build_party_map(parties_path: Path) -> tuple[pd.DataFrame, PartyMap]:
    """Load parties_final.csv, compute a unique *party_id* for every row,
    and build the alias-lookup table.

    Returns the DataFrame (with a new ``party_id`` column) and a PartyMap.
    """
    df = pd.read_csv(parties_path, low_memory=False, na_values=[''], keep_default_na=False)

    # Count how many parties share the same (region, abbreviation) pair
    abbrev_counts: dict[tuple[str, str], int] = (
        df.groupby(["region", "abbreviation"], dropna=False)
        .size()
        .to_dict()
    )

    party_ids: dict[int, str] = {}
    for idx, row in df.iterrows():
        region = str(row.get("region", "")).strip()
        abbrev = str(row.get("abbreviation", "")).strip()
        n = abbrev_counts.get((region, abbrev), 1)
        pid = _make_party_id(
            row.get("abbreviation"),
            row.get("name_native"),
            row.get("founded_year"),
            n,
        )
        party_ids[int(idx)] = pid  # type: ignore[arg-type]

    df["party_id"] = [party_ids[i] for i in range(len(df))]

    # Build alias lookup: (relative_file, column_name) → party_id
    alias_lookup: dict[tuple[str, str], str] = {}
    for idx, row in df.iterrows():
        pid = party_ids[int(idx)]  # type: ignore[arg-type]
        raw = row.get("aliases_in_files", "[]")
        try:
            if pd.isna(raw):
                raw = "[]"
        except Exception:
            pass
        try:
            aliases = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            aliases = []
        for entry in aliases:
            file_str = str(entry.get("file", ""))
            # Strip leading "blue_db/" to get the relative path used as key
            rel = file_str.removeprefix("blue_db/")
            col = str(entry.get("party_name", ""))
            if rel and col:
                alias_lookup[(rel, col)] = pid

    return df, PartyMap(id_by_index=party_ids, alias_lookup=alias_lookup)


# ── geo-name helpers ──────────────────────────────────────────────────────────

def _name_for_year(name_or: str, names_json: str | float, year: int) -> str:
    """Return the canonical name of a geographic unit valid in *year*.

    The *names* column holds a JSON list like::

        [{"start_date": "", "end_date": "2011-01-01", "name": "OldName"}, …]

    A record applies when *year* is in [start_date, end_date).  An empty
    start / end means open-ended on that side.  If no historical record
    matches, ``name_or`` (the current canonical name) is returned.
    """
    try:
        if pd.isna(names_json):
            return name_or
    except Exception:
        pass
    if not isinstance(names_json, str) or not names_json.strip():
        return name_or
    try:
        records = json.loads(names_json)
    except Exception:
        return name_or
    for rec in records:
        start = rec.get("start_date") or ""
        end   = rec.get("end_date")   or ""
        after_start  = (not start) or year >= int(start[:4])
        before_end   = (not end)   or year <  int(end[:4])
        if after_start and before_end:
            return rec.get("name") or name_or
    return name_or


def build_geo_name_map(
    communes_path: Path,
    special_path: Path,
) -> dict[tuple[str, int], str]:
    """Build a mapping  (gisco_id, year) → canonical name.

    For each (gisco_id, year) pair the name valid in that year is returned,
    falling back to the current ``name_or`` when no historical variant applies.
    The map covers all gisco_ids found in the communes and special files.
    """
    frames = []
    for path in (communes_path, special_path):
        if path.exists():
            frames.append(pd.read_csv(path, low_memory=False, na_values=[''], keep_default_na=False))
    if not frames:
        return {}

    geo = pd.concat(frames, ignore_index=True)
    # Build a plain gisco_id → (name_or, names_json) dict first
    id_to_row: dict[str, tuple[str, str]] = {}
    for _, row in geo.iterrows():
        gid = str(row["gisco_id"]).strip()
        if gid:
            id_to_row[gid] = (str(row["name_or"]), row.get("names", ""))

    return id_to_row  # actual year look-up is done lazily per election file


# Countries that have EU election folders named EU_{CC}
_EU_COUNTRIES = {
    p.name[3:]
    for p in OUTPUT.iterdir()
    if p.is_dir() and p.name.startswith("EU_")
}

# All domestic countries (folder names without 'EU_' prefix)
_DOM_COUNTRIES = {
    p.name
    for p in OUTPUT.iterdir()
    if p.is_dir() and not p.name.startswith("EU_")
}

# All countries that appear in either domestic or EU
ALL_COUNTRIES = _DOM_COUNTRIES | _EU_COUNTRIES


def _copy_election_files(
    src_folder: Path,
    dest_folder: Path,
    alias_lookup: dict[tuple[str, str], str],
    geo_name_map: dict[str, tuple[str, str]],
    rel_prefix: str,
    file_meta: dict[str, dict],
) -> int:
    """Copy per-election CSVs, renaming party columns and applying the new
    filename convention {year}[_{month}][_{round}][_{vote_type}].csv.

    Only files present in *file_meta* (i.e. main results files) are copied;
    _special.csv, _errors.csv and other variants are skipped automatically.
    *rel_prefix* is the source folder name used as the alias-lookup key prefix
    (e.g. ``"AT"`` or ``"EU_AT"``).
    Returns number of files copied.
    """
    dest_folder.mkdir(parents=True, exist_ok=True)
    is_eu = rel_prefix.startswith("EU_")
    missing_party_map: list[pd.Series] = []
    count = 0
    for csv_path in sorted(src_folder.glob("*.csv")):
        old_key = f"output/{rel_prefix}/{csv_path.name}"
        meta = file_meta.get(old_key)
        if meta is None:
            continue  # not a main results file

        stem = csv_path.stem
        year_m = re.match(r"^(\d{4})", stem)
        year = int(year_m.group(1)) if year_m else 0
        dest_name = _build_dist_filename(stem, meta, is_eu)

        rel_file = f"{rel_prefix}/{csv_path.name}"  # alias-lookup key

        try:
            election_df = pd.read_csv(csv_path, low_memory=False, dtype={"gisco_id": str}, na_values=[''], keep_default_na=False)
        except Exception as exc:
            warnings.warn(f"Could not read {csv_path}: {exc}")
            continue

        # ── normalise abroad host codes so they match the geo typology ─────
        if "gisco_id" in election_df.columns:
            orig = election_df["gisco_id"].astype(str)
            norm = orig.map(_normalize_gisco_id)
            changed = orig != norm
            if changed.any():
                election_df["gisco_id"] = norm
                # Warn only if a *remapped* code now collides with another row
                # (pre-existing duplicates, e.g. German city-states, are left
                # untouched and not flagged here).
                dup = election_df["gisco_id"].duplicated(keep=False)
                if (changed & dup).any():
                    warnings.warn(
                        f"{rel_file}: host-code normalisation collided with an "
                        f"existing unit; votes should be merged manually.")

        # ── replace name column with canonical geo name for this year ──────
        if "gisco_id" in election_df.columns and "name" in election_df.columns:
            def _resolve_name(row: pd.Series) -> str:
                gid = str(row["gisco_id"]).strip() if pd.notna(row["gisco_id"]) else ""
                if gid and gid in geo_name_map:
                    name_or, names_json = geo_name_map[gid]
                    return _name_for_year(name_or, names_json, year)
                return row["name"]
            election_df["name"] = election_df.apply(_resolve_name, axis=1)

        # ── rename party columns to party_id keys ─────────────────────────
        rename_map: dict[str, str] = {}
        for col in election_df.columns:
            if col.lower() in _META_COLS:
                continue
            pid = alias_lookup.get((rel_file, col))
            if pid is None:
                warnings.warn(
                    f"[party_id] No alias match for column '{col}' "
                    f"in {rel_file} — column kept as-is"
                )
                missing_party_map.append(pd.Series(
                    {"party_name": col, 
                     "file": rel_file, 
                     "country": rel_file.split("/")[0].split("_")[-1],
                     "corr_party_name": ""}))
            else:
                rename_map[col] = pid

        if rename_map:
            election_df = election_df.rename(columns=rename_map)

        # Sum any party columns that ended up with the same name after renaming
        # (multiple source columns aliased to the same party_id).
        party_positions = [
            i for i, c in enumerate(election_df.columns) if c.lower() not in _META_COLS
        ]
        if len(party_positions) != len({election_df.columns[i] for i in party_positions}):
            meta_part = election_df[
                [c for c in election_df.columns if c.lower() in _META_COLS]
            ].copy()
            merged: dict[str, pd.Series] = {}
            for i in party_positions:
                col = election_df.columns[i]
                vals = pd.to_numeric(election_df.iloc[:, i], errors="coerce").fillna(0)
                merged[col] = merged[col] + vals if col in merged else vals
            election_df = pd.concat(
                [meta_part, pd.DataFrame(merged, index=election_df.index)], axis=1
            )

        election_df.to_csv(dest_folder / dest_name, index=False)
        count += 1

    if missing_party_map:
        missing_party_df = pd.DataFrame(missing_party_map)
        if os.path.exists(MISSING_PARTIES_FN):
            old_missing_party_df = pd.read_csv(MISSING_PARTIES_FN, na_values=[''], keep_default_na=False)
            missing_party_df = pd.concat([old_missing_party_df, missing_party_df], axis=0)
        missing_party_df.to_csv(MISSING_PARTIES_FN, index=False)
        
    return count


def build_elections(
    alias_lookup: dict[tuple[str, str], str],
    geo_name_map: dict[str, tuple[str, str]],
    file_meta: dict[str, dict],
) -> None:
    """Copy per-election CSVs into dist/elections/{CC}/, renaming party columns,
    resolving geo names, and applying the new filename convention."""
    out_dir = DIST / "elections"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Remove any leftover flat per-country CSVs from an older build layout
    for old_csv in out_dir.glob("*.csv"):
        old_csv.unlink()

    for cc in sorted(ALL_COUNTRIES):
        cc_dir = out_dir / cc
        n_dom = 0
        n_eu = 0

        dom_folder = OUTPUT / cc
        if dom_folder.is_dir():
            n_dom = _copy_election_files(
                dom_folder, cc_dir, alias_lookup, geo_name_map,
                rel_prefix=cc, file_meta=file_meta,
            )

        eu_folder = OUTPUT / f"EU_{cc}"
        if eu_folder.is_dir():
            n_eu = _copy_election_files(
                eu_folder, cc_dir, alias_lookup, geo_name_map,
                rel_prefix=f"EU_{cc}", file_meta=file_meta,
            )

        if n_dom + n_eu > 0:
            print(f"  {cc}: {n_dom} national + {n_eu} EP election files")
        else:
            print(f"  SKIP {cc}: no data files")


def _load_file_meta() -> dict[str, dict]:
    """Load data_files_reference.csv → lookup keyed by 'output/CC/file.csv' path.

    Each value is {'month': '11', 'round': '1', 'vote_type': 'Erststimme'} with
    empty strings for absent fields.
    """
    if not DATA_FILES_REF_SRC.exists():
        return {}
    df = pd.read_csv(DATA_FILES_REF_SRC, low_memory=False, na_values=[''], keep_default_na=False)
    meta: dict[str, dict] = {}
    for _, row in df.iterrows():
        fp = str(row.get("file_path") or "").strip()
        if not fp:
            continue
        date = str(row.get("election_date") or "").strip()
        month = date[5:7] if len(date) >= 7 else ""
        round_raw = row.get("round")
        round_str = ""
        if pd.notna(round_raw) and str(round_raw).strip() not in ("", "nan"):
            try:
                round_str = str(int(float(round_raw)))
            except (ValueError, TypeError):
                round_str = str(round_raw).strip()
        vote_type = str(row.get("vote_type") or "").strip()
        if vote_type == "nan":
            vote_type = ""
        meta[fp] = {"month": month, "round": round_str, "vote_type": vote_type}
    return meta


def _build_dist_filename(stem: str, meta: dict, is_eu: bool) -> str:
    """Construct the distribution filename using the convention
    {year}[_{month}][_{round}][_{vote_type}].csv (with EU_ prefix for EP files).
    """
    year = stem[:4]
    parts = [year]
    month = (str(meta.get("month") or "")).strip()
    if month and month != "nan":
        parts.append(month)
    round_val = (str(meta.get("round") or "")).strip()
    if round_val and round_val not in ("", "nan"):
        parts.append(round_val)
    vote_type = (str(meta.get("vote_type") or "")).strip()
    if vote_type and vote_type not in ("", "nan"):
        parts.append(vote_type)
    prefix = "EU_" if is_eu else ""
    return prefix + "_".join(parts) + ".csv"


def write_data_files_reference(file_meta: dict[str, dict]) -> None:
    """Write a transformed copy of data_files_reference.csv to dist/.

    - Strips the 'output/' prefix from file_path.
    - Applies the new distribution filename convention.
    - Drops the file_type column if present.
    """
    if not DATA_FILES_REF_SRC.exists():
        print("  WARN: data_files_reference.csv not found, skipping")
        return
    df = pd.read_csv(DATA_FILES_REF_SRC, low_memory=False, na_values=[''], keep_default_na=False)

    def transform_path(old_path: str) -> str:
        meta = file_meta.get(old_path, {})
        parts = str(old_path).split("/")
        if len(parts) < 3:
            return old_path
        folder = parts[1]          # "AT" or "EU_AT"
        filename = parts[2]        # "2002.csv"
        is_eu = folder.startswith("EU_")
        cc = folder[3:] if is_eu else folder
        stem = Path(filename).stem
        new_name = _build_dist_filename(stem, meta, is_eu)
        return f"elections/{cc}/{new_name}"

    df["file_path"] = df["file_path"].apply(lambda p: transform_path(str(p)))
    df = df.drop(columns=["file_type"], errors="ignore")
    df = df.sort_values(by=["election_date", "country_code"]).reset_index(drop=True)
    df.to_csv(DIST / "elections" / "index.csv", index=False)
    print(f"  data_files_reference.csv: {len(df)} rows")


# Historical EP group names → canonical party_id, for predecessor groups that
# are not (and need not be) listed as name variants in ep_groups.csv.
# Keyed by the normalised form produced by _norm_group_name().
_EP_GROUP_ALIASES = {
    "communist and allies": "GUE/NGL",
    "european united left": "GUE/NGL",                 # Confederal Group of the EUL
    "europe nations coordination": "UEN",              # Europe of Nations Group
    "union for europe": "UEN",                         # Group Union for Europe
    "for a europe democracies and diversities": "EFDD",  # EDD
    "european liberal democrat and reform party": "Renew",  # ELDR
    "liberal and democratic reformist": "Renew",       # LDR
    "european people s party christian democratic": "EPP Group",  # EPP-CD (pre-1999)
    "european radical alliance": "Greens/EFA",         # ERA (regionalist, → EFA)
    "identity tradition and sovereignty": "NI",        # ITS (→ non-attached)
    "non attached": "NI",
    "non attached members": "NI",
    "technical independent members mixed": "NI",
    "green": "Greens/EFA",                             # The Green Group (pre-1999)
}


def _norm_group_name(s: object) -> str:
    """Normalise an EP group name for fuzzy matching.

    Lowercases, unifies punctuation, and strips boilerplate words ("Group",
    "of the", "in the European Parliament", …) so that historical phrasings of
    the same group collapse onto a single key.
    """
    text = str(s).lower().replace("’", "'").replace("–", "-").replace("/", " ")
    text = re.sub(r"\b(group|of|the|in|european parliament|confederal)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _build_ep_group_lookup(ep_groups_df: pd.DataFrame) -> dict[str, str]:
    """Build a name → party_id lookup for EP groups.

    Includes the exact ``ep_group_key`` plus *normalised* forms of every name
    variant (ep_group_key, name_native, name_english, other_names) and the
    historical aliases in :data:`_EP_GROUP_ALIASES`, so that the full official
    group names stored in parties_final.csv resolve to the short canonical id.
    """
    lookup: dict[str, str] = {}
    for _, row in ep_groups_df.iterrows():
        pid = str(row["party_id"])
        # Exact key (back-compat with the previous behaviour)
        key = row.get("ep_group_key")
        if pd.notna(key) and str(key).strip():
            lookup[str(key).strip()] = pid
        # Normalised name variants
        names = [row.get("ep_group_key"), row.get("name_native"), row.get("name_english")]
        raw = row.get("other_names")
        if pd.notna(raw):
            try:
                for e in json.loads(str(raw)):
                    if isinstance(e, dict) and e.get("name_native"):
                        names.append(e["name_native"])
            except Exception:
                pass
        for n in names:
            if pd.notna(n) and str(n).strip():
                lookup.setdefault(_norm_group_name(n), pid)
    # Historical predecessors not present in ep_groups.csv
    for norm_name, pid in _EP_GROUP_ALIASES.items():
        lookup.setdefault(norm_name, pid)
    return lookup


def _rewrite_party_json_refs(
    json_value: object,
    key_to_id: dict[str, str],
    old_field: str,
    normalizer=None,
    unmapped: set | None = None,
) -> str:
    """Rewrite a JSON-list party-ref column: rename *old_field* → 'party_id' via lookup.

    Resolution order for each value:
      1. exact match in *key_to_id*;
      2. if *normalizer* is given, match on ``normalizer(value)``;
      3. otherwise keep the original value (and record it in *unmapped*).
    """
    if json_value is None:
        return "[]"
    try:
        if isinstance(json_value, float) and pd.isna(json_value):
            return "[]"
    except Exception:
        pass
    s = str(json_value).strip()
    if not s or s in ("nan", "[]"):
        return "[]"
    try:
        entries = json.loads(s)
        if not isinstance(entries, list):
            return "[]"
    except Exception:
        return "[]"
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        old_val = str(entry.get(old_field) or "").strip()
        new_entry = {k: v for k, v in entry.items() if k != old_field}
        if old_val:
            mapped = key_to_id.get(old_val)
            if mapped is None and normalizer is not None:
                mapped = key_to_id.get(normalizer(old_val))
            if mapped is None:
                mapped = old_val
                if unmapped is not None:
                    unmapped.add(old_val)
            new_entry["party_id"] = mapped
        out.append(new_entry)
    return json.dumps(out, ensure_ascii=False)


def _rename_json_date_keys(json_value: object) -> str:
    """Rename date_start/date_end → year_start/year_end in every entry of a JSON list."""
    if json_value is None:
        return "[]"
    try:
        if isinstance(json_value, float) and pd.isna(json_value):
            return "[]"
    except Exception:
        pass
    s = str(json_value).strip()
    if not s or s in ("nan", "[]"):
        return "[]"
    try:
        entries = json.loads(s)
        if not isinstance(entries, list):
            return "[]"
    except Exception:
        return "[]"
    _MAP = {"date_start": "year_start", "date_end": "year_end"}
    return json.dumps(
        [{_MAP.get(k, k): v for k, v in e.items()} if isinstance(e, dict) else e
         for e in entries],
        ensure_ascii=False,
    )


def _dates_to_years(df: pd.DataFrame) -> pd.DataFrame:
    """Replace start_date/end_date ISO strings with start_year/end_year integers."""
    for old, new in [("start_date", "start_year"), ("end_date", "end_year")]:
        if old in df.columns:
            years = pd.to_numeric(df[old].astype(str).str[:4], errors="coerce")
            idx = df.columns.get_loc(old)
            df.insert(idx, new, years.astype("Int64"))
            df.drop(columns=[old], inplace=True)
    return df


def build_geo():
    out_dir = DIST / "geo"
    out_dir.mkdir(parents=True, exist_ok=True)

    for src, name in [(COMMUNES_SRC, "laus.csv"), (SPECIAL_SRC, "special.csv")]:
        if src.exists():
            df = _dates_to_years(pd.read_csv(src, low_memory=False, na_values=[''], keep_default_na=False))
            df.to_csv(out_dir / name, index=False)
            print(f"  geo/{name}: {len(df)} rows")
        else:
            print(f"  WARN: {src} not found")

    nuts_frames = [pd.read_csv(s, low_memory=False, na_values=['']) for s in NUTS_SRCS if s.exists()]
    if nuts_frames:
        nuts_df = _dates_to_years(pd.concat(nuts_frames, ignore_index=True))
        nuts_df.to_csv(out_dir / "nuts.csv", index=False)
        print(f"  geo/nuts.csv: {len(nuts_df)} rows ({len(nuts_frames)} levels)")
    else:
        print("  WARN: no NUTS source files found")


def build_parties(parties_df: pd.DataFrame) -> None:
    """Write parties.csv, eu_parties.csv and ep_groups.csv into dist/parties/."""
    out_dir = DIST / "parties"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load reference files and build key → party_id lookup dicts
    eu_parties_df: pd.DataFrame | None = None
    ep_groups_df: pd.DataFrame | None = None
    eu_key_to_id: dict[str, str] = {}
    ep_key_to_id: dict[str, str] = {}

    if EU_PARTIES_SRC.exists():
        eu_parties_df = pd.read_csv(EU_PARTIES_SRC, low_memory=False, na_values=[''], keep_default_na=False)
        eu_key_to_id = dict(zip(
            eu_parties_df["european_party_key"].astype(str),
            eu_parties_df["party_id"].astype(str),
        ))
    if EP_GROUPS_SRC.exists():
        ep_groups_df = pd.read_csv(EP_GROUPS_SRC, low_memory=False, na_values=[''], keep_default_na=False)
        # Lookup includes exact keys + normalised name variants + historical aliases
        ep_key_to_id = _build_ep_group_lookup(ep_groups_df)

    # Collect any group names that could not be mapped, to warn at the end
    unmapped_groups: set[str] = set()

    # ── parties.csv: rewrite JSON refs from key strings to party_ids ──────────
    dist_df = parties_df.copy()
    if eu_key_to_id and "eu_party" in dist_df.columns:
        dist_df["eu_party"] = dist_df["eu_party"].apply(
            lambda v: _rename_json_date_keys(
                _rewrite_party_json_refs(v, eu_key_to_id, "organization")
            )
        )
    if ep_key_to_id and "ep_group" in dist_df.columns:
        dist_df["ep_group"] = dist_df["ep_group"].apply(
            lambda v: _rename_json_date_keys(
                _rewrite_party_json_refs(
                    v, ep_key_to_id, "group",
                    normalizer=_norm_group_name, unmapped=unmapped_groups,
                )
            )
        )
    cols = ["party_id"] + [c for c in dist_df.columns if c not in ("party_id", "aliases_in_files")]
    dist_df[cols].to_csv(out_dir / "parties.csv", index=False)
    print(f"  parties/parties.csv: {len(dist_df)} rows")
    if unmapped_groups:
        warnings.warn(
            "[ep_group] Unmapped EP group names kept verbatim "
            f"({len(unmapped_groups)}): {sorted(unmapped_groups)}"
        )

    # ── eu_parties.csv: drop join key, rewrite ep_group refs ─────────────────
    if eu_parties_df is not None:
        out = eu_parties_df.drop(columns=["european_party_key"], errors="ignore").copy()
        if ep_key_to_id and "ep_group" in out.columns:
            out["ep_group"] = out["ep_group"].apply(
                lambda v: _rename_json_date_keys(
                    _rewrite_party_json_refs(
                        v, ep_key_to_id, "group",
                        normalizer=_norm_group_name, unmapped=unmapped_groups,
                    )
                )
            )
        out.to_csv(out_dir / "eu_parties.csv", index=False)
        print(f"  parties/eu_parties.csv: {len(out)} rows")
    else:
        print(f"  WARN: {EU_PARTIES_SRC} not found")

    # ── ep_groups.csv: drop join key ─────────────────────────────────────────
    if ep_groups_df is not None:
        ep_groups_df.drop(columns=["ep_group_key"], errors="ignore").to_csv(
            out_dir / "ep_groups.csv", index=False
        )
        print(f"  parties/ep_groups.csv: {len(ep_groups_df)} rows")
    else:
        print(f"  WARN: {EP_GROUPS_SRC} not found")


def write_readme():
    text = """\
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
"""
    (DIST / "README.md").write_text(text, encoding="utf-8")
    print("  README.md written")


def check_errors():
    """Warn about any _errors.csv files found under output/."""
    error_files = sorted(OUTPUT.rglob("*_errors.csv"))
    if not error_files:
        return
    print(f"\n  {'─'*60}")
    print(f"  WARNING: {len(error_files)} error file(s) found in output/:")
    for f in error_files:
        try:
            n = sum(1 for _ in open(f)) - 1
        except Exception:
            n = "?"
        print(f"    ⚠  {f.relative_to(OUTPUT)}  ({n} row(s))")
    print(f"  {'─'*60}")


def main():
    DIST.mkdir(parents=True, exist_ok=True)
    print("=== Building distribution ===")

    if not PARTIES_SRC.exists():
        raise FileNotFoundError(f"parties file not found: {PARTIES_SRC}")
    parties_df, party_map = build_party_map(PARTIES_SRC)
    n_ids = len(set(party_map.id_by_index.values()))
    print(f"  Loaded {len(parties_df)} parties → {n_ids} unique party_ids")

    file_meta = _load_file_meta()
    print(f"  Loaded {len(file_meta)} file metadata entries")

    print("\n[geo]")
    build_geo()

    print("\n[parties]")
    build_parties(parties_df)

    geo_name_map = build_geo_name_map(COMMUNES_SRC, SPECIAL_SRC)
    print(f"  Loaded {len(geo_name_map)} geo units for name resolution")

    print("\n[elections]")
    build_elections(party_map.alias_lookup, geo_name_map, file_meta)

    print("\n[reference]")
    write_data_files_reference(file_meta)

    print("\n[README]")
    write_readme()

    check_errors()

    print("\nDone.")


if __name__ == "__main__":
    main()
