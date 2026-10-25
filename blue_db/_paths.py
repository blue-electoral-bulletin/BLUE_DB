"""
_paths.py
=========
Location of the BLUE_DB data directories for an installed copy of the library.

The published distribution (``dist/``) and the constituency shapefiles
(``maps/``) are far too large to ship inside a wheel, so the installed
package locates them at run time.  Search order, first hit wins:

1. an explicit path passed to :class:`BlueDB` / :class:`BlueGeo`;
2. the environment variables ``BLUE_DB_DIST`` / ``BLUE_DB_MAPS``;
3. ``$BLUE_DB_HOME/<name>``;
4. the source checkout the package was installed from (editable installs);
5. ``<name>/`` or ``BLUE_DB/<name>/`` in the working directory or any parent;
6. the per-user data directory (``$XDG_DATA_HOME/blue_db``, ``~/Library/
   Application Support/blue_db``, ``%LOCALAPPDATA%\\blue_db``).

Download the data from Zenodo (see ``blue-db info``) and either unpack it into
the user data directory or point ``BLUE_DB_HOME`` at it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "BlueDataNotFound",
    "cache_dir",
    "dist_dir",
    "external_dir",
    "maps_dir",
    "user_data_dir",
]

ENV_HOME = "BLUE_DB_HOME"
ENV_DIST = "BLUE_DB_DIST"
ENV_MAPS = "BLUE_DB_MAPS"
ENV_CACHE = "BLUE_DB_CACHE"

_PKG_DIR = Path(__file__).resolve().parent
# The repository the package lives in, if this is a source checkout: dist/ and
# maps/ sit next to the lib/ directory holding this file.
_CHECKOUT = _PKG_DIR.parent


class BlueDataNotFound(FileNotFoundError):
    """Raised when a required BLUE_DB data directory cannot be located."""


def user_data_dir() -> Path:
    """Per-user directory where an unpacked BLUE_DB distribution is expected."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "blue_db"


def _user_cache_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "blue_db"


def _candidates(name: str, env_var: str) -> list[Path]:
    """Ordered candidate locations for the data directory *name*."""
    out: list[Path] = []
    if os.environ.get(env_var):
        out.append(Path(os.environ[env_var]).expanduser())
    if os.environ.get(ENV_HOME):
        out.append(Path(os.environ[ENV_HOME]).expanduser() / name)
    out.append(_CHECKOUT / name)
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        out.append(parent / name)
        out.append(parent / "BLUE_DB" / name)
    out.append(user_data_dir() / name)
    out.append(user_data_dir() / "BLUE_DB" / name)
    return out


def _env_paths(name: str, env_var: str) -> list[Path]:
    """Locations configured through the environment (authoritative if set)."""
    out = []
    if os.environ.get(env_var):
        out.append(Path(os.environ[env_var]).expanduser())
    if os.environ.get(ENV_HOME):
        out.append(Path(os.environ[ENV_HOME]).expanduser() / name)
    return out


def _env_source(name: str, path: Path) -> str:
    """Name the environment variable that produced *path*, for error messages."""
    if os.environ.get(ENV_DIST) and name == "dist":
        if Path(os.environ[ENV_DIST]).expanduser() == path:
            return ENV_DIST
    if os.environ.get(ENV_MAPS) and name == "maps":
        if Path(os.environ[ENV_MAPS]).expanduser() == path:
            return ENV_MAPS
    return ENV_HOME


def _not_found_message(name: str, env_var: str, tried: list[Path]) -> str:
    shown = "\n".join(f"  - {p}" for p in tried[:8])
    return (
        f"BLUE_DB {name}/ directory not found.  Locations tried:\n{shown}\n"
        f"\nFix this by either:\n"
        f"  * passing the path explicitly, e.g. BlueDB(dist='/data/BLUE_DB/dist');\n"
        f"  * setting {env_var}=/path/to/{name};\n"
        f"  * setting {ENV_HOME}=/path/to/BLUE_DB (holding dist/ and maps/);\n"
        f"  * unpacking the Zenodo archive into {user_data_dir()}.\n"
        f"Run `blue-db info` to see where the library is looking."
    )


def dist_dir(explicit: str | Path | None = None) -> Path:
    """Resolve the ``dist/`` distribution folder, or raise :class:`BlueDataNotFound`."""
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise BlueDataNotFound(f"dist folder not found: {path}")
        return path
    tried = _candidates("dist", ENV_DIST)
    for path in tried:
        if (path / "elections" / "index.csv").exists():
            return path
        if path in _env_paths("dist", ENV_DIST):
            # An explicitly configured location that does not hold the data is
            # an error, not a reason to silently fall back to another copy.
            raise BlueDataNotFound(
                f"{_env_source('dist', path)} points at {path}, which does not "
                f"contain elections/index.csv."
            )
    raise BlueDataNotFound(_not_found_message("dist", ENV_DIST, tried))


def maps_dir(explicit: str | Path | None = None) -> Path:
    """Resolve the ``maps/`` shapefile folder.

    The overrides in ``maps/`` are optional — without them geometries are
    resolved from GISCO alone — so a missing directory is not an error here;
    the first candidate path is returned so callers can report it.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    tried = _candidates("maps", ENV_MAPS)
    for path in tried:
        if path.is_dir():
            return path
    return tried[0]


def cache_dir(explicit: str | Path | None = None) -> Path:
    """Resolve the download cache for GISCO tiles (created on first use)."""
    if explicit is not None:
        return Path(explicit).expanduser()
    if os.environ.get(ENV_CACHE):
        return Path(os.environ[ENV_CACHE]).expanduser()
    if os.environ.get(ENV_HOME):
        return Path(os.environ[ENV_HOME]).expanduser() / ".cache" / "geo"
    # Keep using the checkout's cache when running from a source tree, so an
    # editable install reuses tiles already downloaded by the build scripts.
    if (_CHECKOUT / ".cache" / "geo").is_dir():
        return _CHECKOUT / ".cache" / "geo"
    return _user_cache_dir() / "geo"


def external_dir(explicit: str | Path | None = None) -> Path:
    """Resolve the download cache for the Jersey and Guernsey results.

    These are not part of the Zenodo record and are fetched from the GitHub
    repository on demand (see :mod:`blue_db._external`); they are cached next
    to the GISCO tiles, in a sibling ``external/`` folder.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    return cache_dir().parent / "external"
