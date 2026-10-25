"""
_derived.py
===========
Boundary layers that are built on the user's machine rather than shipped.

Malta's electoral districts are unions of localities.  The locality boundaries
come from the Malta Spatial Data Infrastructure (MSDI), whose licence
(CC BY-NC-ND) lets them be shared unmodified but not as a derivative, so the
repository ships the original file (``maps/sources/MT/``) and the districts are
dissolved from it here, the first time they are needed, into the cache.  The
``maps/`` layer name stays ``MT.shp``, so the crosswalk in
``dist/geo/geo_lookup.csv`` does not change.
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

import geopandas as gpd

__all__ = ["DERIVED", "MT_LOCALITIES_DISTRICTS", "build_mt", "derived_glob", "maps_path"]

MT_SOURCE = Path("sources") / "MT" / "AdministrativeUnitsAndBoundaries.gml"

# The 13 electoral districts as unions of MSDI localities (the ``text`` field).
# Localities that straddle a district boundary are assigned to their primary
# district.
MT_LOCALITIES_DISTRICTS = {
    "MT_01": [
        "VALLETTA", "FLORIANA", "HAMRUN", "MARSA", "SANTA VENERA",
        # Birkirkara appears here only as a sub-area (Fleur-De-Lys); main polygon → MT_08
    ],
    "MT_02": [
        "BIRGU", "ISLA", "BORMLA", "ZABBAR", "KALKARA", "XGHAJRA",
        # Marsaskala part → MT_03 primary; Fgura (Tal-Gallu) → MT_04 primary
    ],
    "MT_03": [
        "ZEJTUN", "GHAXAQ", "MARSASCALA", "MARSAXLOKK",
    ],
    "MT_04": [
        "FGURA", "GUDJA", "PAOLA", "SANTA LUCIJA", "TARXIEN",
    ],
    "MT_05": [
        "BIRZEBBUGIA", "KIRKOP", "MQABBA", "QRENDI", "SAFI", "ZURRIEQ",
    ],
    "MT_06": [
        "LUQA", "QORMI", "SIGGIEWI",
    ],
    "MT_07": [
        "DINGLI", "MDINA", "MTARFA", "RABAT (Malta)", "ZEBBUG (Malta)",
    ],
    "MT_08": [
        "BIRKIRKARA", "IKLIN", "LIJA", "BALZAN",
        # Naxxar part → MT_10 primary
    ],
    "MT_09": [
        "GHARGHUR", "MSIDA", "PIETA", "SAN GWANN", "SWIEQI", "TA' XBIEX",
    ],
    "MT_10": [
        "GZIRA", "PEMBROKE", "SAN GILJAN", "SLIEMA", "NAXXAR",
    ],
    "MT_11": [
        "ATTARD", "MOSTA",
    ],
    "MT_12": [
        "MGARR", "MELLIEHA", "SAN PAWL IL-BAHAR",
    ],
    "MT_13": [
        "RABAT (Victoria)", "FONTANA", "GHAJNSIELEM", "GHARB", "GHASRI",
        "KERCEM", "MUNXAR", "NADUR", "QALA", "SAN LAWRENZ",
        "SANNAT", "XAGHRA", "XEWKIJA", "ZEBBUG (Ghawdex)",
    ],
}


def build_mt(source: Path, out: Path) -> Path:
    """Dissolve the MSDI localities in *source* into Malta's districts at *out*."""
    localities = gpd.read_file(source, layer="AdministrativeUnit").to_crs("EPSG:3035")
    rows = []
    for gisco_id, names in MT_LOCALITIES_DISTRICTS.items():
        subset = localities[localities["text"].isin(names)]
        if not subset.empty:
            rows.append({"gisco_id": gisco_id, "geometry": subset.geometry.union_all()})
    out.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(rows, crs=localities.crs).to_file(out)
    return out


# maps/ layer name -> (source file under maps/, builder)
DERIVED = {"MT.shp": (MT_SOURCE, build_mt)}


def _derived_dir(cache_dir: Path) -> Path:
    return Path(cache_dir).parent / "derived"


def maps_path(maps_dir: Path, cache_dir: Path, name: str) -> Path:
    """Path of the ``maps/`` layer *name*, building a derived one if needed.

    A file shipped in ``maps/`` wins; a derived layer is built once from its
    source and cached.  For anything else the (possibly missing) ``maps/``
    path is returned, so callers keep their existing ``exists()`` checks.
    """
    path = Path(maps_dir) / name
    if path.exists() or name not in DERIVED:
        return path
    source, build = DERIVED[name]
    source = Path(maps_dir) / source
    if not source.exists():
        return path
    out = _derived_dir(cache_dir) / name
    return out if out.exists() else build(source, out)


def derived_glob(maps_dir: Path, cache_dir: Path, pattern: str) -> list[Path]:
    """``maps_dir.glob(pattern)`` plus the derived layers whose name matches."""
    found = {p.name: p for p in Path(maps_dir).glob(pattern)}
    for name, (source, _) in DERIVED.items():
        if name not in found and fnmatch(name, pattern) and (Path(maps_dir) / source).exists():
            found[name] = maps_path(maps_dir, cache_dir, name)
    return [found[n] for n in sorted(found)]
