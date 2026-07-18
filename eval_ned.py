#!/usr/bin/env python3
"""
eval_ned.py
===========
Cross-validate BLUE_DB electoral results against EU-NED.

For each election the finest NUTS level available in EU-NED is used:
  - NUTS 3 for most countries
  - NUTS 2 for BE, GB, IE, NL, PL (and some ES / GR years)
  - NUTS 1 for SI

BLUE_DB results are aggregated to that same NUTS level before comparison.

Output
------
A flat CSV table with one row per comparison cell:

  country_code, year, nuts_level, nuts_id, metric,
  blu_value, ned_value, diff, reldiff_pct

where metric ∈ {registered, turnout, invalid_blank, <party_name> (<pfid>)}.

Usage
-----
    python eval_ned.py                    # report + eval_ned_results.csv
    python eval_ned.py --csv custom.csv   # custom output path
    python eval_ned.py --no-csv           # report only
"""

import sys, argparse, warnings
from pathlib import Path


import numpy as np
import pandas as pd

from blue_db import BlueDB

NED_PATH    = Path("resources/EU-NED/eu_ned_national.csv")
NED_EP_PATH = Path("resources/EU-NED/eu_ned_ep.csv")
DEFAULT_CSV = Path("eval_ned_results.csv")
# (BLUE_DB election_type, EU-NED file, short label) per contest type
CONTESTS    = [("national", NED_PATH,    "national"),
               ("european", NED_EP_PATH, "EP")]
NUTS_FILES  = [Path("resources/nuts_1.csv"),
               Path("resources/nuts_2.csv"),
               Path("resources/nuts_3.csv")]
COMMUNES_FILE = Path("resources/communes.csv")


# ── Helpers ───────────────────────────────────────────────────────────────────

def safe_reldiff(blu, ned):
    if ned == 0 or (isinstance(ned, float) and np.isnan(ned)):
        return np.nan
    return float((blu - ned) / ned * 100)


def signed_min(a, b):
    """The signed value of whichever of a, b has the smaller absolute value."""
    av = abs(a) if pd.notna(a) else np.inf
    bv = abs(b) if pd.notna(b) else np.inf
    if av == np.inf and bv == np.inf:
        return np.nan
    return a if av <= bv else b


def hr(c="─", w=72):
    print(c * w)


# ── NUTS vintage alignment ────────────────────────────────────────────────────
# BLUE_DB emits codes from the latest NUTS vintage (2024), while EU-NED stores an
# older `nuts2016`-flavoured set.  Countries that renumbered their NUTS regions
# between vintages (FI, HR, NO, …) therefore line up on only a fraction of codes
# by raw string match, which silently drops regions from the comparison.
#
# `nuts_{1,2,3}.csv` is a temporal registry: every code carries the `predecessors`
# it descended from.  Walking that chain back to its root ancestors gives a
# vintage-independent identity.  Within one election we union all codes that share
# a root (connected components), so renames, merges and splits all collapse onto a
# common comparison cell — computed per election to avoid over-merging history
# that never co-occurs.

def _nuts_split_edges(pred: dict[str, list[str]], nuts_parent: dict[str, str],
                      obsolete: set[str]) -> set[tuple[str, str]]:
    """Recover NUTS *split* lineage that the `predecessors` field omits.

    BLUE_DB keeps the obsolete pre-split NUTS code on its older LAU units and
    normalises it through a 1->1 successor map, so a region that split (e.g. the
    NUTS3 PL226 -> PL228/PL229/PL22A/PL22B/PL22C, or the NUTS2 PL12 -> PL91/PL92)
    collapses onto a single successor on the BLUE side. EU-NED instead reports
    every successor, so the two sides only reconcile once all of a split's
    descendants share one comparison group. The raw `predecessors` field does not
    give us that grouping directly:

      * some successors omit the link entirely (PL229/PL22A/PL22C never name
        PL226), and
      * coarser levels are never linked (PL91 never names PL12, even though its
        NUTS3 child PL912 names the PL12-side PL129).

    We close both gaps. (1) From BLUE's own municipality history: when a gmina's
    NUTS parent differs from that of its predecessor unit, the two NUTS codes
    cover the same ground. (2) By lifting every predecessor link (gmina- or
    nuts-derived) up the NUTS parent chain, so an established NUTS3 lineage also
    links the NUTS2/NUTS1 codes above it. Only obsolete (retired) old codes are
    linked, so plain boundary moves between two still-active regions are left
    untouched.

    Returns a set of (current_code, obsolete_code) edges to add to `pred`.
    """
    base: set[tuple[str, str]] = set()

    # (1) NUTS3 splits visible only in BLUE's gmina history.
    if COMMUNES_FILE.exists():
        com = pd.read_csv(COMMUNES_FILE, dtype=str, na_values=[''], keep_default_na=False)
        parent_of = dict(zip(com["id"], com["parent"]))      # LAU -> NUTS3
        for new_nuts, preds in zip(com["parent"], com["predecessors"]):
            if not isinstance(new_nuts, str) or not new_nuts \
               or not isinstance(preds, str) or not preds:
                continue
            for p in preds.split(","):
                old_nuts = parent_of.get(p.strip())
                if old_nuts and old_nuts != new_nuts and old_nuts in obsolete:
                    base.add((new_nuts, old_nuts))

    # (2) Every predecessor link recorded in the NUTS files.
    for new_code, olds in pred.items():
        for old in olds:
            if old and old != new_code:
                base.add((new_code, old))

    def chain(code: str) -> list[str]:
        out, seen = [], set()
        while code and code not in seen:
            seen.add(code); out.append(code); code = nuts_parent.get(code, "")
        return out

    # Lift each edge up the parent chain so coarser levels group too.
    edges: set[tuple[str, str]] = set()
    for a, b in base:
        for la, lb in zip(chain(a), chain(b)):
            if la != lb and len(la) == len(lb) and lb in obsolete:
                edges.add((la, lb))
    return edges


def build_nuts_roots(files=NUTS_FILES):
    """Return roots(code) -> frozenset of earliest ancestor codes (cycle-safe)."""
    pred: dict[str, list[str]] = {}
    nuts_parent: dict[str, str] = {}
    obsolete: set[str] = set()
    for f in files:
        df = pd.read_csv(f, dtype=str, na_values=[''], keep_default_na=False)
        for code, p, par, end in zip(df["id"], df["predecessors"],
                                     df["parent"], df["end_date"]):
            pred[code] = ([] if pd.isna(p) or not str(p).strip()
                          else [x.strip() for x in str(p).split(",")])
            if isinstance(par, str) and par.strip():
                nuts_parent[code] = par.strip()
            if isinstance(end, str) and end.strip():
                obsolete.add(code)
    # Augment the predecessor graph with split lineage recovered from BLUE's own
    # municipality history (see _nuts_split_edges), so EU-NED's separately
    # reported successors group with the single code BLUE collapses them onto.
    for new_code, old_code in _nuts_split_edges(pred, nuts_parent, obsolete):
        bucket = pred.setdefault(new_code, [])
        if old_code not in bucket:
            bucket.append(old_code)
    cache: dict[str, frozenset] = {}

    def roots(code: str) -> frozenset:
        if code in cache:
            return cache[code]
        out, stack, seen = set(), [code], set()
        while stack:
            c = stack.pop()
            if c in seen:                 # guards self/loop references (e.g. NO0)
                continue
            seen.add(c)
            ps = pred.get(c)
            if ps:
                stack.extend(ps)
            else:
                out.add(c)
        res = frozenset(out or {code})
        cache[code] = res
        return res

    return roots


