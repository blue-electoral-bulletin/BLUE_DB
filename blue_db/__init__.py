"""
BLUE_DB — Python API for the Basic Local Units Election Database.

    from blue_db import BlueDB, BlueGeo

`BlueDB` reads the ``dist/`` distribution folder (electoral results, party and
geographic typologies); `BlueGeo` resolves matching map geometries. When the
package is imported from a checkout of the repository, both default to the
``dist/`` and ``maps/`` folders next to it; when installed elsewhere, pass the
dataset location explicitly, e.g. ``BlueDB("/path/to/dist")``.
"""
from .blue_db import BlueDB

try:
    from .blue_maps import BlueGeo
except ImportError:  # geopandas / shapely not installed (the `maps` extra)
    BlueGeo = None

__all__ = ["BlueDB", "BlueGeo"]
__version__ = "1.0.0"
