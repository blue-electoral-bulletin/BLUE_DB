"""
blue_maps.py
============
Geometry resolution for BLUE_DB electoral data, returning GeoDataFrames in
EPSG:3035 (ETRS89-LAEA Europe) suitable for choropleth mapping.

Resolution priority for each *gisco_id*:

1. **maps/ overrides** – local shapefiles for special constituencies.
   - ``maps/{CC}.shp``         → national LAU-level overrides (e.g. IE, MT, IS).
   - ``maps/EU_{CC}_{YEAR}.shp`` → EP election constituency boundaries.
2. **GISCO municipalities** – ``LAU_RG_01M_{year}_3035.geojson`` for 2011 and
   later, ``COMM_RG_..._{year}_3035.shp.zip`` (the *communes* distribution) for
   the 2001/2004/2006/2008/2010 vintages, downloaded from
   gisco-services.ec.europa.eu and cached on disk.
3. **GISCO NUTS** – ``NUTS_RG_01M_{year}_3035_LEVL_{n}.geojson`` for
   NUTS3/2/1/0 units, tried in order.

For French, Portuguese, and Spanish overseas territories that participate in
EU elections, the geometries are scaled and translated into inset boxes near
continental Europe so that the full map remains readable.

Quick start
-----------
    from blue_maps import BlueGeo
    geo = BlueGeo()
    gdf = geo.geometries(gisco_ids, year=2024)
    gdf.plot(column="value")
"""

from __future__ import annotations

import io
import os
import re
from collections import defaultdict
import csv
import json
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Collection, NamedTuple, Sequence

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

try:  # installed as a package
    from ._derived import derived_glob as _derived_glob
    from ._derived import maps_path as _maps_path
    from ._paths import cache_dir as _resolve_cache_dir
    from ._paths import dist_dir as _resolve_dist_dir
    from ._paths import maps_dir as _resolve_maps_dir
except ImportError:  # run straight from lib/ (sys.path hack, see demo/)
    from _derived import derived_glob as _derived_glob  # type: ignore[no-redef]
    from _derived import maps_path as _maps_path  # type: ignore[no-redef]
    from _paths import cache_dir as _resolve_cache_dir  # type: ignore[no-redef]
    from _paths import dist_dir as _resolve_dist_dir  # type: ignore[no-redef]
    from _paths import maps_dir as _resolve_maps_dir  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Paths (resolved at import time; see blue_db._paths for the search order)
# ---------------------------------------------------------------------------
_MAPS    = _resolve_maps_dir()
_CACHE   = _resolve_cache_dir()

# ---------------------------------------------------------------------------
# GISCO base URLs
# ---------------------------------------------------------------------------
_GISCO_LAU  = "https://gisco-services.ec.europa.eu/distribution/v2/lau/"
_GISCO_COMM = "https://gisco-services.ec.europa.eu/distribution/v2/communes/"
_GISCO_NUTS = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/"
_GISCO_CNTR = "https://gisco-services.ec.europa.eu/distribution/v2/countries/"

# Available dataset years from GISCO (ascending)
# How far from an election's own vintage another one may be consulted for
# units the election's vintage does not contain, and how many such vintages to
# try.  A country published at the wrong level is fixed by a *neighbouring*
# vintage (Denmark 2011 -> 2012); scanning the whole range only reloads large
# datasets for IDs that exist nowhere.
_MAX_VINTAGE_DISTANCE = 10
_MAX_FALLBACK_VINTAGES = 3

# Share of a country's units that the election's own vintage must resolve for
# the remainder to be treated as ordinary churn rather than a broken vintage.
_FALLBACK_THRESHOLD = 0.95

_LAU_YEARS  = [2011,2012,2013,2014,2015,2016,2017,2018,2019,2020,2021,2022,2023,2024]
_NUTS_YEARS = [2003,2006,2010,2013,2016,2021,2024]
# Vintages of the GISCO *countries* distribution, used for voters living
# outside Europe; see _countries_dataset.
_CNTR_YEARS = [2001,2006,2010,2013,2016,2020,2024]

# Municipal boundaries before 2011 come from the older *communes* distribution.
# Its file names are irregular (some vintages carry the ``01M`` generalisation
# tag, others do not) and only the 3035 projection is used here.
_COMM_FILES = {
    2001: "COMM_RG_01M_2001_3035",
    2004: "COMM_RG_2004_3035",
    2006: "COMM_RG_2006_3035",
    2008: "COMM_RG_2008_3035",
    2010: "COMM_RG_01M_2010_3035",
}
_COMM_YEARS = sorted(_COMM_FILES)

# All municipal-level vintages, communes then LAU (ascending)
_MUNI_YEARS = _COMM_YEARS + _LAU_YEARS



# ---------------------------------------------------------------------------
# Dataset year helpers
# ---------------------------------------------------------------------------

def _resource_bases() -> list[Path]:
    """Folders whose ``resources/`` (and ``dist/geo/``) the resolver reads.

    In order: the source checkout the package lives in, ``$BLUE_DB_HOME``, the
    folder holding the located ``dist/``, and the working directory -- so an
    installed package finds the overrides shipped with the data, not only a
    checkout does.
    """
    bases = [Path(__file__).resolve().parent.parent]
    if os.environ.get("BLUE_DB_HOME"):
        bases.append(Path(os.environ["BLUE_DB_HOME"]).expanduser())
    try:
        bases.append(Path(_resolve_dist_dir()).resolve().parent)
    except Exception:                                  # no dist/ located
        pass
    bases.append(Path.cwd())
    return list(dict.fromkeys(bases))


def _best_year(requested: int, available: list[int]) -> int:
    """Return the largest available year that is ≤ *requested*."""
    candidates = [y for y in available if y <= requested]
    return max(candidates) if candidates else min(available)


# ---------------------------------------------------------------------------
# maps/ override helpers
# ---------------------------------------------------------------------------

def _maps_national(cc: str, year: int | None = None) -> Path | None:
    """Return path to the best national maps file for *cc* and *year*.

    Checks for ``maps/{CC}_{YEAR}.shp`` files first (picks the latest whose
    year ≤ *year*), then falls back to ``maps/{CC}.shp``.
    """
    if year is not None:
        candidates = _derived_glob(_MAPS, _CACHE, f"{cc}_[0-9][0-9][0-9][0-9].shp")
        if candidates:
            selected = None
            for p in candidates:
                m = re.search(r"_(\d{4})\.shp$", p.name)
                if m and int(m.group(1)) <= year:
                    selected = p
            if selected:
                return selected
    p = _maps_path(_MAPS, _CACHE, f"{cc}.shp")
    return p if p.exists() else None


def _maps_eu(cc: str, year: int) -> Path | None:
    """Return the maps/EU_{CC}_{YEAR}.shp closest to *year* if it exists."""
    candidates = _derived_glob(_MAPS, _CACHE, f"EU_{cc}_*.shp")
    if not candidates:
        return None
    # pick the latest one whose year ≤ requested year
    selected = None
    for p in candidates:
        m = re.search(r"_(\d{4})\.shp$", p.name)
        if m and int(m.group(1)) <= year:
            selected = p
    return selected or candidates[0]