def _components(codes, roots) -> dict[str, str]:
    """Group codes into lineage components; label = '+'.join(sorted roots)."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        r = x
        while parent[r] != r:
            r = parent[r]
        while parent[x] != r:
            parent[x], x = r, parent[x]
        return r

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    code_roots = {c: list(roots(c)) for c in codes}
    for rs in code_roots.values():
        for r in rs:
            union(rs[0], r)

    # Also union codes that stand in a NUTS prefix (parent/child) relation, so a
    # region EU-NED reports only at a coarse level groups with the finer regions
    # BLUE_DB aggregates beneath it (e.g. NED's EL30 with BLUE's EL301…EL307).
    # NUTS codes are hierarchical by string prefix, so a strict prefix within one
    # country is always a genuine ancestor — never a coincidental match.
    code_list = list(codes)
    for i, a in enumerate(code_list):
        sa = str(a)
        for b in code_list[i + 1:]:
            sb = str(b)
            if sa != sb and (sa.startswith(sb) or sb.startswith(sa)):
                union(code_roots[a][0], code_roots[b][0])

    members: dict[str, set] = {}
    for rs in code_roots.values():
        members.setdefault(find(rs[0]), set()).update(rs)
    return {c: "+".join(sorted(members[find(rs[0])]))
            for c, rs in code_roots.items()}


def build_component_table(reg: pd.DataFrame, roots) -> pd.DataFrame:
    """Map (country, year, level, nuts_id) -> nuts_grp, components per election."""
    parts = []
    for (cc, yr, lvl), g in reg.groupby(["country_code", "year", "finest_level"]):
        cmap = _components(g["nuts_id"].dropna().unique().tolist(), roots)
        parts.append(pd.DataFrame({
            "country_code": cc, "year": yr, "finest_level": lvl,
            "nuts_id": list(cmap.keys()), "nuts_grp": list(cmap.values()),
        }))
    return pd.concat(parts, ignore_index=True)


# ── Excel workbook ────────────────────────────────────────────────────────────

def write_discrepancy_workbook(path: Path, result: pd.DataFrame,
                               nat: pd.DataFrame,
                               nat_party: pd.DataFrame,
                               coverage: pd.DataFrame | None = None) -> dict[str, int]:
    """Write an .xlsx with one sheet per discrepancy category. Each sheet lists
    *every* measured cell (not just flagged ones), sorted by severity, where
    severity is uniformly the relative delta % = (BLUE − EU-NED) / EU-NED · 100.

    National sheets carry the BLUE_DB total both WITH and WITHOUT foreign-based
    voters, and are ranked by the smaller of the two deltas (`delta_min`), since
    EU-NED's electorate convention (abroad in/out) varies by country."""

    def by_severity(df: pd.DataFrame, key: str = "delta_%") -> pd.DataFrame:
        return (df.assign(_k=df[key].abs())
                  .sort_values("_k", ascending=False)
                  .drop(columns="_k")
                  .reset_index(drop=True))

    sheets: dict[str, pd.DataFrame] = {}

    # Per-region administrative categories (from `result`)
    region_cats = {
        "registered":    "Registered (region)",
        "turnout":       "Turnout (region)",
        "invalid_blank": "Invalid+blank (region)",
    }
    # For each regional cell, flag whether the country's NATIONAL total for the
    # same election+metric is ALSO off by >1%. When True, the discrepancy is a
    # country-level composition/definition issue (e.g. AT postal "Wahlkarten"
    # inflating turnout) rather than a regional misallocation that nets to zero.
    _nat_min_col = {"registered": "reg_diff_min%",
                    "turnout": "turn_diff_min%",
                    "invalid_blank": "invbl_diff_min%"}
    _nat_lut: dict[tuple, pd.Series] = {}
    for _, _nr in nat.iterrows():
        _nat_lut[(_nr["type"], _nr["country_code"], int(_nr["year"]))] = _nr

    def _country_total_off(row: pd.Series, col: str) -> "bool | None":
        nr = _nat_lut.get((row["type"], row["country_code"], int(row["year"])))
        if nr is None or pd.isna(nr.get(col)):
            return None
        return bool(abs(nr[col]) > 1)

    for metric, sheet in region_cats.items():
        d = (result[result["metric"] == metric]
             .dropna(subset=["reldiff_pct"])
             [["type", "country_code", "year", "nuts_level", "nuts_id",
               "blu_value", "ned_value", "diff", "reldiff_pct"]]
             .rename(columns={"reldiff_pct": "delta_%"}))
        _col = _nat_min_col[metric]
        d["country_total_off_>1%"] = d.apply(
            lambda r, c=_col: _country_total_off(r, c), axis=1)
        sheets[sheet] = by_severity(d)

    # Party vote shares (blu/ned in %, diff in pp)
    party = (result[~result["metric"].isin(
                 ["registered", "turnout", "invalid_blank"])]
             .dropna(subset=["reldiff_pct"])
             [["type", "country_code", "year", "nuts_level", "nuts_id", "metric",
               "blu_value", "ned_value", "diff", "reldiff_pct"]]
             .rename(columns={"metric": "party", "blu_value": "blu_share_%",
                              "ned_value": "ned_share_%", "diff": "diff_pp",
                              "reldiff_pct": "delta_%"}))
    # For each regional party cell, flag whether that party's NATIONAL share for the
    # same election is ALSO off by >1 pp (best of the with/without-foreign deltas).
    # True → a country-wide share gap (party mapping / abroad-vote convention);
    # False → a purely regional reallocation that cancels out nationally.
    _npty_lut: dict[tuple, float] = {}
    for _, _pr in nat_party.iterrows():
        _npty_lut[(_pr["type"], _pr["country_code"], int(_pr["year"]),
                   _pr["party"])] = _pr["delta_min_pp"]
    def _party_country_off(row: pd.Series) -> "bool | None":
        v = _npty_lut.get((row["type"], row["country_code"], int(row["year"]),
                           row["party"]))
        if v is None or pd.isna(v):
            return None
        return bool(abs(v) > 1)
    party["country_total_off_>1pp"] = party.apply(_party_country_off, axis=1)
    # shares are already percentages → severity is the percentage-point gap
    sheets["Party shares"] = by_severity(party, key="diff_pp")

    # National admin totals — BLUE_DB with foreign (LAU) and without (domestic),
    # ranked by the smaller of the two deltas.
    national = {
        "Registered (national)":   ("nat_registered","dom_registered","ned_registered",
                                     "reg_diff%","reg_diff_excl%","reg_diff_min%"),
        "Turnout (national)":      ("nat_turnout","dom_turnout","ned_turnout",
                                     "turn_diff%","turn_diff_excl%","turn_diff_min%"),
        "Invalid+blank (national)":("nat_invbl","dom_invbl","ned_invbl",
                                     "invbl_diff%","invbl_diff_excl%","invbl_diff_min%"),
    }
    for sheet, (bf, bd, nc, d, de, dm) in national.items():
        col = (nat[["type", "country_code", "year", bf, bd, nc, d, de, dm]]
               .dropna(subset=[dm])
               .rename(columns={bf: "blu_value", bd: "blu_value_excl_foreign",
                                nc: "ned_value", d: "delta_%",
                                de: "delta_excl_foreign_%", dm: "delta_min_%"}))
        sheets[sheet] = by_severity(col, key="delta_min_%")

    # National party vote shares — WITH foreign vs WITHOUT, ranked by min pp gap
    npy = (nat_party[["type", "country_code", "year", "party",
                      "blu_share_for", "blu_share_excl", "ned_share",
                      "delta_pp", "delta_excl_pp", "delta_min_pp"]]
           .dropna(subset=["delta_min_pp"])
           .rename(columns={"blu_share_for": "blu_share_%",
                            "blu_share_excl": "blu_share_excl_foreign_%",
                            "ned_share": "ned_share_%",
                            "delta_excl_pp": "delta_excl_foreign_pp"}))
    sheets["Party shares (national)"] = by_severity(npy, key="delta_min_pp")

    # Paper-ready agreement statistics, written as the first sheet(s).
    summary = cross_eval_summary(result, nat, nat_party)
    ordered = {"Cross-eval summary": summary}
    if coverage is not None:
        ordered["Election coverage"] = coverage.sort_values(
            ["type", "country_code", "year"]).reset_index(drop=True)
    ordered.update(sheets)

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in ordered.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
    return {name: len(df) for name, df in ordered.items()}


