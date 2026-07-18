"""
blue_db.py
==========
Python API for the BLUE_DB electoral database distribution.

Quick start
-----------
    from blue_db import BlueDB

    db = BlueDB()           # auto-detects dist/ next to lib/

    # Latest national election per country, ideology shares at country level
    df = db.results(
        election_type="national",
        latest_per_country=True,
        geo_level="NUTS0",
        aggregate_by="ideology",
    )

    # All EP elections since 2019, raw party votes at NUTS2 level
    df = db.results(
        election_type="european",
        years=[2019, 2024],
        geo_level="NUTS2",
    )
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

# Columns in every election CSV that are not party vote columns
_META = frozenset({"gisco_id", "name", "registered", "turnout", "invalid", "blank"})

_GEO_LEVELS = ("LAU", "NUTS3", "NUTS2", "NUTS1", "NUTS0")


class BlueDB:
    """Access layer for the BLUE_DB distribution folder."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, dist: str | Path | None = None) -> None:
        """
        Parameters
        ----------
        dist : path to the ``dist/`` folder.
               Defaults to ``<lib_dir>/../dist``.
        """
        if dist is None:
            dist = Path(__file__).parent.parent / "dist"
        self.dist = Path(dist)
        if not self.dist.exists():
            raise FileNotFoundError(f"dist folder not found: {self.dist}")

        self._index_df: pd.DataFrame | None = None
        self._parties_df: pd.DataFrame | None = None
        self._ep_groups_df: pd.DataFrame | None = None
        self._eu_parties_df: pd.DataFrame | None = None
        self._nuts_df: pd.DataFrame | None = None
        self._laus_df: pd.DataFrame | None = None
        self._special_df: pd.DataFrame | None = None
        self._geo_chain: dict[str, dict[str, str]] | None = None
        self._lau_periods: dict[str, list[tuple[int | None, int | None, str]]] | None = None
        self._nuts_end_years: dict[str, int] | None = None
        self._nuts_successors: dict[str, str] | None = None

        # per-party lookup caches (populated lazily)
        self._ideology_cache: dict[str, str] = {}
        self._ep_group_cache: dict[tuple[str, int], str] = {}
        self._eu_party_cache: dict[tuple[str, int], str] = {}

    # ------------------------------------------------------------------
    # Reference tables (lazy-loaded)
    # ------------------------------------------------------------------

    @property
    def index(self) -> pd.DataFrame:
        """Election index: one row per election file."""
        if self._index_df is None:
            df = pd.read_csv(
                self.dist / "elections" / "index.csv",
                parse_dates=["election_date"],
            )
            df["year"] = df["election_date"].dt.year
            df["election_type"] = df["file_path"].apply(
                lambda p: "european" if "/EU_" in str(p) else "national"
            )
            df["year"] = df["year"].astype("Int64")
            self._index_df = df
        return self._index_df

    @property
    def parties(self) -> pd.DataFrame:
        """Party catalogue (parties.csv)."""
        if self._parties_df is None:
            self._parties_df = pd.read_csv(self.dist / "parties" / "parties.csv")
        return self._parties_df

    @property
    def ep_groups(self) -> pd.DataFrame:
        """EP group reference table."""
        if self._ep_groups_df is None:
            self._ep_groups_df = pd.read_csv(self.dist / "parties" / "ep_groups.csv")
        return self._ep_groups_df

    @property
    def eu_parties(self) -> pd.DataFrame:
        """EU party reference table."""
        if self._eu_parties_df is None:
            self._eu_parties_df = pd.read_csv(self.dist / "parties" / "eu_parties.csv")
        return self._eu_parties_df

    @property
    def nuts(self) -> pd.DataFrame:
        """NUTS geographic units."""
        if self._nuts_df is None:
            self._nuts_df = pd.read_csv(self.dist / "geo" / "nuts.csv")
        return self._nuts_df

    @property
    def laus(self) -> pd.DataFrame:
        """LAU (municipality-level) units."""
        if self._laus_df is None:
            self._laus_df = pd.read_csv(self.dist / "geo" / "laus.csv")
        return self._laus_df

    @property
    def special(self) -> pd.DataFrame:
        """Special geographic units (abroad voters, postal vote, etc.)."""
        if self._special_df is None:
            self._special_df = pd.read_csv(self.dist / "geo" / "special.csv")
        return self._special_df

    # ------------------------------------------------------------------
    # Geographic hierarchy
    # ------------------------------------------------------------------

    @property
    def geo_chain(self) -> dict[str, dict[str, str]]:
        """
        Mapping ``gisco_id → {NUTS0, NUTS1, NUTS2, NUTS3, LAU}``.
        Each value is the ``gisco_id`` of the ancestor at that level.
        """
        if self._geo_chain is None:
            self._geo_chain = self._build_geo_chain()
        return self._geo_chain

    def _build_geo_chain(self) -> dict[str, dict[str, str]]:
        chain: dict[str, dict[str, str]] = {}

        # Index NUTS nodes: gisco_id → (int_level, parent_gisco_id | None)
        nuts_level: dict[str, int] = {}
        nuts_parent: dict[str, str] = {}

        for _, row in self.nuts.iterrows():
            gid = str(row["gisco_id"])
            lvl_str = str(row.get("level") or "")
            if "NUTS" not in lvl_str:
                continue
            lvl = int(lvl_str.split()[-1])
            nuts_level[gid] = lvl
            p = row.get("parent")
            if pd.notna(p) and str(p):
                nuts_parent[gid] = str(p)

        # Recursive chain builder with memoisation
        def _nuts_chain(gid: str) -> dict[str, str]:
            if gid in chain:
                return chain[gid]
            result: dict[str, str] = {}
            if gid in nuts_level:
                result[f"NUTS{nuts_level[gid]}"] = gid
                p = nuts_parent.get(gid)
                if p:
                    result.update(_nuts_chain(p))
            chain[gid] = result
            return result

        for gid in list(nuts_level):
            _nuts_chain(gid)

        # LAU → add {"LAU": gisco_id} + parent NUTS chain
        for _, row in self.laus.iterrows():
            gid = str(row["gisco_id"])
            p = row.get("parent")
            parent_chain = chain.get(str(p), {}) if pd.notna(p) else {}
            chain[gid] = {"LAU": gid, **parent_chain}

        # Special units → inherit parent's chain
        for _, row in self.special.iterrows():
            gid = str(row["gisco_id"])
            if gid in chain:
                continue
            p = row.get("parent")
            if pd.notna(p):
                p_str = str(p)
                if p_str in chain:
                    chain[gid] = chain[p_str].copy()
                elif len(p_str) == 2:
                    # bare country code → NUTS0
                    chain[gid] = {"NUTS0": p_str}
                else:
                    chain[gid] = {}
            else:
                chain[gid] = {}

        return chain

    @property
    def lau_periods(self) -> dict[str, list[tuple[int | None, int | None, str]]]:
        """``gisco_id → [(start_year, end_year, parent_nuts_id), …]``.

        ISTAT (and other) municipality codes are **reused** across renumberings:
        e.g. ``IT_103024`` is *Borgomanero* (parent ITC15/Novara) until 2005 and
        *Craveggia* (parent ITC14/VCO) from 2005. A flat ``gisco_id → parent``
        map (as in :pyattr:`geo_chain`) collapses those to a single, last-seen
        parent, which misfiles the earlier municipality's votes into the later
        occupant's NUTS region. This structure keeps every time slice so the
        parent can be chosen by election year.
        """
        if self._lau_periods is None:
            def _yr(v: Any) -> int | None:
                if pd.isna(v):
                    return None
                try:
                    return int(float(v))
                except (ValueError, TypeError):
                    return None

            m: dict[str, list[tuple[int | None, int | None, str]]] = {}
            for _, row in self.laus.iterrows():
                parent = row.get("parent")
                if pd.isna(parent) or not str(parent).strip():
                    continue
                gid = str(row["gisco_id"])
                m.setdefault(gid, []).append(
                    (_yr(row.get("start_year")), _yr(row.get("end_year")), str(parent).strip())
                )
            self._lau_periods = m
        return self._lau_periods

    def _lau_parent_at(self, gisco_id: str, year: int) -> str | None:
        """LAU's NUTS parent valid in *year* (end-date exclusive), or None.

        Returns None when the code carries no per-period parent info or no slice
        covers *year*, letting the caller fall back to the flat ``geo_chain``.
        """
        periods = self.lau_periods.get(gisco_id)
        if not periods:
            return None
        if len(periods) == 1:
            return periods[0][2]
        for start, end, parent in periods:
            if (start is None or year >= start) and (end is None or year < end):
                return parent
        return None

    @property
    def nuts_end_years(self) -> dict[str, int]:
        """NUTS gisco_id → year the region was abolished (absent if still active)."""
        if self._nuts_end_years is None:
            m: dict[str, int] = {}
            for _, row in self.nuts.iterrows():
                ey = row.get("end_year")
                if pd.notna(ey):
                    m[str(row["gisco_id"])] = int(ey)
            self._nuts_end_years = m
        return self._nuts_end_years

    @property
    def nuts_successors(self) -> dict[str, str]:
        """Map each obsolete NUTS gisco_id to its active successor.

        Built from the ``predecessors`` field: if CZ063 lists CZ061 as a
        predecessor, then CZ061 → CZ063.  Chains are fully resolved so that
        a region replaced twice maps directly to its current form.
        """
        if self._nuts_successors is None:
            raw: dict[str, str] = {}
            for _, row in self.nuts[self.nuts["predecessors"].notna()].iterrows():
                new_id = str(row["gisco_id"])
                for pred in str(row["predecessors"]).split(","):
                    pred = pred.strip()
                    if pred:
                        raw[pred] = new_id
            # Resolve multi-hop chains: A→B→C becomes A→C
            resolved: dict[str, str] = {}
            for old in raw:
                cur = old
                seen: set[str] = set()
                while cur in raw and cur not in seen:
                    seen.add(cur)
                    cur = raw[cur]
                resolved[old] = cur
            self._nuts_successors = resolved
        return self._nuts_successors

    def _active_nuts(self, nuts_id: str, year: int) -> str:
        """Return the latest NUTS id by following the full successor chain.

        NUTS regions are sometimes renumbered across vintages (e.g. CZ061 →
        CZ063 in 2008).  The geography is identical; only the identifier
        changes.  We always normalise to the current code so that BLUE_DB
        results join cleanly against any external source that uses the latest
        NUTS vintage (e.g. EU-NED, GISCO), regardless of the election year.
        """
        seen: set[str] = set()
        while nuts_id not in seen and nuts_id in self.nuts_successors:
            seen.add(nuts_id)
            nuts_id = self.nuts_successors[nuts_id]
        return nuts_id

    # ------------------------------------------------------------------
    # Canonical election selection
    # ------------------------------------------------------------------

    # Vote-type priority: lower number = preferred for cross-country comparison
    _VOTE_TYPE_PRIORITY: dict[str | float, int] = {
        float("nan"):    0,   # NaN → pure PR election, best
        "Zweitstimme":   1,   # DE second vote (list)
        "party":         1,   # HU party list
        "Erststimme":    9,   # DE first vote (constituency)
        "single_member": 9,   # HU/FR single-member
    }

    def _vote_type_rank(self, vt: Any) -> int:
        if pd.isna(vt) or vt == "":
            return 0
        return self._VOTE_TYPE_PRIORITY.get(str(vt), 5)

    def canonical_elections(
        self,
        countries: list[str] | str | None = None,
        election_type: str | None = None,
        years: list[int] | int | None = None,
    ) -> pd.DataFrame:
        """
        Return exactly one election file per unique (country, election_date).

        For elections with multiple files (Germany: Erst-/Zweitstimme;
        Hungary: party/single-member; France: round 1/2), selects the
        most useful file for cross-country vote-share comparison:

        - Prefer ``vote_type=NaN`` (pure PR) over constituency votes.
        - Prefer ``Zweitstimme`` over ``Erststimme`` for Germany.
        - Prefer ``party`` over ``single_member`` for Hungary.
        - For two-round systems: prefer round 1 over round 2.
        """
        df = self.elections(countries=countries, election_type=election_type, years=years)
        df = df.copy()
        df["_vt_rank"] = df["vote_type"].apply(self._vote_type_rank)
        # round 1 preferred; NaN rounds treated as best
        df["_round_rank"] = df["round"].apply(lambda r: 0 if pd.isna(r) else int(r))
        df = df.sort_values(["_vt_rank", "_round_rank"])
        df = df.groupby(["country_code", "election_date"], as_index=False).first()
        df.drop(columns=["_vt_rank", "_round_rank"], inplace=True)
        return df.reset_index(drop=True)

    def _first_round_ballots(self, df: pd.DataFrame,
                             pick_vote_type: bool = True) -> pd.DataFrame:
        """Reduce an election set to one canonical ballot per contest.

        A contest split across several files is collapsed to a single row:
        later rounds are dropped (a two-round contest is represented by its
        first round), and when several vote types share one election date
        (e.g. the German first/second vote) the one preferred for cross-country
        comparison is kept. This is the default selection used by
        :meth:`results` when the caller does not pin a ``round``/``vote_type``.
        """
        df = df.copy()
        if "round" in df.columns:
            df = df[df["round"].isna() | (df["round"] <= 1)]
        if pick_vote_type and "vote_type" in df.columns:
            df["_vt_rank"] = df["vote_type"].apply(self._vote_type_rank)
            df = (df.sort_values("_vt_rank")
                    .groupby(["country_code", "election_date"], as_index=False)
                    .first()
                    .drop(columns="_vt_rank"))
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Party attribute resolution
    # ------------------------------------------------------------------

    def _lookup_party_row(self, party_id: str, country: str | None) -> pd.Series | None:
        """Return the best-matching parties.csv row for (party_id, country)."""
        df = self.parties
        candidates = df[df["party_id"] == party_id]
        if candidates.empty:
            return None
        if country and "region" in df.columns:
            country_match = candidates[candidates["region"] == country]
            if not country_match.empty:
                return country_match.iloc[0]
        return candidates.iloc[0]

    def _ideology(self, party_id: str, country: str | None = None) -> str:
        key = (party_id, country)
        if key not in self._ideology_cache:
            row = self._lookup_party_row(party_id, country)
            self._ideology_cache[key] = (
                str(row["ideology"]) if row is not None else "unknown"
            )
        return self._ideology_cache[key]

    def _ep_group(self, party_id: str, year: int, country: str | None = None) -> str:
        key = (party_id, year, country)
        if key not in self._ep_group_cache:
            row = self._lookup_party_row(party_id, country)
            if row is None:
                self._ep_group_cache[key] = "unknown"
            else:
                raw = row["ep_group"]
                try:
                    spells = json.loads(str(raw)) if pd.notna(raw) else []
                except (ValueError, TypeError):
                    spells = []
                found = "unknown"
                for spell in spells:
                    y0 = int(str(spell.get("year_start") or 0)[:4] or 0)
                    ye_raw = spell.get("year_end")
                    y1 = int(str(ye_raw)[:4]) if ye_raw else 9999
                    if y0 <= year < y1:
                        found = str(spell.get("party_id") or "unknown")
                        break
                self._ep_group_cache[key] = found
        return self._ep_group_cache[key]

    def _eu_party(self, party_id: str, year: int, country: str | None = None) -> str:
        key = (party_id, year, country)
        if key not in self._eu_party_cache:
            row = self._lookup_party_row(party_id, country)
            if row is None:
                self._eu_party_cache[key] = "unknown"
            else:
                raw = row["european_party"]
                try:
                    spells = json.loads(str(raw)) if pd.notna(raw) else []
                except (ValueError, TypeError):
                    spells = []
                found = "unknown"
                for spell in spells:
                    d0 = str(spell.get("date_start") or "")[:4]
                    d1 = str(spell.get("date_end") or "")[:4]
                    y0 = int(d0) if d0.isdigit() else 0
                    y1 = int(d1) if d1.isdigit() else 9999
                    if y0 <= year < y1:
                        found = str(spell.get("organization") or "unknown")
                        break
                self._eu_party_cache[key] = found
        return self._eu_party_cache[key]

    def _group_label(self, party_id: str, year: int, aggregate_by: str,
                     country: str | None = None) -> str:
        if aggregate_by == "ideology":
            return self._ideology(party_id, country)
        if aggregate_by == "ep_group":
            return self._ep_group(party_id, year, country)
        if aggregate_by == "eu_party":
            return self._eu_party(party_id, year, country)
        return party_id

    # ------------------------------------------------------------------
    # Election index filtering
    # ------------------------------------------------------------------

    def elections(
        self,
        countries: list[str] | str | None = None,
        election_type: str | None = None,
        years: list[int] | int | None = None,
        vote_type: str | None = None,
        round: int | float | None = None,
    ) -> pd.DataFrame:
        """
        Return a filtered view of the election index.

        Parameters
        ----------
        countries : ISO-2 code(s) to include, e.g. ``["FR", "DE"]``.
        election_type : ``"national"`` or ``"european"``.
        years : calendar year(s) of the election.
        vote_type : e.g. ``"party"``, ``"single_member"``, ``"Zweitstimme"``.
                    Pass ``None`` to include all vote types.
        round : election round (1 or 2). ``None`` includes all.

        Returns
        -------
        pd.DataFrame with the same columns as ``index``.
        """
        df = self.index.copy()
        if countries is not None:
            if isinstance(countries, str):
                countries = [countries]
            df = df[df["country_code"].isin(countries)]
        if election_type is not None:
            df = df[df["election_type"] == election_type]
        if years is not None:
            if isinstance(years, int):
                years = [years]
            df = df[df["year"].isin(years)]
        if vote_type is not None:
            df = df[df["vote_type"].fillna("") == vote_type]
        if round is not None:
            df = df[df["round"] == float(round)]
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Results loading
    # ------------------------------------------------------------------

    def results(
        self,
        countries: list[str] | str | None = None,
        election_type: str | None = None,
        years: list[int] | int | None = None,
        vote_type: str | None = None,
        round: int | float | None = None,
        latest_per_country: bool = False,
        latest_per_country_year: bool = False,
        elections_df: pd.DataFrame | None = None,
        geo_level: str = "LAU",
        aggregate_by: str | None = None,
    ) -> pd.DataFrame:
        """
        Load election results and return a wide DataFrame.

        Row granularity
        ---------------
        One row per (election file, geographic unit at *geo_level*).

        Columns
        -------
        ``country_code``, ``election_date``, ``year``, ``election_type``,
        ``geo_id``, ``geo_name``, ``geo_level``,
        ``registered``, ``turnout``, ``invalid``, ``blank``,
        then one column per party (or per group when *aggregate_by* is set).

        Parameters
        ----------
        countries, election_type, years, vote_type, round :
            Passed to :meth:`elections` unless *elections_df* is given.
        latest_per_country : bool
            Keep only the single most recent election per country.
        latest_per_country_year : bool
            Keep only the most recent election per (country, year).
            Useful when a country had two elections in the same year.
        elections_df : pd.DataFrame | None
            Provide a pre-filtered election index to use instead.
        geo_level : str
            Geographic granularity of rows:
            ``"LAU"`` (default), ``"NUTS3"``, ``"NUTS2"``, ``"NUTS1"``, ``"NUTS0"``.
        aggregate_by : str | None
            Collapse party columns into groups:
            ``"ideology"``, ``"ep_group"``, ``"eu_party"``, or ``None``.

        Returns
        -------
        pd.DataFrame
        """
        if elections_df is not None:
            elecs = elections_df.copy()
        else:
            elecs = self.elections(
                countries=countries,
                election_type=election_type,
                years=years,
                vote_type=vote_type,
                round=round,
            )
            # Default to the first round of each contest (and the preferred
            # vote type) unless the caller pinned one explicitly, so that
            # `latest_per_country` selects e.g. the French first round rather
            # than the runoff.
            if round is None:
                elecs = self._first_round_ballots(
                    elecs, pick_vote_type=(vote_type is None))

        if latest_per_country_year:
            elecs = (
                elecs.sort_values("election_date")
                .groupby(["country_code", "year"], as_index=False)
                .last()
            )
        elif latest_per_country:
            elecs = (
                elecs.sort_values("election_date")
                .groupby("country_code", as_index=False)
                .last()
            )

        parts: list[pd.DataFrame] = []
        for _, row in elecs.iterrows():
            try:
                parts.append(self._load_one(row, geo_level, aggregate_by))
            except Exception as exc:
                warnings.warn(f"Skipping {row['file_path']}: {exc}")

        if not parts:
            return pd.DataFrame()
        return pd.concat(parts, ignore_index=True)

    # ------------------------------------------------------------------
    # Internal: load one election file
    # ------------------------------------------------------------------

    def _load_one(
        self,
        row: pd.Series,
        geo_level: str,
        aggregate_by: str | None,
    ) -> pd.DataFrame:
        path = self.dist / row["file_path"]
        df = pd.read_csv(path, dtype={"gisco_id": str}, low_memory=False)

        party_cols = [c for c in df.columns if c not in _META]
        year = int(row["year"])

        # Coerce all numeric columns
        for c in party_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
        for c in ("registered", "turnout", "invalid", "blank"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

        # --- Party grouping --------------------------------------------------
        country = str(row.get("country_code", "") or "")

        if aggregate_by is not None:
            # Build {group_label: sum_of_member_votes} per row
            group_map: dict[str, list[str]] = {}
            for p in party_cols:
                lbl = self._group_label(p, year, aggregate_by, country)
                group_map.setdefault(lbl, []).append(p)
            group_series = {
                lbl: df[members].sum(axis=1)
                for lbl, members in group_map.items()
            }
            # Drop party cols and attach group cols in one concat
            df = pd.concat(
                [df.drop(columns=party_cols), pd.DataFrame(group_series, index=df.index)],
                axis=1,
            )
            value_cols = list(group_map.keys())
        else:
            value_cols = party_cols

        # --- Geo aggregation -------------------------------------------------
        if geo_level in ("NUTS0", "NUTS1", "NUTS2", "NUTS3"):
            gc = self.geo_chain
            def _resolve(g: str) -> str:
                # Time-aware: pick the LAU's parent NUTS3 valid in the election
                # year, since a reused gisco_id can belong to different NUTS
                # regions in different periods. Fall back to the flat chain when
                # no per-period parent applies (non-LAU units, uncovered years).
                parent = self._lau_parent_at(g, year)
                if parent:
                    parent_chain = gc.get(parent)
                    if parent_chain:
                        nuts_id = parent_chain.get(geo_level, "")
                    else:
                        nuts_id = parent if geo_level == "NUTS3" else ""
                else:
                    nuts_id = gc.get(g, {}).get(geo_level, "")
                return self._active_nuts(nuts_id, year) if nuts_id else ""
            df["__geo"] = df["gisco_id"].apply(_resolve)
            df = df[df["__geo"] != ""].copy()

            num_cols = [c for c in ("registered", "turnout", "invalid", "blank") if c in df.columns]
            df = (
                df.groupby("__geo", as_index=False)[num_cols + value_cols]
                .sum()
            )
            df.rename(columns={"__geo": "geo_id"}, inplace=True)
            df["geo_name"] = ""
        else:
            df.rename(columns={"gisco_id": "geo_id"}, inplace=True)
            if "name" in df.columns:
                df.rename(columns={"name": "geo_name"}, inplace=True)
            else:
                df["geo_name"] = ""

        # --- Attach election metadata ----------------------------------------
        df.insert(0, "election_type", row["election_type"])
        df.insert(0, "year", year)
        df.insert(0, "election_date", row["election_date"])
        df.insert(0, "country_code", row["country_code"])
        df["geo_level"] = geo_level

        # Reorder: metadata first, geo, stats, then values
        stat_cols = [c for c in ("registered", "turnout", "invalid", "blank") if c in df.columns]
        front = ["country_code", "election_date", "year", "election_type",
                 "geo_id", "geo_name", "geo_level"] + stat_cols
        rest = [c for c in df.columns if c not in front]
        return df[front + rest]

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def vote_shares(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Append ``<col>_pct`` columns to *df* for each party/group column,
        expressing each as a percentage of the sum of all party/group votes
        in that row.

        Operates on a DataFrame previously returned by :meth:`results`.
        Modifies a copy; does not mutate the input.
        """
        df = df.copy()
        # Identify value columns (not metadata)
        meta_like = {
            "country_code", "election_date", "year", "election_type",
            "geo_id", "geo_name", "geo_level",
            "registered", "turnout", "invalid", "blank",
        }
        val_cols = [c for c in df.columns if c not in meta_like]
        total = df[val_cols].sum(axis=1).replace(0, float("nan"))
        for c in val_cols:
            df[f"{c}_pct"] = df[c] / total * 100
        return df

    def fill_forward(
        self,
        df: pd.DataFrame,
        years: range | list[int] | None = None,
    ) -> pd.DataFrame:
        """
        Expand *df* (one row per election) so every (country, year) pair
        in *years* has a row, using the most recent past election for years
        with no election.

        The input must have ``country_code`` and ``year`` columns.
        Useful for timeline charts where you want a continuous series.
        """
        if years is None:
            y_min = int(df["year"].min())
            y_max = int(df["year"].max())
            years = range(y_min, y_max + 1)

        countries = df["country_code"].unique()
        parts: list[pd.DataFrame] = []
        for cc in countries:
            sub = df[df["country_code"] == cc].sort_values("year")
            # Re-index to all requested years, forward-fill
            sub = sub.set_index("year").reindex(years).ffill()
            sub.index.name = "year"
            sub = sub.reset_index()
            sub["country_code"] = cc
            parts.append(sub)
        return pd.concat(parts, ignore_index=True)
