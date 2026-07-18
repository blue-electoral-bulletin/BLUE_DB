"""
blue_maps.py
============
Geometry resolution for BLUE_DB electoral data, returning GeoDataFrames in
EPSG:3035 (ETRS89-LAEA Europe) suitable for choropleth mapping.

Resolution priority for each *gisco_id*:

1. **maps/ overrides** – local shapefiles for special constituencies.
   - ``maps/{CC}.shp``         → national LAU-level overrides (e.g. IE, MT, IS).
   - ``maps/EU_{CC}_{YEAR}.shp`` → EP election constituency boundaries.
2. **GISCO LAU** – ``LAU_RG_01M_{year}_3035.geojson`` downloaded from
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
import re
import warnings
from pathlib import Path
from typing import Collection, Sequence

import geopandas as gpd
import numpy as np
import pandas as pd
import requests


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_LIB_DIR = Path(__file__).parent
_ROOT    = _LIB_DIR.parent
_MAPS    = _ROOT / "maps"
_CACHE   = _ROOT / ".cache" / "geo"

# ---------------------------------------------------------------------------
# GISCO base URLs
# ---------------------------------------------------------------------------
_GISCO_LAU  = "https://gisco-services.ec.europa.eu/distribution/v2/lau/"
_GISCO_NUTS = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/"

# Available dataset years from GISCO (ascending)
_LAU_YEARS  = [2011,2012,2013,2014,2015,2016,2017,2018,2019,2020,2021,2022,2023,2024]
_NUTS_YEARS = [2003,2006,2010,2013,2016,2021,2024]



# ---------------------------------------------------------------------------
# Dataset year helpers
# ---------------------------------------------------------------------------

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
        candidates = sorted(_MAPS.glob(f"{cc}_[0-9][0-9][0-9][0-9].shp"))
        if candidates:
            selected = None
            for p in candidates:
                m = re.search(r"_(\d{4})\.shp$", p.name)
                if m and int(m.group(1)) <= year:
                    selected = p
            if selected:
                return selected
    p = _MAPS / f"{cc}.shp"
    return p if p.exists() else None


def _maps_eu(cc: str, year: int) -> Path | None:
    """Return the maps/EU_{CC}_{YEAR}.shp closest to *year* if it exists."""
    candidates = sorted(_MAPS.glob(f"EU_{cc}_*.shp"))
    if not candidates:
        return None
    # pick the latest one whose year ≤ requested year
    selected = None
    for p in candidates:
        m = re.search(r"_(\d{4})\.shp$", p.name)
        if m and int(m.group(1)) <= year:
            selected = p
    return selected or candidates[0]


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
    return None


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


def _lau_dataset(year: int, cache_dir: Path = _CACHE) -> gpd.GeoDataFrame:
    y = _best_year(year, _LAU_YEARS)
    fname = f"LAU_RG_01M_{y}_3035.geojson"
    url   = f"{_GISCO_LAU}geojson/{fname}"
    gdf   = _fetch_geojson(url, cache_dir, fname)
    if "GISCO_ID" in gdf.columns:
        gdf = gdf.rename(columns={"GISCO_ID": "gisco_id"})
    return gdf[["gisco_id", "geometry"]]


def _nuts_dataset(year: int, level: int, cache_dir: Path = _CACHE) -> gpd.GeoDataFrame:
    y     = _best_year(year, _NUTS_YEARS)
    fname = f"NUTS_RG_01M_{y}_3035_LEVL_{level}.geojson"
    url   = f"{_GISCO_NUTS}geojson/{fname}"
    gdf   = _fetch_geojson(url, cache_dir, fname)
    if "NUTS_ID" in gdf.columns:
        gdf = gdf.rename(columns={"NUTS_ID": "gisco_id"})
    return gdf[["gisco_id", "geometry"]]


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
        self.maps_dir  = Path(maps_dir)  if maps_dir  else _MAPS
        self.cache_dir = Path(cache_dir) if cache_dir else _CACHE

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
        ids = list(gisco_ids)
        if not ids:
            return gpd.GeoDataFrame({"gisco_id": [], "geometry": []},
                                    crs="EPSG:3035")

        # Infer country code from IDs if not provided
        if country_code is None:
            cc_candidates = {gid[:2] for gid in ids if len(gid) >= 2}
            country_code = cc_candidates.pop() if len(cc_candidates) == 1 else None

        # Resolve in priority order
        resolved: dict[str, object] = {}  # gisco_id → Geometry
        remaining = set(ids)

        # 1. maps/ overrides
        remaining = self._resolve_from_maps(
            remaining, resolved, election_type, country_code, year
        )

        # 2. GISCO LAU
        if remaining:
            remaining = self._resolve_from_lau(remaining, resolved, year)

        # 3. GISCO NUTS (levels 3 → 0)
        if remaining:
            remaining = self._resolve_from_nuts(remaining, resolved, year)

        if remaining:
            warnings.warn(
                f"Could not find geometry for {len(remaining)} gisco_id(s): "
                f"{sorted(remaining)[:10]}"
            )

        if not resolved:
            return gpd.GeoDataFrame({"gisco_id": [], "geometry": []},
                                    crs="EPSG:3035")

        return gpd.GeoDataFrame(
            {"gisco_id": list(resolved.keys()),
             "geometry": list(resolved.values())},
            crs="EPSG:3035",
        )

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
            pattern = f"EU_??_*.shp"
            for shp in sorted(self.maps_dir.glob(pattern)):
                m = re.match(r"EU_([A-Z]{2})_(\d{4})\.shp$", shp.name)
                if not m:
                    continue
                cc, syear = m.group(1), int(m.group(2))
                if cc_set is not None and cc not in cc_set:
                    continue
                # Pick the best year ≤ requested; will be overwritten by a
                # later (closer) match since glob is sorted ascending.
                if syear <= year:
                    gdf = _read_maps_shp(shp)
                    gdf["country_code"] = cc
                    parts.append(gdf)
                    maps_ccs.add(cc)
        else:
            # Collect best year-specific file per CC (pattern: {CC}_{YEAR}.shp)
            best_versioned: dict[str, Path] = {}
            for shp in sorted(self.maps_dir.glob("[A-Z][A-Z]_[0-9][0-9][0-9][0-9].shp")):
                m = re.match(r"([A-Z]{2})_(\d{4})\.shp$", shp.name)
                if not m:
                    continue
                cc, syear = m.group(1), int(m.group(2))
                if cc_set is not None and cc not in cc_set:
                    continue
                if syear <= year:
                    best_versioned[cc] = shp  # sorted ascending → last ≤ year wins
            # Fall back to plain {CC}.shp for countries not covered above
            for shp in sorted(self.maps_dir.glob("[A-Z][A-Z].shp")):
                cc = shp.stem
                if cc_set is not None and cc not in cc_set:
                    continue
                if cc not in best_versioned:
                    best_versioned[cc] = shp
            for cc, shp in sorted(best_versioned.items()):
                gdf = _read_maps_shp(shp)
                gdf["country_code"] = cc
                parts.append(gdf)
                maps_ccs.add(cc)

        # ── GISCO LAU ──────────────────────────────────────────────────
        try:
            lau = _lau_dataset(year, self.cache_dir)
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

        geom_by_id: dict[str, object] = {}
        groups = df.groupby(keys, sort=False) if keys else [((), df)]
        for gk, sub in groups:
            info = dict(zip(keys, gk if isinstance(gk, tuple) else (gk,)))
            yr = int(info[year_col]) if year_col else year
            et = str(info[et_col]) if et_col else (election_type or "national")
            cc = str(info[cc_col]) if cc_col else None
            g = self.geometries(sub[id_col].astype(str), year=int(yr),
                                election_type=et, country_code=cc)
            geom_by_id.update(zip(g["gisco_id"], g["geometry"]))

        df["geometry"] = df[id_col].astype(str).map(geom_by_id)
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
                path = _maps_eu(cc, year)
            else:
                path = _maps_national(cc, year)
            if path is None:
                continue
            gdf = _read_maps_shp(path)
            hit = gdf[gdf["gisco_id"].isin(remaining)]
            for _, row in hit.iterrows():
                resolved[row["gisco_id"]] = row["geometry"]
            remaining = remaining - set(hit["gisco_id"])

        return remaining

    def _resolve_from_lau(
        self, remaining: set[str], resolved: dict, year: int
    ) -> set[str]:
        """Try GISCO LAU dataset; return still-unresolved IDs.

        Also applies known ID-format normalisations before the lookup:
        - Liechtenstein: ``LI_00001001`` → ``LI_LI00001001``
          (GISCO prefixes the national code a second time).
        """
        try:
            lau = _lau_dataset(year, self.cache_dir)
        except Exception as exc:
            warnings.warn(f"Could not fetch LAU dataset: {exc}")
            return remaining

        lau_index = lau.set_index("gisco_id")["geometry"].to_dict()

        still_remaining = set()
        for gid in remaining:
            # Direct match
            if gid in lau_index:
                resolved[gid] = lau_index[gid]
                continue
            # Try known ID-format variants
            alt = _normalize_lau_id(gid)
            if alt is not None and alt in lau_index:
                resolved[gid] = lau_index[alt]
            else:
                still_remaining.add(gid)

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
            for _, row in hit.iterrows():
                resolved[row["gisco_id"]] = row["geometry"]
            remaining -= set(hit["gisco_id"])
        return remaining
