"""
BLUE_DB — Basic Local Units Election Database
=============================================
Python API for the BLUE_DB electoral database: municipality-level results of
national parliamentary and European Parliament elections across Europe.

    from blue_db import BlueDB

    db = BlueDB()
    df = db.results(election_type="national", latest_per_country=True,
                    geo_level="NUTS0", aggregate_by="ideology")

Geometries:

    from blue_db import BlueGeo

    gdf = BlueGeo().attach(df, year=2024)

The election data itself is *not* part of the wheel — see :mod:`blue_db._paths`
or run ``blue-db info`` for where the library looks for it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ._external import BlueExternalDataNotice
from ._paths import (BlueDataNotFound, cache_dir, dist_dir, external_dir, maps_dir,
                     user_data_dir)
from .blue_db import BlueDB

# Keep in sync with the version in pyproject.toml (tests/test_packaging.py checks).
__version__ = "1.0.0"

# Kept in sync with blue_db_meta.json at the repository root (see
# tests/test_packaging.py, which asserts they agree in a source checkout).
__author__ = "François Hublet, Nicola Chiecchio"
DOI = "10.5281/zenodo.21434111"
WEBSITE = "https://electionatlas.eu"
GITHUB = "https://github.com/blue-electoral-bulletin/BLUE_DB"
CITATION = (
    "Hublet, F., & Chiecchio, N. (2026). BLUE_DB — Basic Local Units Election "
    "Database. Paris: Groupe d'études géopolitiques. "
    "https://doi.org/10.5281/zenodo.21434111"
)

if TYPE_CHECKING:  # pragma: no cover
    from .blue_maps import BlueGeo

__all__ = [
    "BlueDB",
    "BlueDataNotFound",
    "BlueExternalDataNotice",
    "BlueGeo",
    "CITATION",
    "DOI",
    "GITHUB",
    "WEBSITE",
    "__version__",
    "cache_dir",
    "dist_dir",
    "external_dir",
    "maps_dir",
    "user_data_dir",
]


def __getattr__(name: str):
    """Import :class:`BlueGeo` lazily.

    geopandas is a hard dependency, but it is heavy (GDAL/PROJ) and only needed
    for geometries, so importing ``blue_db`` does not pay for it up front.
    """
    if name == "BlueGeo":
        try:
            from .blue_maps import BlueGeo
        except ImportError as exc:  # pragma: no cover - broken environment
            raise ImportError(
                "BlueGeo could not import its geometry dependencies "
                "(geopandas, shapely, requests) — reinstall blue_db."
            ) from exc
        return BlueGeo
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
