"""
cli.py
======
Command-line front end for the installed package, exposed as ``blue-db``.

    blue-db info                                  # version + where data lives
    blue-db elections --country FR --type national
    blue-db results --country FR --latest --level NUTS2 --by ideology -o fr.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import CITATION, DOI, GITHUB, WEBSITE, __version__
from ._paths import (
    ENV_CACHE,
    ENV_DIST,
    ENV_HOME,
    ENV_MAPS,
    BlueDataNotFound,
    cache_dir,
    dist_dir,
    external_dir,
    maps_dir,
    user_data_dir,
)
from .blue_db import BlueDB


def _write(df, out: str | None) -> None:
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"{len(df)} rows -> {out}", file=sys.stderr)
    else:
        df.to_csv(sys.stdout, index=False)


def _cmd_info(args: argparse.Namespace) -> int:
    print(f"blue-db {__version__}")
    print(f"website  {WEBSITE}")
    print(f"github   {GITHUB}")
    print(f"doi      https://doi.org/{DOI}")
    print(f"cite     {CITATION}")
    print()
    try:
        dist = dist_dir(args.dist)
        print(f"dist     {dist}")
    except BlueDataNotFound as exc:
        dist = None
        print(f"dist     NOT FOUND\n\n{exc}")
    maps = maps_dir()
    print(f"maps     {maps}{'' if maps.is_dir() else '  (missing; GISCO only)'}")
    print(f"cache    {cache_dir()}")
    print(f"external {external_dir()}  (Jersey and Guernsey results, fetched on demand)")
    print(f"\nenv      {ENV_HOME}, {ENV_DIST}, {ENV_MAPS}, {ENV_CACHE}")
    print(f"unpack the Zenodo archive into {user_data_dir()} to install the data")
    if dist is not None:
        db = BlueDB(dist)
        idx = db.index
        print(
            f"\n{len(idx)} election files, "
            f"{idx['country_code'].nunique()} countries, "
            f"{int(idx['year'].min())}-{int(idx['year'].max())}, "
            f"{len(db.parties)} parties"
        )
    return 0


def _cmd_elections(args: argparse.Namespace) -> int:
    db = BlueDB(args.dist, external=not args.no_external)
    df = db.elections(
        countries=args.country,
        election_type=args.type,
        years=args.year,
        vote_type=args.vote_type,
        round=args.round,
    )
    _write(df, args.out)
    return 0


def _cmd_results(args: argparse.Namespace) -> int:
    db = BlueDB(args.dist, external=not args.no_external)
    df = db.results(
        countries=args.country,
        election_type=args.type,
        years=args.year,
        vote_type=args.vote_type,
        round=args.round,
        latest_per_country=args.latest,
        geo_level=args.level,
        aggregate_by=args.by,
    )
    if args.shares:
        df = db.vote_shares(df)
    _write(df, args.out)
    return 0


def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--country", "-c", action="append", metavar="XX",
                   help="ISO-2 country code; repeatable")
    p.add_argument("--type", "-t", choices=["national", "european"],
                   help="election type")
    p.add_argument("--year", "-y", action="append", type=int,
                   help="election year; repeatable")
    p.add_argument("--vote-type", help="e.g. party, single_member, Zweitstimme")
    p.add_argument("--round", type=int, choices=[1, 2], help="election round")
    p.add_argument("--out", "-o", help="write CSV here instead of stdout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blue-db",
        description="BLUE_DB — municipality-level European election results.",
    )
    parser.add_argument("--version", action="version", version=f"blue-db {__version__}")
    parser.add_argument("--dist", help="path to the dist/ folder (overrides lookup)")
    parser.add_argument("--no-external", action="store_true",
                        help="leave out the Jersey and Guernsey results, which are "
                             "not under CC BY 4.0")
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="show version, data locations and dataset size")
    info.set_defaults(func=_cmd_info)

    elections = sub.add_parser("elections", help="list elections from the index")
    _add_filters(elections)
    elections.set_defaults(func=_cmd_elections)

    results = sub.add_parser("results", help="export election results as CSV")
    _add_filters(results)
    results.add_argument("--latest", action="store_true",
                         help="keep only the most recent election per country")
    results.add_argument("--level", "-l", default="LAU",
                         choices=["LAU", "NUTS3", "NUTS2", "NUTS1", "NUTS0"],
                         help="geographic level of the rows (default: LAU)")
    results.add_argument("--by", choices=["ideology", "ep_group", "eu_party"],
                         help="aggregate party columns into groups")
    results.add_argument("--shares", action="store_true",
                         help="convert vote counts to shares of valid votes")
    results.set_defaults(func=_cmd_results)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BlueDataNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # e.g. `blue-db results | head`
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