def cross_eval_summary(result: pd.DataFrame, nat: pd.DataFrame,
                       nat_party: pd.DataFrame) -> pd.DataFrame:
    """General BLUE_DB↔EU-NED agreement statistics for the paper's
    cross-evaluation section.

    One row per (scope, metric, contest). `scope` is the geographic level of the
    comparison (`region` = finest NUTS cell available per election, `national` =
    country total). For every metric it reports the share of comparison units that
    agree within a tight and a wide tolerance, the mean absolute error in the
    metric's native unit, and the BLUE/EU-NED linear r². Administrative counts use
    the relative gap (%, native units of reldiff); vote shares use the
    percentage-point gap (pp). National figures use the smaller of the with- and
    without-foreign deltas, matching the discrepancy workbook.

    `contest` breaks results out by `national` and `EP`, with an `All` roll-up."""
    def agree(abs_series: pd.Series, tol: float) -> tuple[float, int]:
        s = abs_series.dropna()
        return (round((s <= tol).mean() * 100, 1) if len(s) else np.nan, len(s))

    def r2(df: pd.DataFrame, a: str, b: str) -> float:
        sub = df[[a, b]].dropna()
        if len(sub) < 2:
            return np.nan
        c = sub[a].corr(sub[b])
        return round(c * c, 4)

    contests = [("national", "national"), ("EP", "EP"), ("All", None)]
    rows: list[dict] = []

    def emit(scope, metric, cname, n, l1, p1, l2, p2, mae, r2v):
        rows.append({"scope": scope, "metric": metric, "contest": cname, "n": n,
                     "tol_tight": l1, "agree_tight_%": p1,
                     "tol_wide": l2, "agree_wide_%": p2,
                     "MAE": mae, "r2": r2v})

    # ── Regional admin metrics (relative % gap) ───────────────────────────
    admin = {"registered": ("1%", 1, "5%", 5), "turnout": ("1%", 1, "5%", 5),
             "invalid_blank": ("5%", 5, "20%", 20)}
    for metric, (l1, t1, l2, t2) in admin.items():
        for cname, cfilt in contests:
            sub = result[result["metric"] == metric]
            if cfilt:
                sub = sub[sub["type"] == cfilt]
            a = sub["reldiff_pct"].abs()
            p1, n = agree(a, t1)
            p2, _ = agree(a, t2)
            emit("region", metric, cname, n, l1, p1, l2, p2,
                 round(a.mean(), 3) if n else np.nan, r2(sub, "blu_value", "ned_value"))

    # ── Regional party shares (pp gap) ────────────────────────────────────
    pmask = ~result["metric"].isin(["registered", "turnout", "invalid_blank"])
    for cname, cfilt in contests:
        sub = result[pmask].dropna(subset=["diff"])
        if cfilt:
            sub = sub[sub["type"] == cfilt]
        a = sub["diff"].abs()
        p1, n = agree(a, 1)
        p2, _ = agree(a, 2)
        emit("region", "party_share", cname, n, "1pp", p1, "2pp", p2,
             round(a.mean(), 3) if n else np.nan, r2(sub, "blu_value", "ned_value"))

    # ── National admin totals (relative % gap, min of with/without foreign) ─
    natadm = {"registered": ("reg_diff_min%", "nat_registered", "ned_registered", "1%", 1, "5%", 5),
              "turnout": ("turn_diff_min%", "nat_turnout", "ned_turnout", "1%", 1, "5%", 5),
              "invalid_blank": ("invbl_diff_min%", "nat_invbl", "ned_invbl", "5%", 5, "20%", 20)}
    for metric, (dcol, bcol, ncol, l1, t1, l2, t2) in natadm.items():
        for cname, cfilt in contests:
            sub = nat if cfilt is None else nat[nat["type"] == cfilt]
            a = sub[dcol].abs()
            p1, n = agree(a, t1)
            p2, _ = agree(a, t2)
            emit("national", metric, cname, n, l1, p1, l2, p2,
                 round(a.mean(), 3) if n else np.nan, r2(sub, bcol, ncol))

    # ── National party shares (pp gap, min of with/without foreign) ────────
    for cname, cfilt in contests:
        sub = nat_party if cfilt is None else nat_party[nat_party["type"] == cfilt]
        a = sub["delta_min_pp"].abs()
        p1, n = agree(a, 1)
        p2, _ = agree(a, 2)
        emit("national", "party_share", cname, n, "1pp", p1, "2pp", p2,
             round(a.mean(), 3) if n else np.nan, r2(sub, "blu_share_for", "ned_share"))

    return pd.DataFrame(rows)


# ── National LAU aggregates (memory-safe) ──────────────────────────────────────
# Loading every matched election at LAU level in one `db.results` call builds a
# frame whose columns are the *union* of all countries' party ids — ~420k
# municipality rows × ~1800 mostly-empty party columns (≈ 6 GB) — and the later
# party `melt` expands that to ~0.8 billion rows, which OOM-kills the process
# (and can take the editor down with it). Each election only populates its own
# country's parties, so we reduce one election at a time to small per-(country,
# year) rows and concatenate those, keeping peak memory to a single election.

def lau_national_aggregates(db: BlueDB, matched: pd.DataFrame, pf_map: dict,
                            meta: set) -> tuple[pd.DataFrame, ...]:
    """Reduce each matched election from LAU to national per-(country, year) rows.

    Returns (blu_nat, votes_for, valid_for, abroad_nat, abroad_votes):

      blu_nat      : country_code, year, nat_registered, nat_turnout, nat_invbl
      votes_for    : country_code, year, pfid, blu_votes_for
      valid_for    : country_code, year, blu_valid_for
      abroad_nat   : country_code, year, ab_registered, ab_turnout, ab_invbl, ab_valid
      abroad_votes : country_code, year, pfid, ab_votes

    `blu_nat`/`votes_for`/`valid_for` are full LAU sums (INCLUDING abroad). The
    `abroad_*` frames isolate the "Voters Abroad" units (e.g. Spanish C.E.R.E.):
    these carry a *home NUTS region* as parent, so the NUTS-aggregated `blu`
    frame folds them into the home regions instead of dropping them. The caller
    subtracts `abroad_*` from `blu`-derived totals to get genuine WITHOUT-foreign
    figures.
    """
    abroad_ids = set(db.special.loc[db.special["type"] == "Voters Abroad",
                                    "gisco_id"].astype(str))

    def _reduce(frame: pd.DataFrame, pcols: list[str]):
        """(registered, turnout, invbl, valid, {pfid: votes}) for a row subset."""
        def s(col):
            return float(pd.to_numeric(frame[col], errors="coerce").fillna(0).sum()) \
                   if col in frame.columns else 0.0
        votes = frame[pcols].apply(pd.to_numeric, errors="coerce").fillna(0)
        by_pf: dict[int, float] = {}
        for pid, v in votes.sum(axis=0).items():
            pf = pf_map.get(pid)
            if pf is not None:
                by_pf[pf[0]] = by_pf.get(pf[0], 0.0) + float(v)
        return s("registered"), s("turnout"), s("invalid") + s("blank"), \
               float(votes.to_numpy().sum()), by_pf

    nat_rows, vote_rows, valid_rows = [], [], []
    ab_nat_rows, ab_vote_rows = [], []
    seen_pfids: set[int] = set()
    for i in range(len(matched)):
        one = db.results(elections_df=matched.iloc[[i]],
                         geo_level="LAU", aggregate_by="party")
        if one.empty:
            continue
        cc = str(one["country_code"].iloc[0])
        yr = int(one["year"].iloc[0])
        pcols = [c for c in one.columns if c not in meta]

        reg, tur, inv, val, by_pf = _reduce(one, pcols)
        nat_rows.append({"country_code": cc, "year": yr, "nat_registered": reg,
                         "nat_turnout": tur, "nat_invbl": inv})
        valid_rows.append({"country_code": cc, "year": yr, "blu_valid_for": val})
        for pfid, v in by_pf.items():
            seen_pfids.add(pfid)
            vote_rows.append({"country_code": cc, "year": yr,
                              "pfid": pfid, "blu_votes_for": v})

        a_reg, a_tur, a_inv, a_val, a_by_pf = _reduce(
            one[one["geo_id"].astype(str).isin(abroad_ids)], pcols)
        ab_nat_rows.append({"country_code": cc, "year": yr,
                            "ab_registered": a_reg, "ab_turnout": a_tur,
                            "ab_invbl": a_inv, "ab_valid": a_val})
        for pfid, v in a_by_pf.items():
            ab_vote_rows.append({"country_code": cc, "year": yr,
                                 "pfid": pfid, "ab_votes": v})

    blu_nat = (pd.DataFrame(nat_rows)
               .groupby(["country_code", "year"], as_index=False).sum())
    valid_for = (pd.DataFrame(valid_rows)
                 .groupby(["country_code", "year"], as_index=False).sum())
    votes_for = (pd.DataFrame(vote_rows)
                 .groupby(["country_code", "year", "pfid"], as_index=False).sum())
    abroad_nat = (pd.DataFrame(ab_nat_rows)
                  .groupby(["country_code", "year"], as_index=False).sum())
    abroad_votes = (pd.DataFrame(
                        ab_vote_rows,
                        columns=["country_code", "year", "pfid", "ab_votes"])
                    .groupby(["country_code", "year", "pfid"], as_index=False).sum())

    # Reproduce the old union-wide melt's zero entries: every election carried a
    # 0-vote row for *every* party id seen in any matched election (foreign party
    # columns were NaN→0 after the concat). This is what surfaces EU-NED parties
    # absent from BLUE_DB as `blu_share = 0` in the downstream inner join, so we
    # recreate the full (country, year) × pfid grid with zero fill.
    grid = (blu_nat[["country_code", "year"]]
            .merge(pd.Series(sorted(seen_pfids), name="pfid"), how="cross"))
    votes_for = (grid.merge(votes_for, on=["country_code", "year", "pfid"],
                            how="left")
                 .fillna({"blu_votes_for": 0.0}))
    return blu_nat, votes_for, valid_for, abroad_nat, abroad_votes


