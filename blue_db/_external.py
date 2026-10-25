"""
_external.py
============
Election results that are not part of the Zenodo record.

The Jersey and Guernsey results are reproduced under their publishers' terms,
which restrict reuse to research and non-commercial purposes and so cannot be
released under the dataset's CC BY 4.0 licence.  They are kept in the GitHub
repository instead, and :class:`~blue_db.BlueDB` downloads them from there the
first time they are requested, pinned to the release tag, like the GISCO
boundaries are downloaded on demand by :class:`~blue_db.BlueGeo`.

The notices below are kept in sync with ``external`` in ``blue_db_meta.json``
(see tests/test_packaging.py).
"""
from __future__ import annotations

import warnings
from pathlib import Path

try:  # installed as a package
    from ._paths import external_dir
except ImportError:  # run straight from lib/ (sys.path hack, see demo/)
    from _paths import external_dir  # type: ignore[no-redef]

__all__ = [
    "BASE_URL",
    "COUNTRIES",
    "REF",
    "BlueExternalDataNotice",
    "fetch",
    "notice",
]

REF = "v2026.2"
BASE_URL = "https://raw.githubusercontent.com/blue-electoral-bulletin/BLUE_DB/{ref}/dist/"

COUNTRIES: dict[str, dict[str, str]] = {
    "JE": {
        "name": "Jersey",
        "licence": "© States of Jersey; research and non-commercial use",
        "notice": (
            "Jersey election results © States of Jersey, compiled from "
            "secondary sources and checked against the official results "
            "published by the States Greffe (https://www.vote.je). Reuse is permitted for research, reporting, "
            "and personal or non-commercial purposes only; any other reuse "
            "requires the permission of the States Greffe (contact@vote.je). "
            "These files are not covered by BLUE_DB's CC BY 4.0 licence or by "
            "the MIT License."
        ),
        "terms_url": "https://www.vote.je/terms-and-conditions/",
    },
    "GG": {
        "name": "Guernsey",
        "licence": "© States of Guernsey; research and private study",
        "notice": (
            "Guernsey election results © States of Guernsey, compiled from "
            "secondary sources and checked against the official results "
            "published on https://www.gov.gg; reproduced for research and "
            "private study, accurately and with the source and copyright "
            "acknowledged; any other reuse requires the permission of the "
            "States of Guernsey. These files are not covered by BLUE_DB's "
            "CC BY 4.0 licence or by the MIT License."
        ),
        "terms_url": "https://www.gov.gg/TsCs",
    },
}


class BlueExternalDataNotice(UserWarning):
    """Issued once per country when results under their publisher's terms are used."""


_noticed: set[str] = set()


def notice(country: str) -> None:
    """Show the publisher's terms for *country*, once per session."""
    if country in _noticed or country not in COUNTRIES:
        return
    _noticed.add(country)
    info = COUNTRIES[country]
    warnings.warn(f"{info['notice']} Terms: {info['terms_url']}",
                  BlueExternalDataNotice, stacklevel=3)


def fetch(file_path: str, cache: str | Path | None = None,
          timeout: float = 60) -> Path:
    """Return a local copy of the external election file *file_path*.

    *file_path* is the index's path relative to ``dist/``
    (``elections/JE/2022_06.csv``).  The file is downloaded from the GitHub
    repository at :data:`REF` on first use and cached under
    :func:`~blue_db._paths.external_dir`.
    """
    import requests  # a default dependency, imported lazily like in BlueGeo

    root = Path(cache) if cache is not None else external_dir()
    path = root / REF / file_path
    if path.exists():
        return path
    url = BASE_URL.format(ref=REF) + file_path
    resp = requests.get(url, timeout=timeout)
    if resp.status_code != 200:
        raise FileNotFoundError(
            f"could not download {file_path} from {url} (HTTP {resp.status_code})")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(resp.content)
    tmp.replace(path)
    return path
