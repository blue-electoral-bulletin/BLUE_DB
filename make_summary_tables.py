#!/usr/bin/env python3
"""
make_summary_tables.py
======================
Generate the per-country summary table for the paper from the built
distribution (``dist/``).  For every country it counts:

  * municipalities  -- LAU units in the geographic typology (dist/geo/laus.csv),
  * parties         -- parties, coalitions, lists and candidates whose
                       ``region`` is that country (dist/parties/parties.csv),
  * national        -- distinct national election contests (two rounds of the
                       same contest count once), and
  * EP              -- distinct European Parliament election contests,

reading the election index (dist/elections/index.csv).

Output: ``paper/summary_by_country.tex`` -- a booktabs ``tabular`` meant to be
``\\input`` from the paper, plus totals in the last row.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
OUT = ROOT / "summary_by_country.tex"

# ISO 3166-1 alpha-2 codes as used by BLUE_DB (Greece is EL, not GR).
COUNTRY_NAMES = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "CH": "Switzerland",
    "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DK": "Denmark",
    "EE": "Estonia", "EL": "Greece", "ES": "Spain", "FI": "Finland",
    "FR": "France", "HR": "Croatia", "HU": "Hungary", "IE": "Ireland",
    "IS": "Iceland", "IT": "Italy", "LI": "Liechtenstein", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "MT": "Malta", "NL": "Netherlands",
    "NO": "Norway", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia",
}

# Countries and territories outside the EU and EFTA, listed in a second block.
OTHER_NAMES = {
    "AD": "Andorra", "AL": "Albania", "AM": "Armenia",
    "BA": "Bosnia and Herzegovina", "FO": "Faroe Islands", "GE": "Georgia",
    "GG": "Guernsey", "GI": "Gibraltar", "GL": "Greenland", "IM": "Isle of Man",
    "JE": "Jersey", "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro",
    "MK": "North Macedonia", "RS": "Serbia", "SM": "San Marino",
    "TR": "T\\\"{u}rkiye", "UA": "Ukraine", "UK": "United Kingdom", "XK": "Kosovo",
}


def _year(row: pd.Series) -> str | None:
    """Election year from the date, or parsed from the filename when the index
    leaves the date blank (some EP files carry no date)."""
    if pd.notna(row["election_date"]):
        return str(row["election_date"])[:4]
    m = re.search(r"(\d{4})", row["file_path"].split("/")[-1])
    return m.group(1) if m else None


def counts() -> pd.DataFrame:
    laus = pd.read_csv(DIST / "geo" / "laus.csv", dtype=str, low_memory=False)
    parties = pd.read_csv(DIST / "parties" / "parties.csv", dtype=str, low_memory=False, keep_default_na=False, na_values=[""])
    idx = pd.read_csv(DIST / "elections" / "index.csv", dtype=str)

    n_munic = laus["gisco_id"].str[:2].value_counts()
    n_party = parties["region"].value_counts()

    idx["type"] = idx["file_path"].apply(
        lambda p: "EP" if "/EU_" in p else "national")
    idx["year"] = idx.apply(_year, axis=1)

    # EP: one contest per (country, year). National: one contest per
    # (country, date), collapsing the second round of two-round systems.
    ep = idx[idx["type"] == "EP"].drop_duplicates(["country_code", "year"])
    nat = idx[(idx["type"] == "national")
              & (idx["round"].isna() | (idx["round"] == "1.0"))]
    nat = nat.drop_duplicates(["country_code", "election_date"])
    n_ep = ep["country_code"].value_counts()
    n_nat = nat["country_code"].value_counts()

    rows = []
    for cc in sorted(COUNTRY_NAMES) + sorted(OTHER_NAMES):
        rows.append({
            "group": "EU/EFTA" if cc in COUNTRY_NAMES else "other",
            "country": COUNTRY_NAMES.get(cc) or OTHER_NAMES[cc],
            "munic": int(n_munic.get(cc, 0)),
            "parties": int(n_party.get(cc, 0)),
            "national": int(n_nat.get(cc, 0)),
            "ep": int(n_ep.get(cc, 0)),
        })
    return pd.DataFrame(rows)


def to_latex(df: pd.DataFrame) -> str:
    """Two tables side by side (EU/EFTA, then the rest), so that the 52 rows
    fit on one page; the grand total closes the second one."""
    def fmt(n: int) -> str:
        return f"{n:,}"

    def total(label, d):
        return (f"\\textbf{{{label}}} & \\textbf{{{fmt(d['munic'].sum())}}} & "
                f"\\textbf{{{fmt(d['parties'].sum())}}} & "
                f"\\textbf{{{d['national'].sum()}}} & \\textbf{{{d['ep'].sum()}}} \\\\")

    def block(d, title, totals):
        lines = [
            r"\begin{tabular}[t]{@{}lrrrr@{}}",
            r"\multicolumn{5}{@{}l}{\emph{" + title + r"}} \\",
            r"\toprule",
            r"Country & Munic. & Parties & Nat. & EP \\",
            r"\midrule",
        ]
        for _, r in d.iterrows():
            lines.append(f"{r['country']} & {fmt(r['munic'])} & {fmt(r['parties'])} "
                         f"& {r['national']} & {r['ep']} \\\\")
        lines.append(r"\midrule")
        lines += [total(label, t) for label, t in totals]
        lines += [r"\bottomrule", r"\end{tabular}"]
        return lines

    groups = list(dict.fromkeys(df["group"]))
    eu, other = (df[df["group"] == g] for g in groups)
    lines = (block(eu, "EU and EFTA", [("Subtotal", eu)])
             + [r"\hspace{2em}"]
             + block(other, "Outside the EU and EFTA",
                     [("Subtotal", other), ("Total", df)]))
    return "\n".join(lines) + "\n"


def main() -> None:
    df = counts()
    OUT.write_text(to_latex(df), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(df.to_string(index=False))
    print(f"\nTotals: {df['munic'].sum():,} municipalities, "
          f"{df['parties'].sum():,} parties, "
          f"{df['national'].sum()} national + {df['ep'].sum()} EP "
          f"= {df['national'].sum() + df['ep'].sum()} elections")


if __name__ == "__main__":
    main()