@lru_cache(maxsize=32)
def _read_maps_shp(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:3035")
    else:
        gdf = gdf.to_crs("EPSG:3035")
    if "gisco_id" not in gdf.columns and "GISCO_ID" in gdf.columns:
        gdf = gdf.rename(columns={"GISCO_ID": "gisco_id"})
    return gdf[["gisco_id", "geometry"]]


# ---------------------------------------------------------------------------
# ID normalisation – BLUE_DB gisco_id → GISCO LAU GISCO_ID
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-country vintage corrections
# ---------------------------------------------------------------------------
#
# A country's data can be reported at a level the GISCO vintage of its own year
# does not contain.  Resolving it there does not fail — it silently returns a
# polygon of the wrong level — so the affected countries carry a floor on the
# municipal vintage in ``resources/geo_corrections.csv``.  Only vintages below
# the floor are raised: an election after it still uses its own vintage, so
# boundary changes over time remain visible.

_CORRECTIONS_FILE = "geo_corrections.csv"
_vintage_floors: dict[str, int] | None = None


def _load_vintage_floors() -> dict[str, int]:
    """``{country: earliest usable municipal vintage}`` from the corrections file."""
    global _vintage_floors
    if _vintage_floors is not None:
        return _vintage_floors
    floors: dict[str, int] = {}
    for base in _resource_bases():
        path = base / "resources" / _CORRECTIONS_FILE
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    cc = (row.get("country") or "").strip().upper()
                    raw = (row.get("min_vintage") or "").strip()
                    if cc and raw.isdigit():
                        floors[cc] = int(raw)
        except Exception as exc:                      # never fatal: worst case
            warnings.warn(f"Could not read {path}: {exc}")   # is today's behaviour
        break
    _vintage_floors = floors
    return floors


def effective_vintage(country_code: str | None, year: int) -> int:
    """The year whose municipal vintage should be used to draw *country_code*.

    ``year`` itself unless the country has a floor and the year falls below it.
    Public because the viewer pre-computes geometry per (country, vintage) and
    has to make the same choice as :class:`BlueGeo`.
    """
    if not country_code:
        return year
    floor = _load_vintage_floors().get(str(country_code).upper())
    return max(year, floor) if floor else year


# ---------------------------------------------------------------------------
# Geometry provenance: which polygon, in which layer, a unit is drawn with
# ---------------------------------------------------------------------------
#
# Resolution produces a *reference* to a polygon rather than the polygon
# itself.  That separation is what makes the join auditable and correctable:
# the reference can be written to a file, reviewed, and overridden, while the
# geometry stays where its licence requires it to stay (GISCO's servers or the
# repository's own maps/).  A reference has three parts:
#
#     kind    "maps"  a shapefile in maps/
#             "lau"   a GISCO municipal tile (LAU, or communes before 2011)
#             "nuts"  a GISCO NUTS tile
#     layer   "BE_2003.shp" | "2021" (the vintage) | "2021:3" (vintage:level)
#     source_id   the identifier of the feature inside that layer
#
# ``resources/geo_overrides.csv`` maps a (country, gisco_id, year range) to a
# reference explicitly, and is consulted before every automatic rule.  It is
# how a unit whose ID no automatic rule can match — an abolished municipality,
# a renumbered constituency — is given the polygon it should have.


class SourceRef(NamedTuple):
    """A pointer to one polygon in one layer.  See the module notes above."""

    kind: str
    layer: str
    source_id: str

    def __str__(self) -> str:                      # "lau:2021#BE_11001"
        return f"{self.kind}:{self.layer}#{self.source_id}"

    @classmethod
    def parse(cls, text: str) -> "SourceRef":
        head, _, source_id = str(text).partition("#")
        kind, _, layer = head.partition(":")
        return cls(kind, layer, source_id)


_OVERRIDES_FILE = "geo_overrides.csv"
_OVERRIDE_COLUMNS = ["country", "gisco_id", "year_from", "year_to",
                     "election_type", "source_kind", "source_layer",
                     "source_id", "note"]
_overrides: dict[str, list[tuple]] | None = None


def _load_geo_overrides() -> dict[str, list[tuple]]:
    """``{gisco_id: [(year_from, year_to, election_type, SourceRef), …]}``.

    Blank ``year_from``/``year_to`` mean "any year"; a blank ``election_type``
    means both.  Several rows may share a gisco_id when its geometry changed:
    the first whose window contains the election year wins, so narrower rows
    must come first — the loader sorts them so that dated rows precede
    open-ended ones and the file's own order does not matter.
    """
    global _overrides
    if _overrides is not None:
        return _overrides
    table: dict[str, list[tuple]] = {}
    for base in _resource_bases():
        path = base / "resources" / _OVERRIDES_FILE
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    gid = (row.get("gisco_id") or "").strip()
                    kind = (row.get("source_kind") or "").strip()
                    if not gid or not kind or gid.startswith("#"):
                        continue
                    y0 = (row.get("year_from") or "").strip()
                    y1 = (row.get("year_to") or "").strip()
                    table.setdefault(gid, []).append((
                        int(y0) if y0.isdigit() else 0,
                        int(y1) if y1.isdigit() else 9999,
                        (row.get("election_type") or "").strip().lower(),
                        SourceRef(kind, (row.get("source_layer") or "").strip(),
                                  (row.get("source_id") or "").strip() or gid),
                    ))
        except Exception as exc:                    # never fatal: worst case is
            warnings.warn(f"Could not read {path}: {exc}")   # today's behaviour
        break
    for rows in table.values():                     # narrowest window first
        rows.sort(key=lambda r: (r[1] - r[0], not r[2]))
    _overrides = table
    return table


# ---------------------------------------------------------------------------
# Constituencies made of whole foreign countries
# ---------------------------------------------------------------------------
#
# Expatriates do not always vote in a unit that maps to one country.  France
# divides the world into eleven circonscriptions législatives des Français de
# l'étranger; Portugal groups its emigrants by continent; several countries
# have a single "abroad" unit covering everywhere at once.  None of these has a
# polygon anywhere, but each is exactly a *union of countries*, so it can be
# drawn from the world country layer once the membership is written down —
# which is what ``resources/foreign_constituencies.csv`` does.
#
# Two kinds of membership appear there:
#   ``list``  an explicit set of country codes (France's, fixed by law).
#   ``rest``  every country of one or more regions, *minus* the countries that
#             have their own unit in the same election.  Portugal's "restantes
#             países da Europa" means whatever is left once Germany, France,
#             Switzerland and the rest have been counted separately, and that
#             remainder differs from election to election — so it is computed
#             per election rather than stored.

# Antarctica (AQ) and the uninhabited sub-Antarctic dependencies: Bouvet,
# South Georgia, Heard & McDonald, and the French Southern Territories.
_ANTARCTIC = {"AQ", "BV", "GS", "HM", "TF"}

_FOREIGN_FILE = "foreign_constituencies.csv"
_REGIONS_FILE = "country_regions.csv"
_foreign: dict[str, tuple[str, list[str]]] | None = None
_regions: dict[str, str] | None = None


def _resource(name: str) -> Path | None:
    for base in _resource_bases():
        path = base / "resources" / name
        if path.exists():
            return path
    return None


def _load_country_regions() -> dict[str, str]:
    """``{country code: continent code}`` (UN M49: EU, AF, AS, NA, SA, OC)."""
    global _regions
    if _regions is None:
        _regions = {}
        path = _resource(_REGIONS_FILE)
        if path:
            try:
                with path.open(encoding="utf-8") as fh:
                    for row in csv.DictReader(fh):
                        code = (row.get("country_code") or "").strip()
                        if code:
                            _regions[code] = (row.get("continent") or "").strip()
            except Exception as exc:
                warnings.warn(f"Could not read {path}: {exc}")
    return _regions


def _load_foreign_constituencies() -> dict[str, tuple[str, list[str]]]:
    """``{gisco_id: (mode, members)}`` from the foreign-constituency file."""
    global _foreign
    if _foreign is None:
        _foreign = {}
        path = _resource(_FOREIGN_FILE)
        if path:
            try:
                with path.open(encoding="utf-8") as fh:
                    for row in csv.DictReader(fh):
                        gid = (row.get("gisco_id") or "").strip()
                        mode = (row.get("mode") or "").strip().lower()
                        if gid and mode:
                            members = [m.strip() for m in
                                       (row.get("members") or "").split(";") if m.strip()]
                            _foreign[gid] = (mode, members)
            except Exception as exc:
                warnings.warn(f"Could not read {path}: {exc}")
    return _foreign


def foreign_members(gisco_id: str, siblings: Collection[str] = ()) -> list[str]:
    """Countries making up a foreign constituency, or ``[]`` if it is not one.

    *siblings* are the other unit IDs of the same election; the countries they
    cover are excluded, so a "remaining countries" unit does not swallow the
    ones that were counted separately.
    """
    entry = _load_foreign_constituencies().get(gisco_id)
    if entry is None:
        return []
    mode, members = entry
    if mode == "list":
        return sorted(members)

    regions = _load_country_regions()
    wanted = set(regions) if members == ["WORLD"] else {
        c for c, region in regions.items() if region in members}
    # Antarctica and the sub-Antarctic islands are in no constituency, and a
    # "rest of the world" unit that includes them stretches to the pole and
    # swamps the map it is drawn on.  Nobody votes there.
    wanted -= _ANTARCTIC
    # Never the home country: these units are defined as being abroad.
    home = gisco_id.split("_")[0].upper()
    wanted.discard(_ISO_TO_NUTS.get(home, home))
    for sibling in siblings:
        if sibling == gisco_id:
            continue
        host = abroad_host(sibling)
        if host:
            wanted.discard(host)
        # Another constituency with an explicit membership (France's) also
        # claims its countries.  Only "list" siblings, never "rest" ones —
        # two remainders would otherwise cancel each other out entirely.
        other = _load_foreign_constituencies().get(sibling)
        if other and other[0] == "list":
            wanted.difference_update(other[1])
    return sorted(wanted)


def geo_override(gisco_id: str, year: int, election_type: str = "national"):
    """The overriding :class:`SourceRef` for a unit in a year, if any."""
    for y0, y1, etype, ref in _load_geo_overrides().get(gisco_id, ()):
        if y0 <= year <= y1 and etype in ("", str(election_type).lower()):
            return ref
    return None



# Bulgaria's older files identify an obshtina by its administrative centre, so
# the ID carries a settlement (EKATTE) code that resolves to that one village.
# Two forms occur: ``BG_BLG5277058`` (oblast + obshtina + EKATTE), where the
# obshtina is already present, and ``BG_66665`` (bare EKATTE), which needs the
# settlement register to find it.
_BG_LONG_RE = re.compile(r"^BG_([A-Z]{3}\d{2})\d{5}$")
_BG_BARE_RE = re.compile(r"^BG_(\d{5})$")
_bg_ekatte: dict[str, str] | None = None


def _bg_obshtina_of_ekatte(ekatte: str) -> str | None:
    """Obshtina code of a Bulgarian settlement, from resources/bg_ekatte.json."""
    global _bg_ekatte
    if _bg_ekatte is None:
        _bg_ekatte = {}
        for base in _resource_bases():
            path = base / "resources" / "bg_ekatte.json"
            if not path.exists():
                continue
            try:
                for row in json.loads(path.read_text(encoding="utf-8")):
                    code, obshtina = row.get("ekatte"), row.get("obshtina")
                    if code and obshtina:
                        _bg_ekatte[str(code).zfill(5)] = str(obshtina)
            except Exception as exc:
                warnings.warn(f"Could not read {path}: {exc}")
            break
    return _bg_ekatte.get(str(ekatte).zfill(5))


def _bg_obshtina_id(gisco_id: str) -> str | None:
    """``BG_BLG5277058`` / ``BG_77058`` → ``BG_BLG52``; None if not applicable."""
    m = _BG_LONG_RE.match(gisco_id)
    if m:
        return f"BG_{m.group(1)}"
    m = _BG_BARE_RE.match(gisco_id)
    if m:
        obshtina = _bg_obshtina_of_ekatte(m.group(1))
        return f"BG_{obshtina}" if obshtina else None
    return None



# ---------------------------------------------------------------------------
# Special units → their NUTS parent
# ---------------------------------------------------------------------------
#
# Electoral constituencies and other special units have no municipal geometry,
# but ``dist/geo/special.csv`` records the NUTS region each one sits in.  Where
# a unit *is* that region — Malta's EP contests are a single national
# constituency, ``MT_MT``/``MT_(MEP)`` with parent ``MT000`` — the parent's
# polygon is the right geometry rather than no geometry at all.

_special_parents: dict[str, str] | None = None


def _special_parent(gisco_id: str) -> str | None:
    """NUTS code a special unit belongs to, from dist/geo/special.csv."""
    global _special_parents
    if _special_parents is None:
        _special_parents = {}
        for base in _resource_bases():
            path = base / "dist" / "geo" / "special.csv"
            if not path.exists():
                continue
            try:
                with path.open(encoding="utf-8") as fh:
                    for row in csv.DictReader(fh):
                        gid = (row.get("gisco_id") or "").strip()
                        parent = (row.get("parent") or "").strip()
                        if gid and parent:
                            _special_parents[gid] = parent
            except Exception as exc:
                warnings.warn(f"Could not read {path}: {exc}")
            break
    return _special_parents.get(str(gisco_id))



# ---------------------------------------------------------------------------
# Voters abroad → the host country's boundary
# ---------------------------------------------------------------------------
#
# Abroad units are coded ``CC_CCZZxx`` (codebook §abroad), where ``xx`` is the
# ISO 3166-1 alpha-2 code of the host country: ``BG_BGZZDE`` is Bulgarians
# voting in Germany.  The natural geometry is the host's own boundary, which
# GISCO publishes as NUTS 0 for European countries; hosts outside that coverage
# stay unresolved until a boundary file is supplied.  ``ZZ98``/``ZZ99`` are
# aggregates of several hosts and have no single boundary.

# ``BG_BGZZDE`` is the canonical form, but Romania's files carry the bare
# ``ROZZDE``, so the country prefix is optional here: the alternative is that
# 314 Romanian abroad units silently have no geometry.
_ABROAD_RE = re.compile(r"^([A-Z]{2})(?:_\1)?ZZ([A-Z]{2})$", re.I)

# NUTS spells two countries differently from ISO 3166-1.
_ISO_TO_NUTS = {"GR": "EL", "GB": "UK"}

# Countries that no longer exist under the code the results use.  The election
# recorded a state that was real at the time, so the honest polygon is the one
# GISCO drew for it then: Poland's 2004 voters in "CS" lived in Serbia and
# Montenegro, which the 2001 vintage carries as YU.  Listed newest-first, so a
# successor state is used if no vintage still holds the original.
# Only codes GISCO itself once published are listed as alternatives.  A
# successor state must not appear here: NUTS still carries RS, so offering it
# for CS would draw Serbia alone and quietly lose Montenegro — half the
# constituency — where leaving it out sends the unit to the country layer,
# which has the state whole.
_HISTORIC_COUNTRIES = {
    "CS": ["YU"],           # Serbia and Montenegro, 2003–2006
    "DD": ["DE"],           # German Democratic Republic; GISCO has no DD
    "SU": ["RU"],           # Soviet Union; GISCO has no SU
}


def abroad_host(gisco_id: str) -> str | None:
    """NUTS code of the country an abroad unit's voters live in, if any."""
    m = _ABROAD_RE.match(str(gisco_id).strip())
    if not m:
        return None
    host = m.group(2).upper()
    if not host.isalpha():          # ZZ98 / ZZ99: several hosts pooled
        return None
    return _ISO_TO_NUTS.get(host, host)



def geo_join_id(gisco_id: str) -> str:
    """The ID a unit's geometry is published under.

    :meth:`BlueGeo.geometries` hides ID differences by resolving them on the
    way in, which is invisible to a consumer that publishes geometry separately
    and joins to it by ID — the map viewer does exactly that.  Such a consumer
    must map its unit IDs through this first.

    Two countries differ, both for the same reason — a unit is named after a
    place inside it rather than after itself:

    * Bulgaria's pre-2017 files identify an obshtina by its administrative
      centre (``BG_BLG5277058``, ``BG_66665``) where the geometry is published
      per obshtina (``BG_BLG52``).
    * Lithuania's 2008–2016 files identify a savivaldybė by one of its
      seniūnijos (``LT_4917`` is the town of Kaišiadorys, not the district),
      and its 2004 files use a code scheme GISCO retired after 2001.  Both are
      mapped to the modern municipal code (``LT_49``).

    Everything else is returned unchanged.  The mapping is many-to-one — an
    election can report a town and its surrounding rural area separately — so a
    caller keying records by ID has to merge collisions.
    """
    gid = str(gisco_id)
    return _bg_obshtina_id(gid) or _lt_municipality_id(gid) or gid


_LT_MUNICIPALITIES_FILE = "lt_municipalities.csv"
_lt_municipalities: dict[str, str] | None = None


def _lt_municipality_id(gisco_id: str) -> str | None:
    """The savivaldybė a Lithuanian unit belongs to, if it is not one itself.

    Built by locating each identifier's polygon inside the municipal layer, so
    it holds whichever level the source happened to use; see
    ``resources/lt_municipalities.csv``.
    """
    global _lt_municipalities
    if _lt_municipalities is None:
        _lt_municipalities = {}
        path = _resource(_LT_MUNICIPALITIES_FILE)
        if path:
            try:
                with path.open(encoding="utf-8") as fh:
                    for row in csv.DictReader(fh):
                        src = (row.get("gisco_id") or "").strip()
                        dst = (row.get("municipality") or "").strip()
                        if src and dst and src != dst:
                            _lt_municipalities[src] = dst
            except Exception as exc:
                warnings.warn(f"Could not read {path}: {exc}")
    return _lt_municipalities.get(gisco_id)



def _normalize_lau_id(gisco_id: str) -> str | None:
    """Return the GISCO LAU equivalent of a BLUE_DB *gisco_id*, or None.

    BLUE_DB and GISCO sometimes use different national-code conventions for
    the same municipality.  Known patterns:

    * **NL** ``NL_0358`` → ``NL_GM0358``
      GISCO prefixes the CBS municipal code with ``GM``.
    * **CH** ``CH_0001`` → ``CH_CH0001``
      GISCO prefixes the OFS/BFS commune number with the country code again.
    * **RO** ``RO_001017`` → ``RO_1017``
      BLUE_DB zero-pads the SIRUTA code to six digits; GISCO uses the bare
      integer (no leading zeros).
    * **LI** ``LI_00001001`` → ``LI_LI00001001``
      Same double-prefix pattern as CH.
    """
    if "_" not in gisco_id:
        return None
    cc, local = gisco_id.split("_", 1)
    if cc == "NL":
        return f"NL_GM{local}"
    if cc == "CH":
        return f"CH_CH{local}"
    if cc == "LI":
        return f"LI_LI{local}"
    if cc == "RO":
        try:
            return f"RO_{int(local)}"
        except ValueError:
            return None
    if cc == "BG":
        return _bg_obshtina_id(gisco_id)
    return None


# ---------------------------------------------------------------------------
# ID normalisation – GISCO communes → BLUE_DB gisco_id
# ---------------------------------------------------------------------------
#
# The pre-2011 *communes* datasets predate the ``GISCO_ID`` column: they carry a
# national commune code (``COMM_CODE`` in 2001, ``COMM_ID`` from 2004 on) whose
# layout differs per country and per vintage.  The rules below are the ones
# BLUE_DB itself used to build its municipality registry
# (``municipality_generation/preprocess_gisco_ids.py``); keeping them in sync is
# what makes the resulting IDs match the ``gisco_id`` values in ``dist/``.

def _commune_gisco_id(cntr_code: str, comm_code: str, vintage: int) -> str | None:
    """Convert a communes-dataset national code into a BLUE_DB *gisco_id*."""
    if not isinstance(comm_code, str) or len(comm_code) < 3:
        return None
    cc = "EL" if cntr_code == "GR" else cntr_code
    # The code repeats the country prefix; the national part follows.
    lau = comm_code[2:]

    try:
        if cc == "FR":
            dep = lau[2:4]
            if dep in ("97", "9A", "9B", "9C", "9D"):
                # The overseas départements are coded differently before 2006.
                if vintage in (2001, 2004):
                    lau = "97" + lau[-3:]
                else:
                    lau = "97" + lau[4] + lau[-2:]
            else:
                lau = dep + lau[-3:]
        elif cc == "DE":
            # Rebuild the 8-digit AGS (Land, Kreis, Gemeinde).
            if lau[2] != "0":
                lau = lau[:3] + lau[4:6] + lau[-3:]
            else:
                lau = lau[:2] + lau[3:6] + lau[-3:]
        elif cc in ("IT", "CZ", "SK"):
            lau = lau[-6:]
        elif cc in ("ES", "RO", "BE", "HR"):
            lau = lau[-5:]
        elif cc in ("HU", "CH", "LT", "NL", "EE", "IS"):
            lau = lau[-4:]
        elif cc == "FI":
            lau = lau[-3:]
        elif cc == "PT":
            lau = lau[1:-1]
        elif cc == "LU":
            lau = lau[:4]
        elif cc == "MT":
            lau = "0" + lau[1:]
        elif cc == "LI":
            lau = "0000" + lau
    except IndexError:
        return None

    return _normalize_commune_id(f"{cc}_{lau}".upper())


def _normalize_commune_id(gisco_id: str) -> str:
    """Fold GISCO's national-code quirks into the BLUE_DB convention."""
    s = gisco_id.replace("NL_GM", "NL_").replace("NL_NL", "NL_")
    s = s.replace("MF_", "FR_").replace("LI_LI", "LI_").replace("MT_MT", "MT_")
    if s.startswith("CH"):
        s = s.replace("CH_CH", "CH_")
        try:
            s = f"CH_{int(s.split('_')[-1]):04}"
        except ValueError:
            pass
    return s


# Countries where the commune code above cannot reproduce the BLUE_DB ID but
# the national statistical code (``NSI_CODE``) can, as (accepted code lengths,
# zero-padded width).  Both the country list and the lengths were checked
# against ``dist/geo/laus.csv`` by comparing commune names: NSI codes of the
# wrong length come from another level of the national hierarchy (French codes
# are région+département+commune in 2001, Danish ones gain a digit in 2010) and
# for Greece and Slovenia they are valid-looking BLUE_DB IDs that denote
# *other* municipalities — hence no entry for them at all.
_ALIAS_RULES = {
    "DK": ({3}, None),          # pre-reform kommune numbers (2001-2008)
    "RO": ({4, 5, 6}, 6),       # SIRUTA code, zero-padded to six digits
    "PT": ({6}, None),          # freguesia code (2010 vintage)
    "PL": ({9}, None),          # gmina code (2010 vintage)
    "DE": ({8}, None),          # a few city districts (2010 vintage)
    "FR": ({5}, None),          # Paris/Lyon/Marseille arrondissements
}


def _commune_alias_ids(cntr_code: str, nsi_code: str) -> list[str]:
    """Return fallback IDs built from the national statistical code.

    These are only ever consulted for units the primary rule did not resolve,
    so a spurious alias cannot displace a correct geometry.
    """
    rule = _ALIAS_RULES.get(cntr_code)
    if rule is None or not isinstance(nsi_code, str):
        return []
    n = nsi_code.strip()
    if n[:2] == cntr_code:          # the 2010 vintage prefixes the country code
        n = n[2:]
    lengths, width = rule
    if not n.isdigit() or len(n) not in lengths:
        return []
    return [f"{cntr_code}_{n.zfill(width) if width else n}"]


# ---------------------------------------------------------------------------
# GISCO download & cache
# ---------------------------------------------------------------------------

def _fetch_geojson(url: str, cache_dir: Path, cache_file: str,
                   timeout: int = 120) -> gpd.GeoDataFrame:
    """Download a GeoJSON from *url*, caching at *cache_file* in EPSG:3035."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cp = cache_dir / cache_file
    if not cp.exists():
        print(f"    Downloading {url} …", flush=True)
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        cp.write_bytes(r.content)
    gdf = gpd.read_file(cp)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:3035")
    elif gdf.crs.to_epsg() != 3035:
        gdf = gdf.to_crs("EPSG:3035")
    return gdf


def _commune_dataset(year: int, cache_dir: Path = _CACHE) -> gpd.GeoDataFrame:
    """Load a pre-2011 GISCO *communes* vintage, keyed by BLUE_DB gisco_id.

    The raw shapefile is normalised once and the slim result (IDs + geometry)
    is cached as a GeoPackage, so later calls skip the reprojection and the
    per-country ID rewriting.  Rows flagged ``is_alias`` carry a fallback ID
    (see :func:`_commune_alias_ids`) and are ignored by :meth:`all_geometries`.
    """
    stem  = _COMM_FILES[year]
    slim  = cache_dir / f"{stem}_blue.gpkg"
    if slim.exists():
        return gpd.read_file(slim)

    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = cache_dir / f"{stem}.shp.zip"
    if not raw.exists():
        url = f"{_GISCO_COMM}shp/{stem}.shp.zip"
        print(f"    Downloading {url} …", flush=True)
        r = requests.get(url, timeout=600)
        r.raise_for_status()
        raw.write_bytes(r.content)

    # The shapefiles' .dbf encoding varies between vintages (2004 is latin-1,
    # 2008 is UTF-8).  Only the ASCII code columns are used here, so decoding
    # everything as latin-1 is safe and never raises.
    gdf = gpd.read_file(f"zip://{raw}", encoding="latin1")
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:3035")
    elif gdf.crs.to_epsg() != 3035:
        gdf = gdf.to_crs("EPSG:3035")

    code_col = "COMM_CODE" if "COMM_CODE" in gdf.columns else "COMM_ID"
    gdf["gisco_id"] = [
        _commune_gisco_id(cc, code, year)
        for cc, code in zip(gdf["CNTR_CODE"], gdf[code_col])
    ]
    gdf = gdf[gdf["gisco_id"].notna()]
    gdf = gdf[~gdf["gisco_id"].str.endswith("_UNK")]
    # Swiss lakes are separate polygons in GISCO but not BLUE_DB units.
    gdf = gdf[~gdf["gisco_id"].str.startswith("CH_9")]

    # 2001 attributes each commune's coastal water as its own polygon
    # (SURF_COVR "S"); dropping it keeps the land boundary, unless that is all
    # a unit has.  Inland water ("W") is genuine commune territory.
    if "SURF_COVR" in gdf.columns:
        land = gdf[gdf["SURF_COVR"] != "S"]
        sea_only = gdf[~gdf["gisco_id"].isin(set(land["gisco_id"]))]
        gdf = pd.concat([land, sea_only])

    primary = _dissolve_by_id(gdf)
    primary["is_alias"] = False

    # Fallback IDs.  One never shadows a primary ID; where several units share
    # one (the Paris arrondissements all carry the NSI code of Paris) their
    # geometries are merged, which is exactly what such an ID denotes.
    alias_rows: dict[str, list] = {}
    if "NSI_CODE" in gdf.columns:
        known = set(primary["gisco_id"])
        for cc, nsi, gid in zip(gdf["CNTR_CODE"], gdf["NSI_CODE"], gdf["gisco_id"]):
            for alias in _commune_alias_ids(cc, nsi):
                if alias != gid and alias not in known:
                    alias_rows.setdefault(alias, []).append(gid)

    parts = [primary]
    if alias_rows:
        by_gid = primary.set_index("gisco_id")["geometry"]
        aliases = gpd.GeoDataFrame(
            {"gisco_id": list(alias_rows),
             "geometry": [by_gid[gids].union_all() if len(gids) > 1
                          else by_gid[gids[0]]
                          for gids in alias_rows.values()],
             "is_alias": True},
            crs="EPSG:3035",
        )
        parts.append(aliases)

    out = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:3035")
    out.to_file(slim, driver="GPKG")
    return out


def _dissolve_by_id(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """One row per ``gisco_id``, merging the few multi-polygon units."""
    dup = gdf["gisco_id"].duplicated(keep=False)
    single = gdf.loc[~dup, ["gisco_id", "geometry"]].copy()
    if not dup.any():
        return single.reset_index(drop=True)
    merged = gdf.loc[dup, ["gisco_id", "geometry"]].dissolve(by="gisco_id",
                                                            as_index=False)
    return gpd.GeoDataFrame(pd.concat([single, merged], ignore_index=True),
                            crs=gdf.crs)


@lru_cache(maxsize=6)
def _muni_vintage(y: int, cache_dir: Path) -> gpd.GeoDataFrame:
    """Load one exact municipal vintage (see :func:`_muni_dataset`)."""
    if y in _COMM_FILES:
        return _commune_dataset(y, cache_dir)
    fname = f"LAU_RG_01M_{y}_3035.geojson"
    url   = f"{_GISCO_LAU}geojson/{fname}"
    gdf   = _fetch_geojson(url, cache_dir, fname)
    if "GISCO_ID" in gdf.columns:
        gdf = gdf.rename(columns={"GISCO_ID": "gisco_id"})
    gdf = gdf[["gisco_id", "geometry"]].copy()
    gdf["is_alias"] = False
    return gdf


@lru_cache(maxsize=6)
def _muni_index_of(y: int, cache_dir: Path) -> dict:
    return _muni_vintage(y, cache_dir).set_index("gisco_id")["geometry"].to_dict()


def _muni_dataset(year: int, cache_dir: Path = _CACHE) -> gpd.GeoDataFrame:
    """Municipal boundaries for the vintage closest to (and ≤) *year*.

    Uses the GISCO LAU distribution from 2011 on and the older communes
    distribution before that.  The last two vintages stay in memory: a single
    :meth:`BlueGeo.attach` call typically walks many elections of the same era,
    and these datasets are large enough that re-reading them per election
    dominates the runtime.  Callers must treat the result as read-only.
    """
    return _muni_vintage(_best_year(year, _MUNI_YEARS), cache_dir)


def _muni_index(year: int, cache_dir: Path = _CACHE) -> dict:
    """``gisco_id`` → geometry for the municipal vintage covering *year*."""
    return _muni_index_of(_best_year(year, _MUNI_YEARS), cache_dir)


def _union(geometries) -> object | None:
    """Dissolve *geometries*, repairing them if GEOS refuses.

    Simplified boundaries self-intersect often enough that a plain union of a
    few dozen countries raises "side location conflict", which would otherwise
    lose the whole constituency rather than one country.  Repairing is only
    attempted after a failure, so the common case pays nothing.
    """
    series = gpd.GeoSeries([g for g in geometries if g is not None])
    if series.empty:
        return None
    try:
        return series.union_all()
    except Exception:
        try:
            return series.make_valid().union_all()
        except Exception as exc:
            warnings.warn(f"Could not dissolve {len(series)} geometries: {exc}")
            return None


@lru_cache(maxsize=None)
def _muni_id_set(year: int, cache_dir: Path = _CACHE) -> frozenset[str]:
    """The identifiers a municipal vintage contains — without its geometry.

    Answering "which vintage has this ID?" by loading vintages is why the
    fallback below used to stop after three of them: each tile is tens of
    megabytes and only a few fit in memory at once.  The identifiers alone are
    a few hundred kilobytes, cache to disk, and are all the search needs — the
    polygon is fetched later, from the one vintage that turned out to have it.
    """
    path = Path(cache_dir) / f"ids_muni_{year}.txt"
    if path.exists():
        try:
            return frozenset(path.read_text(encoding="utf-8").split())
        except Exception:                             # unreadable → rebuild it
            pass
    ids = frozenset(_muni_index(year, cache_dir))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(sorted(ids)), encoding="utf-8")
    except Exception as exc:
        warnings.warn(f"Could not cache ID list for vintage {year}: {exc}")
    return ids


@lru_cache(maxsize=16)
def _nuts_dataset(year: int, level: int, cache_dir: Path = _CACHE) -> gpd.GeoDataFrame:
    """NUTS boundaries at *level* for the vintage covering *year*.

    Memoised: the files are large (26 MB at level 3) and re-parsing one per
    lookup dominated the cost of resolving special units, which consult levels
    3 down to 0.  Callers must treat the result as read-only.
    """
    y     = _best_year(year, _NUTS_YEARS)
    fname = f"NUTS_RG_01M_{y}_3035_LEVL_{level}.geojson"
    url   = f"{_GISCO_NUTS}geojson/{fname}"
    gdf   = _fetch_geojson(url, cache_dir, fname)
    if "NUTS_ID" in gdf.columns:
        gdf = gdf.rename(columns={"NUTS_ID": "gisco_id"})
    return gdf[["gisco_id", "geometry"]]


@lru_cache(maxsize=4)
def _countries_dataset(year: int, cache_dir: Path = _CACHE) -> gpd.GeoDataFrame:
    """World country boundaries for the vintage covering *year*.

    NUTS stops at Europe, so an expatriate unit hosted anywhere else — a third
    of Bulgaria's abroad units are in the Americas, Asia or Australia — had no
    polygon at all.  GISCO's *countries* distribution covers the world with the
    same identifiers (ISO 3166-1 alpha-2, with GISCO's ``EL``/``UK``), so those
    units can be drawn as the country their voters live in.

    Coarser than the European layers on purpose: these polygons are only ever
    shown alone, as a "voters abroad" layer, never beside municipalities, and
    20M keeps the download at ~2 MB instead of ~50.
    """
    y     = _best_year(year, _CNTR_YEARS)
    fname = f"CNTR_RG_20M_{y}_4326.geojson"
    url   = f"{_GISCO_CNTR}geojson/{fname}"
    gdf   = _fetch_geojson(url, cache_dir, fname)
    # The 2001 vintage names the code column differently and carries one row
    # per landmass rather than per country, so it is merged back together.
    for column in ("CNTR_ID", "CNTR_CODE"):
        if column in gdf.columns:
            gdf = gdf.rename(columns={column: "gisco_id"})
            break
    gdf = gdf[["gisco_id", "geometry"]]
    return _dissolve_by_id(gdf) if gdf["gisco_id"].duplicated().any() else gdf


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class BlueGeo:
    """Geometry resolver for BLUE_DB electoral data.

    Parameters
    ----------
    maps_dir : path to the ``maps/`` directory (default: auto-detected).
    cache_dir : directory for downloaded GISCO tiles (default: ``.cache/geo/``).
    """

    def __init__(
        self,
        maps_dir: str | Path | None = None,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.maps_dir  = _resolve_maps_dir(maps_dir)
        self.cache_dir = _resolve_cache_dir(cache_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def geometries(
        self,
        gisco_ids: Collection[str],
        year: int,
        election_type: str = "national",
        country_code: str | None = None,
    ) -> gpd.GeoDataFrame:
        """
        Return a GeoDataFrame (EPSG:3035) with one row per resolved *gisco_id*.

        Parameters
        ----------
        gisco_ids : iterable of BLUE_DB geographic unit IDs or None (= all)
        year : election year (used to select the right GISCO dataset version
               and, for EP elections, the right maps/ override).
        election_type : ``"national"`` or ``"european"``.
        country_code : 2-letter ISO code; used to look up maps/ overrides.
        """
        refs = self.resolve_sources(gisco_ids, year, election_type, country_code)
        found = {gid: ref for gid, ref in refs.items() if ref is not None}

        missing = [gid for gid, ref in refs.items() if ref is None]
        if missing:
            warnings.warn(
                f"Could not find geometry for {len(missing)} gisco_id(s): "
                f"{sorted(missing)[:10]}"
            )

        rows = [(gid, geom) for gid, ref in found.items()
                if (geom := self.geometry_of(ref)) is not None]
        if not rows:
            return gpd.GeoDataFrame({"gisco_id": [], "geometry": []},
                                    crs="EPSG:3035")
        return gpd.GeoDataFrame(
            {"gisco_id": [r[0] for r in rows], "geometry": [r[1] for r in rows]},
            crs="EPSG:3035",
        )

    def resolve_sources(
        self,
        gisco_ids: Collection[str],
        year: int,
        election_type: str = "national",
        country_code: str | None = None,
    ) -> dict[str, "SourceRef | None"]:
        """Map each *gisco_id* to the polygon it is drawn with, or ``None``.

        The single place where the question "which polygon is this unit?" is
        answered — :meth:`geometries` is this plus a lookup of each reference,
        and ``build_geo_lookup.py`` is this written to a file.  A unit that
        resolves to nothing is a hole in the map, and is reported as such
        rather than silently dropped.
        """
        ids = list(dict.fromkeys(gisco_ids))
        if not ids:
            return {}

        # Infer country code from IDs if not provided
        if country_code is None:
            cc_candidates = {gid[:2] for gid in ids if len(gid) >= 2}
            country_code = cc_candidates.pop() if len(cc_candidates) == 1 else None

        resolved: dict[str, SourceRef] = {}
        remaining = set(ids)

        # 0. hand-maintained overrides — always win, so a wrong automatic match
        #    can be corrected without weakening the rule that produced it
        for gid in list(remaining):
            ref = geo_override(gid, year, election_type)
            if ref is not None:
                resolved[gid] = ref
                remaining.discard(gid)

        # 1. maps/ overrides
        if remaining:
            remaining = self._resolve_from_maps(
                remaining, resolved, election_type, country_code, year
            )

        # 2. GISCO LAU, at the vintage that actually holds this country's level
        if remaining:
            remaining = self._resolve_from_lau(
                remaining, resolved, effective_vintage(country_code, year))

        # 2b. units of another territory reported with this election -- the
        #     Faroe Islands and Greenland in the Danish Folketing elections --
        #     are drawn from that territory's own sources
        if remaining and country_code:
            foreign = defaultdict(list)
            for gid in remaining:
                if len(gid) > 3 and gid[2] == "_" and gid[:2] != country_code:
                    foreign[gid[:2]].append(gid)
            for cc, gids in foreign.items():
                for gid, ref in self.resolve_sources(gids, year, election_type, cc).items():
                    if ref is not None:
                        resolved[gid] = ref
                        remaining.discard(gid)

        # 3. GISCO NUTS (levels 3 → 0)
        if remaining:
            remaining = self._resolve_from_nuts(remaining, resolved, year)

        # 4. Constituencies made of whole foreign countries, drawn as the union
        #    of their members.  Last, so a unit that has a real polygon keeps
        #    it, and computed against the whole election so that "remaining
        #    countries" means what it says.
        if remaining:
            vintage = _best_year(year, _CNTR_YEARS)
            for gid in list(remaining):
                members = foreign_members(gid, ids)
                if members:
                    resolved[gid] = SourceRef("cntrset", str(vintage),
                                              "+".join(members))
                    remaining.discard(gid)

        return {gid: resolved.get(gid) for gid in ids}

    def geometry_of(self, ref: "SourceRef"):
        """The polygon a :class:`SourceRef` points at, or ``None`` if it is gone.

        A reference can go stale — a maps/ file renamed, a GISCO vintage that
        no longer carries an ID — so this returns ``None`` instead of raising:
        one bad row in the override file must not take a whole map down.
        """
        try:
            if ref.kind == "maps":
                path = _maps_path(self.maps_dir, self.cache_dir, ref.layer)
                if not path.exists():
                    return None
                gdf = _read_maps_shp(path)
                hit = gdf[gdf["gisco_id"] == ref.source_id]
                return hit.iloc[0].geometry if len(hit) else None
            if ref.kind == "mapsset":
                # Several features of one shapefile drawn as one unit, for a
                # constituency that merged two of its predecessors.
                path = _maps_path(self.maps_dir, self.cache_dir, ref.layer)
                if not path.exists():
                    return None
                gdf = _read_maps_shp(path)
                wanted = set(ref.source_id.split("+"))
                return _union(gdf[gdf["gisco_id"].isin(wanted)].geometry)
            if ref.kind == "lau":
                return _muni_index(int(ref.layer), self.cache_dir).get(ref.source_id)
            if ref.kind == "lauset":
                #vintage, _, level = ref.layer.partition(":")
                #nuts = _nuts_dataset(int(vintage), int(level), self.cache_dir)
                wanted = set(ref.source_id.split("+"))
                laus = _muni_index(int(ref.layer), self.cache_dir)
                return _union(laus.get(l) for l in wanted)
            if ref.kind == "nuts":
                vintage, _, level = ref.layer.partition(":")
                nuts = _nuts_dataset(int(vintage), int(level), self.cache_dir)
                hit = nuts[nuts["gisco_id"] == ref.source_id]
                return hit.iloc[0].geometry if len(hit) else None
            if ref.kind == "nutsset":
                vintage, _, level = ref.layer.partition(":")
                nuts = _nuts_dataset(int(vintage), int(level), self.cache_dir)
                wanted = set(ref.source_id.split("+"))
                return _union(nuts[nuts["gisco_id"].isin(wanted)].geometry)
            if ref.kind == "cntr":
                cntr = _countries_dataset(int(ref.layer), self.cache_dir)
                hit = cntr[cntr["gisco_id"] == ref.source_id]
                return hit.iloc[0].geometry if len(hit) else None
            if ref.kind == "cntrset":
                cntr = _countries_dataset(int(ref.layer), self.cache_dir)
                wanted = set(ref.source_id.split("+"))
                return _union(cntr[cntr["gisco_id"].isin(wanted)].geometry)
        except Exception as exc:
            warnings.warn(f"Could not load geometry for {ref}: {exc}")
        return None

    def all_geometries(
        self,
        year: int,
        election_type: str = "national",
        countries: Collection[str] | str | None = None,
    ) -> gpd.GeoDataFrame:
        """
        Return *all* available geometries for a given election context.

        Unlike :meth:`geometries`, you do not supply a list of IDs — the
        function loads the full GISCO LAU (or maps/ override) dataset and
        returns every unit, optionally filtered by country.  Useful as a
        background layer or when you want to show every municipality regardless
        of whether you have election data for it.

        Parameters
        ----------
        year : election year (selects the GISCO dataset vintage and the
               correct maps/ EP override when *election_type* is
               ``"european"``).
        election_type : ``"national"`` (default) or ``"european"``.
        countries : one or more 2-letter ISO country codes to include.
                    ``None`` returns all countries in the dataset.

        Returns
        -------
        gpd.GeoDataFrame with columns ``gisco_id``, ``country_code``,
        ``geometry`` (CRS EPSG:3035).
        """
        if isinstance(countries, str):
            countries = [countries]
        cc_set: set[str] | None = set(countries) if countries is not None else None

        parts: list[gpd.GeoDataFrame] = []

        # ── maps/ overrides ────────────────────────────────────────────
        # Countries that have a maps/ file get their geometries from there
        # (higher priority than GISCO, e.g. IS, MT, SI, IE).
        maps_ccs: set[str] = set()
        if election_type == "european":
            best_eu: dict[str, Path] = {}
            for shp in _derived_glob(self.maps_dir, self.cache_dir, "EU_??_*.shp"):
                m = re.match(r"EU_([A-Z]{2})_(\d{4})\.shp$", shp.name)
                if not m:
                    continue
                cc, syear = m.group(1), int(m.group(2))
                if cc_set is not None and cc not in cc_set:
                    continue
                # The latest file whose year ≤ requested: glob is sorted
                # ascending, so a later (closer) match replaces an earlier one.
                if syear <= year:
                    best_eu[cc] = shp
            for cc, shp in sorted(best_eu.items()):
                gdf = _read_maps_shp(shp).copy()
                gdf["country_code"] = cc
                parts.append(gdf)
                maps_ccs.add(cc)
        else:
            # Collect best year-specific file per CC (pattern: {CC}_{YEAR}.shp)
            best_versioned: dict[str, Path] = {}
            for shp in _derived_glob(self.maps_dir, self.cache_dir,
                                     "[A-Z][A-Z]_[0-9][0-9][0-9][0-9].shp"):
                m = re.match(r"([A-Z]{2})_(\d{4})\.shp$", shp.name)
                if not m:
                    continue
                cc, syear = m.group(1), int(m.group(2))
                if cc_set is not None and cc not in cc_set:
                    continue
                if syear <= year:
                    best_versioned[cc] = shp  # sorted ascending → last ≤ year wins
            # Fall back to plain {CC}.shp for countries not covered above
            for shp in _derived_glob(self.maps_dir, self.cache_dir, "[A-Z][A-Z].shp"):
                cc = shp.stem
                if cc_set is not None and cc not in cc_set:
                    continue
                if cc not in best_versioned:
                    best_versioned[cc] = shp
            for cc, shp in sorted(best_versioned.items()):
                gdf = _read_maps_shp(shp).copy()
                gdf["country_code"] = cc
                parts.append(gdf)
                maps_ccs.add(cc)

        # ── GISCO municipalities (LAU, or communes before 2011) ────────
        try:
            lau = _muni_dataset(year, self.cache_dir)
            lau = lau[~lau["is_alias"]]
        except Exception as exc:
            warnings.warn(f"Could not fetch LAU dataset: {exc}")
            lau = gpd.GeoDataFrame(columns=["gisco_id", "geometry"])

        if not lau.empty:
            if "CNTR_CODE" in lau.columns:
                lau = lau.rename(columns={"CNTR_CODE": "country_code"})
            elif "country_code" not in lau.columns:
                lau["country_code"] = lau["gisco_id"].str.split("_").str[0]

            # Exclude countries that were already covered by maps/ overrides
            lau_filtered = lau[~lau["country_code"].isin(maps_ccs)].copy()
            if cc_set is not None:
                lau_filtered = lau_filtered[lau_filtered["country_code"].isin(cc_set)]

            parts.append(lau_filtered[["gisco_id", "country_code", "geometry"]])

        if not parts:
            return gpd.GeoDataFrame(
                {"gisco_id": [], "country_code": [], "geometry": []},
                crs="EPSG:3035",
            )

        return gpd.GeoDataFrame(
            pd.concat(parts, ignore_index=True),
            crs="EPSG:3035",
        )

    # ------------------------------------------------------------------
    # One-shot join onto a results frame
    # ------------------------------------------------------------------

    # Continental-Europe bounding box in EPSG:3035 (minx, miny, maxx, maxy).
    # Keeps the mainland and the Mediterranean islands (Sicily, Sardinia,
    # Corsica, Malta, Cyprus) but excludes far overseas territories, which in
    # national maps sit at their true, globe-spanning coordinates (e.g. the
    # French Antilles or Reunion) and would otherwise dominate the extent.
    _CONTINENTAL_BBOX = (2.4e6, 1.2e6, 7.6e6, 5.6e6)

    def attach(
        self,
        results: pd.DataFrame,
        year: int | None = None,
        election_type: str | None = None,
        continental: bool = True,
    ) -> gpd.GeoDataFrame:
        """Return *results* as a plottable GeoDataFrame (EPSG:3035).

        Resolves the map geometry of every row's geographic unit and joins it
        onto the frame, so a map is a single call::

            res = db.results(countries=["FR", "DE", "IT"],
                             election_type="national", latest_per_country=True,
                             geo_level="LAU", aggregate_by="ideology")
            geo.attach(db.vote_shares(res)).plot(column="radical right_pct")

        *results* is a frame returned by :meth:`BlueDB.results`. Each unit is
        resolved at the boundary vintage of its own election (taken from the
        ``year`` column) and, when present, its ``election_type`` and
        ``country_code`` -- so a table spanning several countries and years,
        each with its own vintage, is handled in one go without any manual
        concatenation. Pass *year* / *election_type* to force a single
        reference vintage or contest type for all rows.

        With *continental* (the default), units outside continental Europe and
        its Mediterranean islands are dropped, so overseas territories do not
        blow up the map extent; pass ``continental=False`` to keep them. Rows
        whose geometry cannot be resolved are dropped, with a warning giving
        the count.
        """
        df = results.reset_index(drop=True).copy()
        id_col = "geo_id" if "geo_id" in df.columns else "gisco_id"
        if id_col not in df.columns:
            raise ValueError("results must have a 'geo_id' or 'gisco_id' column")

        # Group rows that share a (vintage year, contest type, country) so each
        # group is resolved with a single geometries() call at the right vintage.
        year_col = "year" if (year is None and "year" in df.columns) else None
        et_col = "election_type" if (election_type is None
                                     and "election_type" in df.columns) else None
        cc_col = "country_code" if "country_code" in df.columns else None
        keys = [k for k in (year_col, et_col, cc_col) if k]

        # Geometry per row of its own group: a unit present in elections of
        # different vintages keeps each election's boundary.
        df["geometry"] = None
        groups = df.groupby(keys, sort=False) if keys else [((), df)]
        for gk, sub in groups:
            info = dict(zip(keys, gk if isinstance(gk, tuple) else (gk,)))
            yr = int(info[year_col]) if year_col else year
            et = str(info[et_col]) if et_col else (election_type or "national")
            cc = str(info[cc_col]) if cc_col else None
            g = self.geometries(sub[id_col].astype(str), year=int(yr),
                                election_type=et, country_code=cc)
            geom_by_id = dict(zip(g["gisco_id"], g["geometry"]))
            df.loc[sub.index, "geometry"] = sub[id_col].astype(str).map(geom_by_id)
        missing = int(df["geometry"].isna().sum())
        if missing:
            warnings.warn(f"attach: could not resolve geometry for {missing} "
                          f"of {len(df)} rows")
        gdf = gpd.GeoDataFrame(df[df["geometry"].notna()].copy(),
                               geometry="geometry", crs="EPSG:3035")

        if continental and len(gdf):
            minx, miny, maxx, maxy = self._CONTINENTAL_BBOX
            c = gdf.geometry.centroid
            gdf = gdf[(c.x >= minx) & (c.x <= maxx)
                      & (c.y >= miny) & (c.y <= maxy)].copy()
        return gdf

    # ------------------------------------------------------------------
    # Internal resolvers
    # ------------------------------------------------------------------

    def _resolve_from_maps(
        self,
        remaining: set[str],
        resolved: dict,
        election_type: str,
        country_code: str | None,
        year: int,
    ) -> set[str]:
        """Try maps/ shapefiles; return still-unresolved IDs.

        When *country_code* is None (multi-country call), the country is
        inferred from each gisco_id's two-letter prefix so that every maps/
        override is tried automatically.
        """
        if country_code is not None:
            ccs = [country_code]
        else:
            # Collect unique country prefixes present in remaining IDs
            ccs = sorted({gid.split("_")[0] for gid in remaining
                          if "_" in gid and len(gid.split("_")[0]) == 2})

        for cc in ccs:
            if not remaining:
                break
            if election_type == "european":
                # EP contests use their own file when one exists (Ireland votes
                # in constituencies), but otherwise the same units as national
                # ones, so fall back rather than resolving nothing.
                path = _maps_eu(cc, year) or _maps_national(cc, year)
            else:
                path = _maps_national(cc, year)
            if path is None:
                continue
            gdf = _read_maps_shp(path)
            hit = gdf[gdf["gisco_id"].isin(remaining)]
            for gid in hit["gisco_id"]:
                resolved[gid] = SourceRef("maps", path.name, gid)
            remaining = remaining - set(hit["gisco_id"])

        return remaining

    def _resolve_from_lau(
        self, remaining: set[str], resolved: dict, year: int
    ) -> set[str]:
        """Try the GISCO municipal layer; return still-unresolved IDs.

        A pre-2011 election is resolved against the communes vintage of its own
        era first; whatever that vintage does not cover falls back to the
        earliest LAU dataset, which is all this method used to consult.

        Also applies known ID-format normalisations before the lookup:
        - Liechtenstein: ``LI_00001001`` → ``LI_LI00001001``
          (GISCO prefixes the national code a second time).
        """
        # The election's own vintage first, so a unit that existed then is drawn
        # as it was then.  Whatever it does not cover is looked up in the
        # nearest other vintages: GISCO occasionally publishes a country at the
        # wrong level for a run of years — Denmark is parishes (~2,150 units)
        # from 2010 to 2016 and kommuner (99) either side — which would
        # otherwise leave those elections with no geometry at all.  Only
        # still-unresolved IDs fall through, so units that do resolve at their
        # own vintage keep that vintage's boundaries.
        vintages = [year]
        if _best_year(year, _MUNI_YEARS) in _COMM_FILES:
            vintages.append(_LAU_YEARS[0])
        vintages += sorted((y for y in _MUNI_YEARS if y not in vintages),
                           key=lambda y: (abs(y - year), -y))[:_MAX_FALLBACK_VINTAGES]

        still_remaining = set(remaining)
        own_resolved = 0
        for n, y in enumerate(vintages):
            if not still_remaining:
                break
            # Scanning every vintage for an ID that exists in none of them is
            # expensive (each is a large index), and pointless: a country
            # published at the wrong level fails wholesale — Denmark resolved 0
            # of 99 — whereas a few stragglers are ordinary boundary churn.  So
            # once the election's own vintage has accounted for nearly all the
            # units, stop rather than chase the remainder across two decades.
            if n == 1:
                own_resolved = len(remaining) - len(still_remaining)
                if own_resolved >= _FALLBACK_THRESHOLD * len(remaining):
                    break
            if abs(y - year) > _MAX_VINTAGE_DISTANCE and y not in (year, _LAU_YEARS[0]):
                continue
            try:
                index = _muni_index(y, self.cache_dir)
            except Exception as exc:
                warnings.warn(f"Could not fetch LAU dataset {y}: {exc}")
                continue
            for gid in list(still_remaining):
                alt = _normalize_lau_id(gid)
                if gid in index:
                    resolved[gid] = SourceRef("lau", str(y), gid)
                    still_remaining.discard(gid)
                elif alt is not None and alt in index:
                    resolved[gid] = SourceRef("lau", str(y), alt)
                    still_remaining.discard(gid)

        # Whatever the vintages above did not cover is searched across *every*
        # vintage, using the identifier lists rather than the tiles themselves.
        # This is what catches the long tail of boundary churn: a commune that
        # merged away in 2013 still exists in the 2011 tile, and a country
        # published at the wrong level for a run of years — Denmark is parishes
        # from 2010 to 2016, which left its 2014 EP map completely empty — is
        # found in the nearest vintage that has it.  Nearest first, so a unit
        # is drawn as close to its own era as the data allows.
        for gid in list(still_remaining):
            alt = _normalize_lau_id(gid)
            for y in sorted(_MUNI_YEARS, key=lambda v: (abs(v - year), -v)):
                try:
                    ids = _muni_id_set(y, self.cache_dir)
                except Exception:                     # a vintage we cannot get
                    continue
                match = gid if gid in ids else (alt if alt and alt in ids else None)
                if match is not None:
                    resolved[gid] = SourceRef("lau", str(y), match)
                    still_remaining.discard(gid)
                    break

        return still_remaining

    def _resolve_from_nuts(
        self, remaining: set[str], resolved: dict, year: int
    ) -> set[str]:
        """Try GISCO NUTS datasets (levels 3→0); return still-unresolved IDs."""
        for level in (3, 2, 1, 0):
            if not remaining:
                break
            try:
                nuts = _nuts_dataset(year, level, self.cache_dir)
            except Exception as exc:
                warnings.warn(f"Could not fetch NUTS{level} dataset: {exc}")
                continue
            hit = nuts[nuts["gisco_id"].isin(remaining)]
            for gid in hit["gisco_id"]:
                resolved[gid] = SourceRef("nuts", f"{year}:{level}", gid)
            remaining -= set(hit["gisco_id"])

        # Voters abroad: the host country's own boundary.  A national boundary
        # is stable, but its *code* is not and its presence in NUTS is not
        # either — Greece is GR in the older vintages and EL in the newer ones,
        # and the United Kingdom leaves the dataset altogether after Brexit —
        # so both spellings are tried, and older vintages are consulted for a
        # host the election's own vintage no longer carries.
        if remaining:
            hosts = {gid: h for gid in remaining if (h := abroad_host(gid))}
            aliases = {h: list(dict.fromkeys(
                           [h] + _HISTORIC_COUNTRIES.get(h, [])
                           + [k for k, v in _ISO_TO_NUTS.items() if v == h]))
                       for h in set(hosts.values())}
            for vintage in [year] + sorted(_NUTS_YEARS, reverse=True):
                if not hosts:
                    break
                try:
                    index = set(_nuts_dataset(vintage, 0, self.cache_dir)["gisco_id"])
                except Exception as exc:
                    warnings.warn(f"Could not fetch NUTS0 {vintage}: {exc}")
                    continue
                for gid, host in list(hosts.items()):
                    for code in aliases[host]:
                        if code in index:
                            resolved[gid] = SourceRef("nuts", f"{vintage}:0", code)
                            remaining.discard(gid)
                            del hosts[gid]
                            break

            # Hosts outside Europe are not in NUTS at all: fall back to the
            # world country layer, which uses the same two-letter codes.
            # The election's own vintage first, then the others: a country can
            # be absent from one and present in another, either because it did
            # not exist yet (South Sudan) or no longer does (Serbia and
            # Montenegro), or because GISCO simply added it later (Taiwan, the
            # Holy See).  A national boundary is stable enough that any vintage
            # carrying it is a better answer than no polygon at all.
            own = _best_year(year, _CNTR_YEARS)
            for vintage in [own] + sorted(_CNTR_YEARS, reverse=True):
                if not hosts:
                    break
                try:
                    index = set(_countries_dataset(vintage, self.cache_dir)["gisco_id"])
                except Exception as exc:
                    warnings.warn(f"Could not fetch country boundaries {vintage}: {exc}")
                    continue
                for gid, host in list(hosts.items()):
                    for code in aliases[host]:
                        if code in index:
                            resolved[gid] = SourceRef("cntr", str(vintage), code)
                            remaining.discard(gid)
                            del hosts[gid]
                            break

        # Last resort: a special unit that covers its whole NUTS region can be
        # drawn as that region.  Only applied when the unit is alone in it, so
        # several constituencies never collapse onto one shared polygon.
        if remaining:
            wanted = {gid: p for gid in remaining if (p := _special_parent(gid))}
            # Count every unit of the election that belongs to the region, not
            # just the ones still missing: Kerry North and Kerry South share
            # NUTS 3 IE053, and drawing the first as the whole of Kerry would
            # bury the second, which already has its own polygon.
            counts: dict[str, int] = {}
            for gid in set(resolved) | remaining:
                parent = _special_parent(gid)
                if parent:
                    counts[parent] = counts.get(parent, 0) + 1
            singles = {gid: p for gid, p in wanted.items() if counts[p] == 1}
            if singles:
                # Once anything else in the election has a polygon, the coarse
                # levels are off limits: Ireland is a single NUTS 1 region, so
                # an abolished constituency falling back to IE0 drew the whole
                # island on top of its 35 neighbours.  Malta's one unit really
                # is the whole country, and still reaches NUTS 2 because
                # nothing else resolved beside it.
                levels = (3, 2) if resolved else (3, 2, 1, 0)
                # A parent is recorded in current NUTS coding, which an older
                # vintage may not have — Ireland's IE051 postdates the 2011
                # tiles — so other vintages are consulted rather than letting
                # the walk fall through to a region that swallows the map.
                vintages = [year] + sorted(_NUTS_YEARS, reverse=True)
                for level, vintage in ((l, v) for l in levels for v in vintages):
                    if not singles:
                        break
                    try:
                        nuts = _nuts_dataset(vintage, level, self.cache_dir)
                    except Exception:
                        continue
                    index = set(nuts["gisco_id"])
                    for gid, parent in list(singles.items()):
                        # A parent may be a placeholder for "the whole country
                        # undivided" that GISCO never publishes — Malta's is
                        # MT000, where the real NUTS 3 codes are MT001/MT002 —
                        # so walk up the hierarchy by trimming the code.
                        #
                        # A unit with no geometry leaves a hole, which is
                        # honest and small; one covering the entire map is
                        # neither — hence the level restriction above.
                        for code in dict.fromkeys(
                                [parent, parent[:4], parent[:3], parent[:2]]):
                            if code and code in index:
                                resolved[gid] = SourceRef(
                                    "nuts", f"{vintage}:{level}", code)
                                remaining.discard(gid)
                                del singles[gid]
                                break
        return remaining