# ── Main ──────────────────────────────────────────────────────────────────────

def run_comparison(db: BlueDB, election_type: str, ned_path: Path,
                   label: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cross-validate one contest type; return (result, nat) tagged with `type`."""
    hr("═")
    print(f"BLUE_DB × EU-NED cross-validation — {label}")
    hr("═")

    # ── 1. Load EU-NED (all NUTS levels) ─────────────────────────────────
    ned_raw = pd.read_csv(ned_path, dtype={"partyfacts_id": "Int64"})#, na_values=[''], keep_default_na=False)
    # EU-NED codes Greece as ISO `GR`, whereas BLUE_DB (and Eurostat, and even
    # EU-NED's own `nuts2016` codes, e.g. EL30) use `EL`. Without this the
    # (country, year) match key never lines up and every Greek election is
    # silently dropped from the comparison. Normalise to `EL` at load so the
    # keys, the NUTS joins, and the coverage report are all consistent.
    ned_raw["country_code"] = ned_raw["country_code"].replace({"GR": "EL"})
    ned_raw["nuts_id"] = ned_raw["nuts2016"].str.strip()

    # Deepest NUTS level available per election (the level BLUE_DB is aggregated
    # to, and an election-level tag used to align the two sides).
    finest = (
        ned_raw.groupby(["country_code", "year"])["nutslevel"]
        .max()
        .reset_index()
        .rename(columns={"nutslevel": "finest_level"})
    )
    ned_raw = ned_raw.merge(finest, on=["country_code", "year"])

    # Keep a non-overlapping *cover* rather than a single finest level: for each
    # (country, year), keep every region that has no finer region nested under it
    # in the data. EU-NED reports some regions only at a coarser level than the
    # rest of the country — e.g. Greek Attica (EL30) is given at NUTS2 while the
    # other regions are at NUTS3, and likewise the Spanish islands. The old rule
    # of keeping only the single finest level silently dropped those regions,
    # which discarded ~29% of the Greek electorate (all of Attica) and inflated
    # BLUE_DB's national totals by up to ~40%. The cover keeps them at whatever
    # level EU-NED provides; BLUE_DB's finer regions are rolled up to match them
    # during lineage grouping (a coarse code groups with the finer codes nested
    # under it — see `_components`).
    cover: set[tuple] = set()
    for (cc, yr), g in ned_raw.groupby(["country_code", "year"]):
        codes = [c for c in g["nuts_id"].dropna().unique()]
        for c in codes:
            if not any(o != c and str(o).startswith(str(c)) for o in codes):
                cover.add((cc, yr, c))
    _keys = zip(ned_raw["country_code"], ned_raw["year"], ned_raw["nuts_id"])
    ned = ned_raw[[k in cover for k in _keys]].copy()

    # Administrative block: one row per (country, year, nuts_id)
    ned_admin = (
        ned.groupby(["country_code", "year", "finest_level", "nuts_id"],
                    as_index=False)
        .agg(
            ned_registered = ("electorate", "first"),
            ned_turnout    = ("totalvote",  "first"),
            ned_validvote  = ("validvote",  "first"),
        )
    )
    ned_admin["ned_invbl"] = ned_admin["ned_turnout"] - ned_admin["ned_validvote"]

    ned_keys = set(ned["country_code"] + "_" + ned["year"].astype(str))
    print(f"\nEU-NED elections (all levels): "
          f"{ned[['country_code','year']].drop_duplicates().shape[0]} elections, "
          f"{ned['country_code'].nunique()} countries, "
          f"{int(ned['year'].min())}–{int(ned['year'].max())}")

    # ── 2. Match BLUE_DB elections ────────────────────────────────────────
    canon = db.canonical_elections(election_type=election_type)
    # Two-round: keep round 1 (EU-NED convention)
    r1_keys = set(
        canon[canon["round"] == 1]
        .apply(lambda r: (r["country_code"], r["year"]), axis=1)
    )
    canon = canon[
        ~canon.apply(
            lambda r: r["round"] == 2
                      and (r["country_code"], r["year"]) in r1_keys,
            axis=1,
        )
    ].copy()
    canon["key"] = canon["country_code"] + "_" + canon["year"].astype(str)
    matched = canon[canon["key"].isin(ned_keys)].copy()

    # EU-NED stores one election per (country, year). When BLUE_DB has several
    # (e.g. ES 2019 held elections in April and November), keep the latest so the
    # year-key maps to a single election instead of summing both.
    dup_keys = matched["key"].value_counts()
    dup_keys = dup_keys[dup_keys > 1].index.tolist()
    if dup_keys:
        for k in dup_keys:
            dates = sorted(matched.loc[matched["key"] == k, "election_date"].astype(str))
            print(f"  ⚠  {k}: {len(dates)} elections {dates} → keeping latest")
        matched = (matched.sort_values("election_date")
                          .drop_duplicates("key", keep="last"))

    # Attach finest NUTS level per election
    matched = matched.merge(finest, on=["country_code", "year"], how="left")
    matched["finest_level"] = matched["finest_level"].fillna(3).astype(int)

    print(f"Matched elections: {len(matched)} "
          f"({matched['country_code'].nunique()} countries, "
          f"{int(matched['year'].min())}–{int(matched['year'].max())})")
    level_counts = matched.groupby("finest_level")["key"].count()
    for lvl, cnt in sorted(level_counts.items()):
        print(f"  NUTS{lvl}: {cnt} elections")

    # ── Election coverage: BLUE_DB vs EU-NED at (country, year) granularity ─
    # `compared` = present in both (used for the cross-validation above);
    # `blue_only` = in BLUE_DB but absent from EU-NED (no reference to check
    # against); `ned_only` = in EU-NED but not (yet) in BLUE_DB.
    #
    # A raw list of the missing keys is dominated by structural scope
    # differences (EU-NED starts in the 1980s and covers GB/TR; BLUE_DB starts
    # in 2001 and omits both), which are expected rather than gaps. We therefore
    # tag every missing election with the *reason* it is missing, so the report
    # separates the genuinely surprising gaps (a country/year both datasets are
    # meant to cover) from the structural ones.
    blue_keys = set(canon["key"])
    finest_lut = dict(zip(finest["country_code"] + "_" + finest["year"].astype(str),
                          finest["finest_level"]))

    blue_countries = set(canon["country_code"])
    ned_countries  = set(ned["country_code"])
    blue_min, blue_max = int(canon["year"].min()), int(canon["year"].max())
    ned_min,  ned_max  = int(ned["year"].min()),   int(ned["year"].max())

    def _reason(status: str, cc: str, yr: int) -> str:
        if status == "compared":
            return ""
        if status == "blue_only":
            # In BLUE_DB, no EU-NED reference to check against.
            if cc not in ned_countries:
                return "country not covered by EU-NED"
            if yr > ned_max:
                return f"after EU-NED coverage (>{ned_max})"
            if yr < ned_min:
                return f"before EU-NED coverage (<{ned_min})"
            return "missing from EU-NED (within scope)"
        # ned_only: in EU-NED but not in BLUE_DB.
        if cc not in blue_countries:
            return "country not covered by BLUE_DB"
        if yr < blue_min:
            return f"before BLUE_DB coverage (<{blue_min})"
        if yr > blue_max:
            return f"after BLUE_DB coverage (>{blue_max})"
        return "missing from BLUE_DB (within scope)"

    cov_rows = []
    for key in sorted(blue_keys | ned_keys):
        cc, _, yr = key.rpartition("_")
        yr = int(yr)
        in_blue, in_ned = key in blue_keys, key in ned_keys
        status = ("compared" if in_blue and in_ned
                  else "blue_only" if in_blue else "ned_only")
        cov_rows.append({
            "type": label, "country_code": cc, "year": yr,
            "in_blue": in_blue, "in_ned": in_ned,
            "status": status,
            "reason": _reason(status, cc, yr),
            "ned_finest_level": (f"NUTS{int(finest_lut[key])}"
                                 if key in finest_lut and pd.notna(finest_lut[key])
                                 else ""),
        })
    coverage = pd.DataFrame(cov_rows)
    nb = coverage["status"].value_counts().to_dict()
    print(f"Coverage: {nb.get('compared',0)} compared, "
          f"{nb.get('blue_only',0)} BLUE-only (no EU-NED reference), "
          f"{nb.get('ned_only',0)} EU-NED-only (missing from BLUE_DB)")
    # Report each end grouped by reason, listing the within-scope gaps in full
    # (these are the actionable ones) and only summarising the structural ones.
    for st, msg in [("blue_only", "BLUE_DB elections with no EU-NED reference"),
                    ("ned_only", "EU-NED elections not in BLUE_DB")]:
        miss = coverage[coverage["status"] == st]
        if miss.empty:
            continue
        print(f"  {msg} ({len(miss)}):")
        for reason, grp in sorted(miss.groupby("reason"),
                                  key=lambda x: -len(x[1])):
            pairs = ", ".join(f"{r.country_code} {r.year}"
                              for r in grp.sort_values(["country_code", "year"])
                                          .itertuples())
            print(f"    [{len(grp):>2}] {reason}: {pairs}")

    # ── 3. Aggregate BLUE_DB at the correct NUTS level per election ───────
    print("\nAggregating BLUE_DB …")
    parts, errors = [], []
    for lvl, grp in matched.groupby("finest_level"):
        geo_level = f"NUTS{int(lvl)}"
        for _, erow in grp.iterrows():
            try:
                r = db.results(
                    elections_df=grp[grp["key"] == erow["key"]],
                    geo_level=geo_level,
                    aggregate_by="party",
                )
                r["finest_level"] = int(lvl)
                parts.append(r)
            except Exception as exc:
                errors.append((erow["key"], str(exc)))

    if errors:
        print(f"  ⚠  {len(errors)} elections failed:")
        for k, e in errors:
            print(f"     {k}: {e}")

    blu = pd.concat(parts, ignore_index=True)
    META = {"country_code", "election_date", "year", "election_type",
            "geo_id", "geo_name", "geo_level", "registered", "turnout",
            "invalid", "blank", "round", "finest_level"}
    party_cols = [c for c in blu.columns if c not in META]

    # Compute valid votes and invalid+blank (party columns coerced in place)
    blu[party_cols] = blu[party_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
    blu = blu.copy()                      # de-fragment after wide column rewrite
    blu["registered"] = pd.to_numeric(blu["registered"], errors="coerce").fillna(0)
    blu["turnout"]    = pd.to_numeric(blu["turnout"],    errors="coerce").fillna(0)
    blu["valid"]      = blu[party_cols].sum(axis=1)
    blu["blu_invbl"]  = pd.to_numeric(blu["invalid"], errors="coerce").fillna(0) \
                      + pd.to_numeric(blu["blank"],   errors="coerce").fillna(0)
    blu.rename(columns={"geo_id": "nuts_id"}, inplace=True)

    print(f"  → {len(blu):,} rows across {blu['country_code'].nunique()} countries "
          f"(NUTS levels: {sorted(blu['finest_level'].unique())})")

    # ── 4a. Align NUTS vintages: map both sides to lineage groups ─────────
    roots_fn = build_nuts_roots()
    grp_tbl = build_component_table(
        pd.concat([
            blu[["country_code", "year", "finest_level", "nuts_id"]],
            ned_admin[["country_code", "year", "finest_level", "nuts_id"]],
        ], ignore_index=True).drop_duplicates(),
        roots_fn,
    )

    def add_grp(df):
        out = df.merge(grp_tbl,
                       on=["country_code", "year", "finest_level", "nuts_id"],
                       how="left")
        out["nuts_grp"] = out["nuts_grp"].fillna(out["nuts_id"])
        return out

    blu       = add_grp(blu)
    ned_admin = add_grp(ned_admin)
    ned       = add_grp(ned)

    # ── 4. Party → partyfacts_id mapping ─────────────────────────────────
    pf_map: dict[str, tuple[int, str]] = {}
    for _, pr in db.parties[db.parties["party_facts_id"].notna()].iterrows():
        pfid = int(pr["party_facts_id"])
        name = str(pr.get("name_english") or pr.get("abbreviation") or pr["party_id"])
        pf_map[str(pr["party_id"])] = (pfid, name)

    ned_party_names: dict[int, str] = {}
    for _, pr in ned[ned["partyfacts_id"].notna()].iterrows():
        pfid = int(pr["partyfacts_id"])
        if pfid not in ned_party_names:
            ned_party_names[pfid] = str(
                pr.get("party_english") or pr.get("party_abbreviation") or pfid
            )

    # ── 5. Build comparison rows ──────────────────────────────────────────

    # 5a. Administrative join (per lineage group)
    GRP = ["country_code", "year", "finest_level", "nuts_grp"]
    blu_admin = (
        blu.groupby(GRP, as_index=False)
           .agg(registered=("registered", "sum"), turnout=("turnout", "sum"),
                valid=("valid", "sum"), blu_invbl=("blu_invbl", "sum"))
    )
    ned_admin_g = (
        ned_admin.groupby(GRP, as_index=False)
                 .agg(ned_registered=("ned_registered", "sum"),
                      ned_turnout=("ned_turnout", "sum"),
                      ned_validvote=("ned_validvote", "sum"),
                      ned_invbl=("ned_invbl", "sum"))
    )
    adm = blu_admin.merge(ned_admin_g, on=GRP, how="inner") \
                   .rename(columns={"nuts_grp": "nuts_id"})

    # Coverage: within elections that ARE compared, EU-NED groups with no BLUE_DB
    # counterpart (genuine gaps, not a vintage artifact). Restrict to (country,
    # year) pairs present in BLUE_DB; extra-regio pseudo-regions (…ZZ) excluded.
    compared = blu_admin[["country_code", "year"]].drop_duplicates()
    cover = (ned_admin_g.merge(compared, on=["country_code", "year"], how="inner")[GRP]
             .merge(blu_admin[GRP], on=GRP, how="left", indicator=True))
    coverage_gaps = cover[(cover["_merge"] == "left_only")
                          & ~cover["nuts_grp"].str.contains("ZZ")].copy()

    # National totals INCLUDING abroad/foreign units. Out-of-country votes have no
    # NUTS3 region, so they drop out of the per-region join above; at the national
    # level they belong to the country, and EU-NED folds them into its regional
    # figures. Sum at LAU (raw unit) level, which keeps every row regardless of NUTS
    # resolution (NUTS0 is unreliable here — some countries' units lack a NUTS0
    # mapping), so both sides include abroad for a fair national comparison.
    # WITH foreign: LAU totals (incl. abroad), reduced one election at a time to
    # avoid a 6 GB union-of-all-parties frame. EU-NED's electorate convention varies
    # by country, so we report both WITH and WITHOUT foreign and keep whichever delta
    # is smaller (min by magnitude).
    with warnings.catch_warnings():        # benign wide-frame fragmentation in lib
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        blu_nat, votes_for, valid_for, abroad_nat, abroad_votes = \
            lau_national_aggregates(db, matched, pf_map, META)

    # WITHOUT foreign: sum of NUTS regions (`blu`) MINUS abroad voters. Abroad units
    # (e.g. Spanish C.E.R.E.) are registered to a home NUTS region, so they are
    # folded into `blu` rather than dropped; subtract them for genuine domestic totals.
    blu_dom = (blu.groupby(["country_code", "year"], as_index=False)
               .agg(dom_registered=("registered", "sum"),
                    dom_turnout=("turnout", "sum"),
                    dom_invbl=("blu_invbl", "sum"))
               .merge(abroad_nat, on=["country_code", "year"], how="left"))
    for dom_c, ab_c in [("dom_registered", "ab_registered"),
                        ("dom_turnout", "ab_turnout"),
                        ("dom_invbl", "ab_invbl")]:
        blu_dom[dom_c] = blu_dom[dom_c] - blu_dom[ab_c].fillna(0)
    blu_dom = blu_dom.drop(columns=["ab_registered", "ab_turnout",
                                    "ab_invbl", "ab_valid"])

    ned_nat = (ned_admin.groupby(["country_code", "year"], as_index=False)
               .agg(ned_registered=("ned_registered", "sum"),
                    ned_turnout=("ned_turnout", "sum"),
                    ned_invbl=("ned_invbl", "sum")))

    nat = (blu_nat.merge(blu_dom, on=["country_code", "year"], how="left")
                  .merge(ned_nat, on=["country_code", "year"], how="inner"))
    for nat_c, dom_c, ned_c, d, de, dm in [
        ("nat_registered","dom_registered","ned_registered","reg_diff%","reg_diff_excl%","reg_diff_min%"),
        ("nat_turnout",   "dom_turnout",   "ned_turnout",   "turn_diff%","turn_diff_excl%","turn_diff_min%"),
        ("nat_invbl",     "dom_invbl",     "ned_invbl",     "invbl_diff%","invbl_diff_excl%","invbl_diff_min%"),
    ]:
        nat[d]  = nat.apply(lambda r: safe_reldiff(r[nat_c], r[ned_c]), axis=1).round(2)
        nat[de] = nat.apply(lambda r: safe_reldiff(r[dom_c], r[ned_c]), axis=1).round(2)
        nat[dm] = nat.apply(lambda r: round(signed_min(r[d], r[de]), 2), axis=1)
    nat["coverage%"] = (nat["nat_turnout"] / nat["ned_turnout"] * 100).round(2)

    # ── National party shares: WITH foreign (LAU) and WITHOUT (domestic) ───
    # `votes_for` / `valid_for` (WITH foreign) come from the per-election LAU
    # reduction above. The WITHOUT-foreign side is the already-loaded NUTS frame
    # `blu`, narrow enough to melt directly — but `blu` folds abroad voters into
    # their home regions, so we subtract the isolated abroad votes/valid.
    def _nat_party_votes(df, pcols, name):
        long = df.melt(id_vars=["country_code", "year"], value_vars=pcols,
                       var_name="party_id", value_name="votes")
        long["votes"] = pd.to_numeric(long["votes"], errors="coerce").fillna(0)
        long["pfid"] = long["party_id"].map(lambda p: pf_map.get(p, (None,))[0])
        long = long[long["pfid"].notna()].copy()
        long["pfid"] = long["pfid"].astype(int)
        return long.groupby(["country_code", "year", "pfid"], as_index=False) \
                   .agg(**{name: ("votes", "sum")})

    votes_excl = (_nat_party_votes(blu, party_cols, "blu_votes_excl")
                  .merge(abroad_votes, on=["country_code", "year", "pfid"],
                         how="left"))
    votes_excl["blu_votes_excl"] -= votes_excl["ab_votes"].fillna(0)
    votes_excl = votes_excl.drop(columns="ab_votes")
    valid_excl = (blu.groupby(["country_code", "year"], as_index=False)
                  .agg(blu_valid_excl=("valid", "sum"))
                  .merge(abroad_nat[["country_code", "year", "ab_valid"]],
                         on=["country_code", "year"], how="left"))
    valid_excl["blu_valid_excl"] -= valid_excl["ab_valid"].fillna(0)
    valid_excl = valid_excl.drop(columns="ab_valid")
    _nedp = ned[ned["partyfacts_id"].notna()].copy()
    _nedp["pfid"] = _nedp["partyfacts_id"].astype(int)
    ned_votes_nat = (_nedp.groupby(["country_code", "year", "pfid"], as_index=False)
                     .agg(ned_votes=("partyvote", "sum")))
    ned_valid_nat = (_nedp.drop_duplicates(["country_code", "year", "nuts_id"])
                     .groupby(["country_code", "year"], as_index=False)
                     .agg(ned_valid=("validvote", "sum")))

    nat_party = (ned_votes_nat
                 .merge(ned_valid_nat, on=["country_code", "year"], how="left")
                 .merge(votes_for,  on=["country_code", "year", "pfid"], how="inner")
                 .merge(votes_excl, on=["country_code", "year", "pfid"], how="left")
                 .merge(valid_for,  on=["country_code", "year"], how="left")
                 .merge(valid_excl, on=["country_code", "year"], how="left"))
    nat_party["blu_share_for"]  = (nat_party["blu_votes_for"]
                                   / nat_party["blu_valid_for"] * 100).round(4)
    nat_party["blu_share_excl"] = (nat_party["blu_votes_excl"].fillna(0)
                                   / nat_party["blu_valid_excl"] * 100).round(4)
    nat_party["ned_share"]      = (nat_party["ned_votes"]
                                   / nat_party["ned_valid"] * 100).round(4)
    nat_party["delta_pp"]       = (nat_party["blu_share_for"]  - nat_party["ned_share"]).round(4)
    nat_party["delta_excl_pp"]  = (nat_party["blu_share_excl"] - nat_party["ned_share"]).round(4)
    nat_party["delta_min_pp"]   = nat_party.apply(
        lambda r: signed_min(r["delta_pp"], r["delta_excl_pp"]), axis=1)
    nat_party["party"] = nat_party["pfid"].map(
        lambda p: f"{ned_party_names.get(int(p), p)} ({int(p)})")

    rows: list[dict] = []

    for _, r in adm.iterrows():
        base = dict(
            country_code = r["country_code"],
            year         = int(r["year"]),
            nuts_level   = int(r["finest_level"]),
            nuts_id      = r["nuts_id"],
        )
        for metric, blu_v, ned_v in [
            ("registered",    float(r["registered"]), float(r["ned_registered"])),
            ("turnout",       float(r["turnout"]),    float(r["ned_turnout"])),
            ("invalid_blank", float(r["blu_invbl"]),  float(r["ned_invbl"])),
        ]:
            rows.append({
                **base,
                "metric":      metric,
                "blu_value":   round(blu_v, 2),
                "ned_value":   round(ned_v, 2),
                "diff":        round(blu_v - ned_v, 2),
                "reldiff_pct": round(safe_reldiff(blu_v, ned_v), 3),
            })

    # 5b. Party share comparison (per lineage group)
    blu_grp = blu.groupby(GRP, as_index=False)[party_cols + ["valid"]].sum()
    blu_long = blu_grp.melt(
        id_vars=GRP + ["valid"],
        value_vars=party_cols, var_name="party_id", value_name="votes")
    blu_long["pfid"] = blu_long["party_id"].map(
        lambda p: pf_map.get(p, (None,))[0]
    )
    blu_long = blu_long[blu_long["pfid"].notna()].copy()
    blu_long["pfid"] = blu_long["pfid"].astype(int)

    blu_pf = (
        blu_long.groupby(GRP + ["pfid"], as_index=False)
        .agg(blu_votes=("votes", "sum"), blu_valid=("valid", "first"))
    )

    ned_p = ned[ned["partyfacts_id"].notna()].copy()
    ned_p["pfid"] = ned_p["partyfacts_id"].astype(int)
    # validvote is reported per region; sum it across regions merged into a group
    ned_pf = (
        ned_p.groupby(GRP + ["pfid"], as_index=False)
             .agg(ned_votes=("partyvote", "sum"))
        .merge(
            ned_p.drop_duplicates(["country_code", "year",
                                   "finest_level", "nuts_id"])
                 .groupby(GRP, as_index=False)
                 .agg(ned_valid=("validvote", "sum")),
            on=GRP, how="left",
        )
    )

    party_cmp = blu_pf.merge(ned_pf, on=GRP + ["pfid"], how="inner") \
                      .rename(columns={"nuts_grp": "nuts_id"})

    for _, r in party_cmp.iterrows():
        pfid   = int(r["pfid"])
        name   = ned_party_names.get(pfid, str(pfid))
        metric = f"{name} ({pfid})"
        blu_v  = r["blu_votes"] / r["blu_valid"] * 100 if r["blu_valid"] > 0 else np.nan
        ned_v  = r["ned_votes"] / r["ned_valid"] * 100 if r["ned_valid"] > 0 else np.nan
        rows.append({
            "country_code": r["country_code"],
            "year":         int(r["year"]),
            "nuts_level":   int(r["finest_level"]),
            "nuts_id":      r["nuts_id"],
            "metric":       metric,
            "blu_value":    round(blu_v, 4) if pd.notna(blu_v) else np.nan,
            "ned_value":    round(ned_v, 4) if pd.notna(ned_v) else np.nan,
            "diff":         round(blu_v - ned_v, 4)
                            if pd.notna(blu_v) and pd.notna(ned_v) else np.nan,
            "reldiff_pct":  round(safe_reldiff(blu_v, ned_v), 4),
        })

    result = pd.DataFrame(rows)

    # ── 6. Print reports ──────────────────────────────────────────────────

    adm["reg_diff%"]   = adm.apply(
        lambda r: safe_reldiff(r["registered"], r["ned_registered"]), axis=1
    ).round(2)
    adm["coverage%"]   = (adm["turnout"] / adm["ned_turnout"] * 100).round(1)
    adm["invbl_diff%"] = adm.apply(
        lambda r: safe_reldiff(r["blu_invbl"], r["ned_invbl"]), axis=1
    ).round(2)

    party_rows = result[~result["metric"].isin(
        ["registered", "turnout", "invalid_blank"]
    )].dropna(subset=["diff"])

    def flagged(df, col, lo, hi, n=100000):
        mask = pd.Series(False, index=df.index)
        if lo is not None: mask |= df[col] < lo
        if hi is not None: mask |= df[col] > hi
        return (df[mask]
                .assign(_k=df[col].abs())
                .sort_values("_k", ascending=False)
                .drop(columns="_k")
                .head(n))

    # ── Section 0: Region coverage ───────────────────────────────────────
    hr("═"); print("SECTION 0 — Region coverage (after NUTS lineage alignment)"); hr("═")
    n_cells = len(adm)
    print(f"\n  {n_cells:,} comparison cells matched across "
          f"{adm[['country_code','year']].drop_duplicates().shape[0]} elections.")
    print("\n  EU-NED regions with no BLUE_DB match (excl. extra-regio):")
    hr()
    if coverage_gaps.empty:
        print("  None.")
    else:
        gap = (coverage_gaps.groupby(["country_code", "year"])["nuts_grp"]
               .agg(lambda s: ", ".join(sorted(s)))
               .reset_index().rename(columns={"nuts_grp": "unmatched_groups"}))
        print(gap.to_string(index=False))

    # ── Section 1: Registered voters ─────────────────────────────────────
    reg = adm[["country_code","year","finest_level","nuts_id",
               "registered","ned_registered","reg_diff%"]].rename(columns={
        "country_code":"country","finest_level":"lvl",
        "registered":"blu_registered","reg_diff%":"diff%"})

    hr("═"); print("SECTION 1 — Registered voters"); hr("═")

    print("\n  1a. NUTS regions with |diff| > 1%")
    hr()
    bad_reg = flagged(reg, "diff%", lo=-1, hi=1)
    print(f"  {(~reg['diff%'].isna()).sum():,} NUTS regions compared. Flagged: {len(bad_reg)}\n")
    print("  None." if bad_reg.empty else bad_reg.to_string(index=False))

    print("\n  1b. National sums with |diff| > 1%  (incl. abroad)")
    hr()
    nat_reg = (nat[nat["reg_diff%"].abs() > 1]
               .assign(_k=lambda d: d["reg_diff%"].abs())
               .sort_values("_k", ascending=False).drop(columns="_k"))
    print("  None." if nat_reg.empty
          else nat_reg[["country_code","year","nat_registered","ned_registered","reg_diff%"]]
               .rename(columns={"nat_registered":"blu_registered","reg_diff%":"nat_diff%"})
               .to_string(index=False))

    # ── Section 2: Turnout / coverage ────────────────────────────────────
    cov = adm[["country_code","year","finest_level","nuts_id",
               "turnout","ned_turnout","coverage%"]].rename(columns={
        "country_code":"country","finest_level":"lvl","turnout":"blu_turnout"})

    hr("═"); print("SECTION 2 — Turnout"); hr("═")

    print("\n  2a. NUTS regions with coverage outside [90%, 110%]")
    hr()
    bad_cov = flagged(cov, "coverage%", lo=90, hi=110)
    print(f"  {(~cov['coverage%'].isna()).sum():,} NUTS regions compared. Flagged: {len(bad_cov)}\n")
    print("  None." if bad_cov.empty else bad_cov.to_string(index=False))

    print("\n  2b. National coverage with elections outside [99%, 101%]  (incl. abroad)")
    hr()
    bad_nat_cov = (nat[(nat["coverage%"] < 99) | (nat["coverage%"] > 101)]
                   .assign(_k=lambda d: (d["coverage%"] - 100).abs())
                   .sort_values("_k", ascending=False).drop(columns="_k"))
    print("  None." if bad_nat_cov.empty
          else bad_nat_cov[["country_code","year","nat_turnout","ned_turnout","coverage%"]]
               .rename(columns={"nat_turnout":"blu_turnout","coverage%":"nat_coverage%"})
               .to_string(index=False))

    # ── Section 3: Invalid + blank ────────────────────────────────────────
    inv = adm[["country_code","year","finest_level","nuts_id",
               "blu_invbl","ned_invbl","invbl_diff%"]].rename(columns={
        "country_code":"country","finest_level":"lvl","invbl_diff%":"diff%"})

    hr("═"); print("SECTION 3 — Invalid + blank"); hr("═")

    print("\n  3a. NUTS regions with |diff| > 20%")
    hr()
    bad_inv = flagged(inv, "diff%", lo=-20, hi=20)
    print(f"  {(~inv['diff%'].isna()).sum():,} NUTS regions compared. Flagged: {len(bad_inv)}\n")
    print("  None." if bad_inv.empty else bad_inv.to_string(index=False))

    print("\n  3b. National sums with |diff| > 20%  (incl. abroad)")
    hr()
    bad_nat_inv = (nat[nat["invbl_diff%"].abs() > 20]
                   .assign(_k=lambda d: d["invbl_diff%"].abs())
                   .sort_values("_k", ascending=False).drop(columns="_k"))
    print("  None." if bad_nat_inv.empty
          else bad_nat_inv[["country_code","year","nat_invbl","ned_invbl","invbl_diff%"]]
               .rename(columns={"nat_invbl":"blu_invbl","invbl_diff%":"nat_diff%"})
               .to_string(index=False))

    # ── Section 4: Party shares — per-country summary ─────────────────────
    hr("═")
    print("SECTION 4 — Party vote shares (BLUE_DB % − EU-NED %), by country")
    hr("═")
    cc_party = []
    for cc, g in party_rows.groupby("country_code"):
        d  = g["diff"].dropna()
        r2 = (g[["blu_value","ned_value"]].dropna().corr().iloc[0,1] ** 2
              if len(g) >= 2 else np.nan)
        cc_party.append({
            "country":     cc,
            "NUTS_level":  int(g["nuts_level"].iloc[0]),
            "n_obs":       len(d),
            "MAE_pp":      round(d.abs().mean(), 3),
            "RMSE_pp":     round(float(np.sqrt((d**2).mean())), 3),
            "within_1pp%": round((d.abs() <= 1).mean() * 100, 1),
            "r2":          round(r2, 4),
        })
    print()
    print(pd.DataFrame(cc_party).set_index("country").to_string())

    # ── Section 5: Worst individual NUTS3 party rows ──────────────────────
    hr("═")
    print("SECTION 5 — Largest |Δ share| at individual NUTS3 level")
    hr("═")
    nuts3_party = (
        party_rows[party_rows["nuts_level"] == 3]
        .dropna(subset=["diff"])
        .assign(_k=lambda d: d["diff"].abs())
        .sort_values("_k", ascending=False)
        .drop(columns="_k")
        .head(100)
        [["country_code","year","nuts_id","metric","blu_value","ned_value","diff"]]
    )
    print(f"\n  Top 100 observations (NUTS3 only):\n")
    print("  None." if nuts3_party.empty else nuts3_party.to_string(index=False))

    # ── Overall summary ───────────────────────────────────────────────────
    d_all  = party_rows["diff"].dropna()
    r2_all = party_rows[["blu_value","ned_value"]].dropna().corr().iloc[0,1] ** 2
    hr("═")
    print("Overall party-share summary")
    hr("═")
    print(f"  Observations:  {len(d_all):,}")
    print(f"  MAE:           {d_all.abs().mean():.4f} pp")
    print(f"  RMSE:          {float(np.sqrt((d_all**2).mean())):.4f} pp")
    print(f"  Within ±1 pp:  {(d_all.abs()<=1).mean()*100:.2f}%")
    print(f"  Within ±2 pp:  {(d_all.abs()<=2).mean()*100:.2f}%")
    print(f"  Pearson r²:    {r2_all:.6f}")

    # ── 7. Tag with contest type and return ───────────────────────────────
    for df in (result, nat, nat_party):
        df.insert(0, "type", label)   # `coverage` is already tagged
    hr("═")
    print(f"Done — {label}.")
    hr("═")
    return result, nat, nat_party, coverage


def main(csv_out: Path | None = DEFAULT_CSV) -> None:
    db = BlueDB()
    results, nats, nat_parties, coverages = [], [], [], []
    for election_type, ned_path, label in CONTESTS:
        r, n, npy, cov = run_comparison(db, election_type, ned_path, label)
        results.append(r)
        nats.append(n)
        nat_parties.append(npy)
        coverages.append(cov)
    result    = pd.concat(results,     ignore_index=True)
    nat       = pd.concat(nats,        ignore_index=True)
    nat_party = pd.concat(nat_parties, ignore_index=True)
    coverage  = pd.concat(coverages,   ignore_index=True)

    # ── Election coverage summary (BLUE_DB vs EU-NED) ─────────────────────
    hr("═")
    print("ELECTION COVERAGE — BLUE_DB vs EU-NED  (per country×year)")
    hr("═")
    cov_tab = (coverage.pivot_table(index="type", columns="status",
                                    values="year", aggfunc="count", fill_value=0)
               .reindex(columns=["compared", "blue_only", "ned_only"], fill_value=0))
    cov_tab["blue_total"] = cov_tab["compared"] + cov_tab["blue_only"]
    cov_tab["ned_total"]  = cov_tab["compared"] + cov_tab["ned_only"]
    cov_tab["compared_%_of_blue"] = (cov_tab["compared"] /
                                     cov_tab["blue_total"] * 100).round(1)
    print()
    print(cov_tab.to_string())

    # Why the un-compared elections are missing, pooled across contest types.
    for st, msg in [("blue_only", "BLUE_DB elections with no EU-NED reference"),
                    ("ned_only", "EU-NED elections not in BLUE_DB")]:
        miss = coverage[coverage["status"] == st]
        if miss.empty:
            continue
        print(f"\n  {msg} ({len(miss)}) by reason:")
        by_reason = (miss.groupby("reason").size()
                     .sort_values(ascending=False))
        for reason, cnt in by_reason.items():
            print(f"    [{cnt:>3}] {reason}")

    # ── Cross-evaluation summary (paper-ready agreement statistics) ───────
    summary = cross_eval_summary(result, nat, nat_party)
    hr("═")
    print("CROSS-EVALUATION SUMMARY — BLUE_DB vs EU-NED agreement rates")
    hr("═")
    print("  admin metrics: relative gap (%); vote shares: percentage-point gap (pp)\n")
    print(summary.to_string(index=False))

    # ── CSV + Excel output (national + EP combined) ───────────────────────
    if csv_out:
        result.to_csv(csv_out, index=False)
        print(f"\nFull comparison table → {csv_out} ({len(result):,} rows)")

        summary_csv = Path(csv_out).with_name(
            Path(csv_out).stem + "_summary.csv")
        summary.to_csv(summary_csv, index=False)
        print(f"Cross-eval summary     → {summary_csv} ({len(summary)} rows)")

        coverage_csv = Path(csv_out).with_name(
            Path(csv_out).stem + "_coverage.csv")
        coverage.sort_values(["type", "country_code", "year"]).to_csv(
            coverage_csv, index=False)
        print(f"Election coverage      → {coverage_csv} ({len(coverage)} rows)")

        xls_out = Path(csv_out).with_suffix(".xlsx")
        counts = write_discrepancy_workbook(xls_out, result, nat, nat_party,
                                            coverage)
        print(f"Discrepancy workbook   → {xls_out}")
        for name, n in counts.items():
            print(f"    {name:<26} {n:>6,} rows")

    hr("═")
    print("All done.")
    hr("═")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv", metavar="FILE", type=Path,
                   nargs="?", const=DEFAULT_CSV, default=DEFAULT_CSV)
    p.add_argument("--no-csv", action="store_true")
    args = p.parse_args()
    main(csv_out=None if args.no_csv else args.csv)
