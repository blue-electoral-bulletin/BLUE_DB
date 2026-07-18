"""
build_codebook.py
=================
Generates the BLUE_DB codebook as a PDF.

Workflow
--------
1.  Compute dataset statistics from the output/ folder and resource files.
2.  Render the Jinja2 main LaTeX template (codebook/blue_db_codebook.tex.j2),
    substituting computed stats and including the hand-editable template
    fragments from codebook/templates/.
3.  Write the rendered .tex to codebook/blue_db_codebook.tex.
4.  Compile twice with xelatex (for TOC / cross-references).

Editable files
--------------
codebook/templates/intro.tex              – Introduction prose
codebook/templates/overview.tex           – Dataset-structure overview
codebook/templates/variables_geo.tex      – Variable table: geo files
codebook/templates/variables_elections.tex– Variable table: election files
codebook/templates/variables_parties.tex  – Variable table: parties file
codebook/templates/special_units.tex      – Appendix: special unit types
codebook/templates/countries/{CC}.tex     – Per-country annotations
codebook/templates/countries/_default.tex – Used when no country file exists

Run
---
    python3 build_codebook.py [--no-compile] [--open]
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import subprocess
import sys
from pathlib import Path
from collections import defaultdict

import pandas as pd
from foreign_resolver import _COUNTRY_EN_NAME

try:
    import pycountry  # type: ignore
except Exception:  # pragma: no cover
    pycountry = None

# ── paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
RESOURCES = ROOT / "resources"
CODEBOOK_DIR = ROOT / "codebook"
TEMPLATES_DIR = CODEBOOK_DIR / "templates"
TEX_OUT = CODEBOOK_DIR / "blue_db_codebook.tex"
PDF_OUT = CODEBOOK_DIR / "blue_db_codebook.pdf"

# Regex for data filenames (not _special.csv)
_YEAR_RE = re.compile(r"^(\d{4})(?:_(\d+))?\.csv$")
# Coverage helper regex: allows suffixes such as _party / _single_member.
_YEAR_PREFIX_RE = re.compile(r"^(\d{4})(?:_(\d+))?.*\.csv$")

# ── country metadata ─────────────────────────────────────────────────────────
# Maps ISO code → full English name (add entries as needed)
_COUNTRY_NAMES: dict[str, str] = {
    "AD": "Andorra",
    "AL": "Albania",
    "AM": "Armenia",
    "AT": "Austria",
    "BA": "Bosnia and Herzegovina",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "CH": "Switzerland",
    "CY": "Cyprus",
    "CZ": "Czech Republic",
    "DE": "Germany",
    "DK": "Denmark",
    "EE": "Estonia",
    "EL": "Greece",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GG": "Guernsey",
    "GI": "Gibraltar",
    "HR": "Croatia",
    "HU": "Hungary",
    "IE": "Ireland",
    "IS": "Iceland",
    "IT": "Italy",
    "LI": "Liechtenstein",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "MC": "Monaco",
    "MT": "Malta",
    "NL": "Netherlands",
    "NO": "Norway",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SE": "Sweden",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "UK": "United Kingdom",
}


def _tex_escape(s: str, max_length: int | None = None) -> str:
    """Escape special LaTeX characters in a plain string."""
    if max_length is not None:
        if len(s) > max_length:
            first_part = s[:(max_length - 2) // 2]
            last_part = s[-(max_length - 2) // 2:]
            s = f"{first_part}..{last_part}"
    replacements = [
        ("\\", "\\textbackslash{}"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("{", "\\{"),
        ("}", "\\}"),
        ("~", "\\textasciitilde{}"),
        ("^", "\\textasciicircum{}"),
    ]
    for old, new in replacements:
        s = s.replace(old, new)
    return s


# ── data sources ─────────────────────────────────────────────────────────────
# (parliament_name, source_office, url)
_DATA_SOURCES: dict[str, tuple[str, str, str]] = {
    "AD": ("Consell General",        "Govern d'Andorra",                          "https://www.govern.ad"),
    "AL": ("Kuvendi",                 "Komisioni Qendror i Zgjedhjeve",             "https://www.kqz.gov.al"),
    "AM": ("Ազգային ժողով",           "Կենտրոնական ընտրական հանձնաժողով",            "https://www.elections.am"),
    "AT": ("Nationalrat",             "Bundesministerium des Innern",               "https://www.bmi.gv.at/wahlen"),
    "BA": ("Parlamentarna skupština", "Izborna komisija BiH",                       "https://www.izbori.ba"),
    "BE": ("Chambre des Représentants","IBZ",                                       "https://wahlergebnisse.belgium.be"),
    "BG": ("Народно събрание",         "Централна избирателна комисия",              "https://results.cik.bg"),
    "CH": ("Nationalrat",             "Bundeskanzlei",                              "https://www.bk.admin.ch/wahlen"),
    "CY": ("Βουλή των Αντιπροσώπων",   "Υπουργείο Εσωτερικών",                        "https://www.moi.gov.cy"),
    "CZ": ("Poslanecká sněmovna",     "Český statistický úřad",                    "https://www.volby.cz"),
    "DE": ("Bundestag",               "Bundeswahlleiterin",                         "https://www.bundeswahlleiterin.de"),
    "DK": ("Folketing",               "Danmarks Statistik",                         "https://www.dst.dk/valg"),
    "EE": ("Riigikogu",               "Valimised (Vabariigi Valimiskomisjon)",       "https://www.valimised.ee"),
    "EL": ("Βουλή των Ελλήνων",        "Υπουργείο Εσωτερικών",                        "https://www.ypes.gr/ekloges"),
    "ES": ("Congreso de los Diputados","Infoelectoral (Ministerio del Interior)",   "https://infoelectoral.interior.gob.es"),
    "FI": ("Eduskunta",               "Vaalit (Oikeusministeriö)",                  "https://tulospalvelu.vaalit.fi"),
    "FR": ("Assemblée nationale",     "Ministère de l'Intérieur",                  "https://www.resultats-elections.interieur.gouv.fr"),
    "GG": ("States of Deliberation",  "States of Guernsey",                         "https://www.gov.gg"),
    "GI": ("Gibraltar Parliament",    "Gibraltar Electoral Office",                  "https://www.gibraltar.gov.gi"),
    "HR": ("Hrvatski sabor",          "Državno izborno povjerenstvo",               "https://www.izbori.hr"),
    "HU": ("Országgyűlés",            "Nemzeti Választási Iroda",                   "https://www.valasztas.hu"),
    "IE": ("Dáil Éireann",            "National Electoral Commission",               "https://www.electoralcommission.ie"),
    "IS": ("Alþingi",                 "Landskjörstjórn",                            "https://www.landskjor.is"),
    "IT": ("Camera dei Deputati",     "Eligendo (Ministero dell'Interno)",          "https://elezionistorico.interno.gov.it"),
    "LI": ("Landtag",                 "Amt für Statistik Liechtenstein",            "https://www.llv.li/wahlen"),
    "LT": ("Seimas",                  "Vyriausioji rinkimų komisija",               "https://www.vrk.lt"),
    "LU": ("Chambre des Députés",     "Gouvernement du Grand-Duché",                "https://elections.public.lu"),
    "LV": ("Saeima",                  "Centrālā vēlēšanu komisija",                 "https://www.cvk.lv"),
    "MC": ("Conseil national",        "Gouvernement de Monaco",                     "https://www.gouv.mc"),
    "MT": ("Parlament ta' Malta",     "Electoral Commission of Malta",               "https://electoral.gov.mt"),
    "NL": ("Tweede Kamer",            "Kiesraad",                                   "https://www.verkiezingsuitslagen.nl"),
    "NO": ("Storting",                "Valgdirektoratet",                           "https://valgresultat.no"),
    "PL": ("Sejm",                    "Państwowa Komisja Wyborcza",                 "https://wybory.gov.pl"),
    "PT": ("Assembleia da República", "Secretaria-Geral da Administração Interna",  "https://www.eleicoes.mai.gov.pt"),
    "RO": ("Camera Deputaților",      "Biroul Electoral Central",                   "https://prezenta.roaep.ro"),
    "SE": ("Riksdagen",               "Valmyndigheten",                             "https://www.val.se"),
    "SI": ("Državni zbor",            "Državna volilna komisija",                   "https://www.dvk-rs.si"),
    "SK": ("Národná rada",            "Štatistický úrad Slovenskej republiky",      "https://volby.statistics.sk"),
    "UK": ("House of Commons",        "Electoral Commission (UK)",                  "https://www.electoralcommission.org.uk"),
}


def build_data_sources_table(active_countries: set[str]) -> str:
    """Generate a longtable with parliament names and data-source blocks.
    Only includes countries that have actual data files.
    """
    rows = []
    for cc in sorted(_DATA_SOURCES):
        if cc not in active_countries:
            continue
        parl, source, url = _DATA_SOURCES[cc]
        name = _COUNTRY_NAMES.get(cc, cc)
        e_name   = _tex_escape(name)
        e_parl   = _tex_escape(parl)
        e_source = _tex_escape(source)
        e_url    = url  # URLs go into \url{} which handles special chars
        source_block = (
            f"{e_source}\\newline "
            f"\\href{{{e_url}}}{{\\small\\texttt{{{_tex_escape(url.replace('https://', ''), max_length=32)}}}}}"
        )
        rows.append("\\texttt{{{}}} & {} & {} & {} \\\\".format(cc, e_name, e_parl, source_block))
    header = (
        "\\begin{longtable}{@{}llp{4.8cm}p{6.6cm}@{}}\n"
        "\\toprule\n"
        "\\textbf{CC} & \\textbf{Country} & \\textbf{Parliament} & "
        "\\textbf{Data source (with URL)} \\\\\n"
        "\\midrule\n"
        "\\endhead\n"
        "\\bottomrule\n"
        "\\endlastfoot\n"
    )
    return header + "\n".join(rows) + "\n\\end{longtable}\n"


def _abroad_country_name_from_code(code: str) -> str:
    """Resolve ISO-like host code to a readable country/region label."""
    c = str(code or "").strip().upper()
    if not c:
        return ""

    # Common aggregates in foreign-voter coding (PT and similar cases).
    aggregate = {
        "01": "North America",
        "02": "Latin America",
        "03": "Northern Europe",
        "04": "Benelux",
        "05": "Iberian Peninsula and Monaco",
        "06": "Switzerland and Liechtenstein",
        "07": "Central Europe",
        "08": "Southern Europe, Turkey, Israel",
        "09": "Northwest Africa",
        "10": "Central, South, and East Africa",
        "11": "Eastern Europe, Asia, Oceania",
        "90": "Rest of Europe",
        "91": "Rest of the Americas",
        "92": "Asia and Oceania",
        "93": "Africa",
        "94": "Americas and Africa",
        "98": "Ships at Sea",
        "99": "Unspecified foreign",
    }
    if c in aggregate:
        return aggregate[c]

    if c in _COUNTRY_NAMES:
        return _COUNTRY_NAMES[c]
    if c in _COUNTRY_EN_NAME:
        return _COUNTRY_EN_NAME[c]
    if c == "EL":
        return "Greece"
    if c == "GB":
        return "United Kingdom"
    if c == "YU":
        return "Federal Republic of Yugoslavia"
    if c == "CS":
        return "Serbia and Montenegro"
    if c == "KO":
        return "Kosovo"

    if pycountry is not None:
        try:
            if len(c) == 2 and c.isalpha():
                hit = pycountry.countries.get(alpha_2=c)
                if hit is not None:
                    return str(hit.name)
            if len(c) == 3 and c.isalpha():
                hit = pycountry.countries.get(alpha_3=c)
                if hit is not None:
                    return str(hit.name)
        except Exception:
            pass
    return c


def build_abroad_matrix(active_countries: set[str]) -> str:
    """
    Build a 2-D table: rows = host countries, columns = voter nationalities.
    Only voter countries with host-resolved Voters Abroad rows are included.
    Rows for countries that are primary DB units get a light-gray background.
    """
    special_src = RESOURCES / "special_raw.csv"
    if not special_src.exists():
        return "% special_raw.csv not found\n"

    df = pd.read_csv(special_src, dtype=str, low_memory=False, na_values=[''], keep_default_na=False)

    # Filter Voters Abroad rows that follow {VC}_{VC}ZZ{HC} pattern
    abroad_re = re.compile(r"^([A-Z]{2})_[A-Z]{2}ZZ([A-Z0-9]{2,3})$")
    voter_countries: dict[str, set[str]] = defaultdict(set)  # vc → set of host CCs
    for gid in df["gisco_id"].dropna():
        m = abroad_re.match(str(gid))
        if m:
            vc, hc = m.group(1), m.group(2)
            voter_countries[vc].add(hc)

    if not voter_countries:
        return "% No Voters Abroad data found in special_raw.csv\n"

    # Columns: voter countries sorted
    voter_cols = sorted(voter_countries)

    # Collect all host countries, sort them
    all_hosts: set[str] = set()
    for hosts in voter_countries.values():
        all_hosts |= hosts
    host_rows = sorted(all_hosts)

    n_cols = len(voter_cols)
    # Wide enough for code + name column
    col_spec = "@{}lp{5cm}" + "c" * n_cols + "@{}"
    header_cells = " & ".join(f"\\rotatebox{{90}}{{\\texttt{{{vc}}}}}" for vc in voter_cols)

    lines = [
        "\\begin{center}",
        "\\footnotesize",
        f"\\begin{{longtable}}{{{col_spec}}}",
        "\\toprule",
        f"\\textbf{{CC}} & \\textbf{{Host country}} & {header_cells} \\\\",
        "\\midrule",
        "\\endhead",
        "\\bottomrule",
        "\\endlastfoot",
    ]
    for hc in host_rows:
        name = _tex_escape(_abroad_country_name_from_code(hc))
        shade = "\\rowcolor[gray]{0.92}" if hc in active_countries else ""
        cells = [f"{shade}\\texttt{{{hc}}} & {name}"]
        for vc in voter_cols:
            cells.append("$\\bullet$" if hc in voter_countries[vc] else "")
        lines.append(" & ".join(cells) + " \\\\")
    lines += ["\\end{longtable}", "\\end{center}"]
    return "\n".join(lines) + "\n"


def build_overseas_extra_nuts_table() -> str:
    """List extra overseas NUTS-like parent entries used in special_raw.csv."""
    special_src = RESOURCES / "special_raw.csv"
    if not special_src.exists():
        return "% special_raw.csv not found\n"

    df = pd.read_csv(special_src, dtype=str, low_memory=False, na_values=[''], keep_default_na=False)
    if "parent" not in df.columns or "gisco_id" not in df.columns:
        return "% parent/gisco_id columns not found\n"

    sub = df[
        df["gisco_id"].astype(str).str.startswith(("FR_", "NL_"), na=False)
        & df["parent"].astype(str).str.match(r"^(FRZ|NL90[123])", na=False)
    ].copy()
    if sub.empty:
        return "% No overseas extra NUTS-like entries found\n"

    grouped = (
        sub.groupby("parent", dropna=False)
        .agg(n_units=("gisco_id", "nunique"), countries=("gisco_id", lambda s: ", ".join(sorted({str(v).split("_", 1)[0] for v in s}))))
        .reset_index()
        .sort_values("parent")
    )

    parent_names = {
        "FRZ10": "Nouvelle-Calédonie",
        "FRZ20": "Polynésie française",
        "FRZ30": "Saint-Barthélemy / Saint-Martin",
        "FRZ40": "Saint-Pierre-et-Miquelon",
        "FRZ50": "Wallis-et-Futuna",
        "NL901": "Bonaire",
        "NL902": "Sint Eustatius",
        "NL903": "Saba",
    }

    rows = []
    for _, r in grouped.iterrows():
        rows.append(
            f"\\texttt{{{_tex_escape(str(r['parent']))}}} & "
            f"{_tex_escape(parent_names.get(str(r['parent']), ''))} & "
            f"{_tex_escape(str(r['countries']))} & "
            f"{int(r['n_units']):,} \\\\"
        )

    lines = [
        "\\paragraph{Extra NUTS entries used for OCT coverage.}",
        "\\begin{longtable}{@{}p{2cm}p{7cm}p{1.5cm}r@{}}",
        "\\toprule",
        "\\textbf{Code} & \\textbf{Name} & \\textbf{CC} & \\textbf{LAUs} " + "\\\\",
        "\\midrule",
        "\\endhead",
        "\\bottomrule",
        "\\endlastfoot",
        *rows,
        "\\end{longtable}",
    ]
    return "\n".join(lines) + "\n"


def build_parties_country_counts_table() -> str:
    """Build table: by country counts of party/list entries, coalitions, individual candidates."""
    parties_src = ROOT / "parties_final.csv"
    if not parties_src.exists():
        return "% parties_final.csv not found\n"

    df = pd.read_csv(parties_src, low_memory=False, na_values=[''], keep_default_na=False)
    if "region" not in df.columns:
        return "% region column not found in parties_final.csv\n"

    def _safe_json_list(value: object) -> list[object]:
        if isinstance(value, list):
            return value
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return []
        s = str(value).strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    def _is_individual(value: object) -> bool:
        s = str(value or "").strip().lower()
        return s in {"1", "true", "yes", "y", "t"}

    df = df.copy()
    if "coalition_members" in df.columns:
        df["_is_coalition"] = df["coalition_members"].apply(lambda v: len(_safe_json_list(v)) > 0)
    else:
        df["_is_coalition"] = False

    if "individual_candidate" in df.columns:
        df["_is_individual"] = df["individual_candidate"].apply(_is_individual)
    else:
        df["_is_individual"] = False

    # Exclusive plain party/list entries (neither coalition nor individual).
    df["_is_party_list_only"] = (~df["_is_coalition"]) & (~df["_is_individual"])

    grouped = (
        df.groupby("region", dropna=False)
        .agg(
            party_list_only=("_is_party_list_only", "sum"),
            coalitions=("_is_coalition", "sum"),
            individual_candidates=("_is_individual", "sum"),
            total_entries=("name_native", "count"),
        )
        .reset_index()
        .rename(columns={"region": "cc"})
    )
    grouped["country"] = grouped["cc"].map(lambda c: _COUNTRY_NAMES.get(str(c), str(c)))
    grouped = grouped.sort_values(["country", "cc"])

    rows = []
    for _, r in grouped.iterrows():
        rows.append(
            f"\\texttt{{{_tex_escape(str(r['cc']))}}} & "
            f"{_tex_escape(str(r['country']))} & "
            f"{int(r['party_list_only']):,} & "
            f"{int(r['coalitions']):,} & "
            f"{int(r['individual_candidates']):,} & "
            f"{int(r['total_entries']):,} \\\\" 
        )

    lines = [
        "\\subsection{Entries by country}",
        "\\begin{longtable}{p{1cm}p{7cm}rrrr}",
        "\\toprule",
        "\\textbf{CC} & \\textbf{Country} & \\textbf{Party/list} & \\textbf{Coalitions} & \\textbf{Indep.} & \\textbf{Total} \\\\",
        "\\midrule",
        "\\endhead",
        "\\bottomrule",
        "\\endlastfoot",
        *rows,
        "\\end{longtable}",
    ]
    return "\n".join(lines) + "\n"


def _parse_json_list(value: object) -> list[object]:
    """Parse a JSON-encoded list column value; return [] on failure."""
    if isinstance(value, list):
        return value
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    s = str(value).strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _former_names_tex(other_names_value: object) -> str:
    """Return a footnotesize LaTeX line for former names, or '' if none."""
    entries = _parse_json_list(other_names_value)
    parts: list[str] = []
    seen: set[str] = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name_native") or e.get("name_english") or "").strip()
        abbr = str(e.get("abbreviation") or e.get("abbr") or "").strip()
        label = f"{_tex_escape(name)} ({_tex_escape(abbr)})" if (name and abbr) else _tex_escape(name or abbr)
        if label and label not in seen:
            parts.append(label)
            seen.add(label)
    if not parts:
        return ""
    return r"\newline{\footnotesize\textit{Formerly: " + "; ".join(parts) + "}}"


def build_ep_groups_table() -> str:
    """EP-groups longtable with a count column (number of parties per group).

    Rows are driven by ep_groups.csv; the ep_group_key column provides the
    exact compound string to match against parties_final.csv's ep_group[].group.
    """
    parties_src = ROOT / "parties_final.csv"
    ep_groups_src = RESOURCES / "ep_groups.csv"
    if not parties_src.exists():
        return "% parties_final.csv not found\n"
    if not ep_groups_src.exists():
        return "% ep_groups.csv not found\n"

    df = pd.read_csv(parties_src, low_memory=False, na_values=[''], keep_default_na=False)
    ep_groups_df = pd.read_csv(ep_groups_src, low_memory=False, na_values=[''], keep_default_na=False)

    # Collect the set of full (unsplit) group strings for every party.
    party_group_sets: list[set[str]] = []
    for val in df.get("ep_group", pd.Series(dtype=str)):
        groups: set[str] = set()
        for entry in _parse_json_list(val):
            if isinstance(entry, dict):
                g = entry.get("group", "").strip()
                if g:
                    groups.add(g)
        party_group_sets.append(groups)

    rows = []
    for _, row in ep_groups_df.iterrows():
        ep_key = str(row.get("ep_group_key") or "").strip()
        name = str(row.get("name_english") or row.get("name_native") or "").strip()
        start_raw = row.get("founded_year")
        end_raw = row.get("dissolved_year")
        start = str(int(float(start_raw))) if pd.notna(start_raw) else ""
        end = str(int(float(end_raw))) if pd.notna(end_raw) else ""
        period = f"{start}--{end}" if end else (f"{start}--" if start else "")
        party_id = str(row.get("party_id") or "").strip()
        count = sum(1 for gs in party_group_sets if ep_key in gs)
        former = _former_names_tex(row.get("other_names"))
        name_cell = f"\\textbf{{{_tex_escape(party_id)}}} {_tex_escape(name)}" + former
        rows.append(f"{name_cell} & {_tex_escape(period)} & {count:,} \\\\")

    lines = [
        "\\begin{longtable}{p{11cm}p{2.5cm}r}",
        "\\toprule",
        "\\textbf{Group} & \\textbf{Period} & \\textbf{Parties} \\\\",
        "\\midrule",
        "\\endhead",
        "\\bottomrule",
        "\\endlastfoot",
        *rows,
        "\\end{longtable}",
    ]
    return "\n".join(lines) + "\n"


def build_ideology_table() -> str:
    """Ideology longtable with a count column (number of parties per category)."""
    parties_src = ROOT / "parties_final.csv"
    if not parties_src.exists():
        return "% parties_final.csv not found\n"

    df = pd.read_csv(parties_src, low_memory=False, na_values=[''], keep_default_na=False)
    counts = (
        df["ideology"]
        .fillna("unknown")
        .value_counts()
        .sort_values(ascending=False)
    )

    rows = [
        f"{_tex_escape(str(ideology))} & {int(count):,} \\\\"
        for ideology, count in counts.items()
    ]

    lines = [
        "\\begin{longtable}{p{14cm}r}",
        "\\toprule",
        "\\textbf{Ideology} & \\textbf{Parties} \\\\",
        "\\midrule",
        "\\endhead",
        "\\bottomrule",
        "\\endlastfoot",
        *rows,
        "\\end{longtable}",
    ]
    return "\n".join(lines) + "\n"


def build_european_parties_table() -> str:
    """European parties longtable with a count column (number of affiliated parties).

    Rows are driven by eu_parties.csv; the european_party_key column provides
    the match string for parties_final.csv's european_party[].organization.
    """
    parties_src = ROOT / "parties_final.csv"
    eu_parties_src = RESOURCES / "eu_parties.csv"
    if not parties_src.exists():
        return "% parties_final.csv not found\n"
    if not eu_parties_src.exists():
        return "% eu_parties.csv not found\n"

    df = pd.read_csv(parties_src, low_memory=False, na_values=[''], keep_default_na=False)
    eu_parties_df = pd.read_csv(eu_parties_src, low_memory=False, na_values=[''], keep_default_na=False)

    # Count unique parties per organization key.
    org_counts: dict[str, int] = defaultdict(int)
    for val in df.get("european_party", pd.Series(dtype=str)):
        seen: set[str] = set()
        for entry in _parse_json_list(val):
            if isinstance(entry, dict):
                org = entry.get("organization", "").strip()
                if org and org not in seen:
                    seen.add(org)
                    org_counts[org] += 1

    entries = []
    for _, row in eu_parties_df.iterrows():
        eu_key = str(row.get("european_party_key") or "").strip()
        name = str(row.get("name_english") or row.get("name_native") or "").strip()
        display = re.sub(r"\s*\([A-Z/]+\)$", "", name).strip() or name
        start_raw = row.get("founded_year")
        end_raw = row.get("dissolved_year")
        start = str(int(float(start_raw))) if pd.notna(start_raw) else ""
        end = str(int(float(end_raw))) if pd.notna(end_raw) else ""
        period = f"{start}--{end}" if end else (f"{start}--" if start else "")
        party_id = str(row.get("party_id") or "").strip()
        former = _former_names_tex(row.get("other_names"))
        name_cell = f"\\textbf{{{_tex_escape(party_id)}}} {_tex_escape(display)}" + former
        count = org_counts.get(eu_key, 0)
        entries.append((name_cell, period, count))

    entries.sort(key=lambda x: -x[2])
    rows = [
        f"{name_cell} & {_tex_escape(period)} & {count:,} \\\\"
        for name_cell, period, count in entries
    ]

    lines = [
        "\\begin{longtable}{p{11cm}p{2.5cm}r}",
        "\\toprule",
        "\\textbf{Party} & \\textbf{Period} & \\textbf{Parties} \\\\",
        "\\midrule",
        "\\endhead",
        "\\bottomrule",
        "\\endlastfoot",
        *rows,
        "\\end{longtable}",
    ]
    return "\n".join(lines) + "\n"


def build_country_special_units_table(cc: str) -> str:
    """
    Return a LaTeX table of special units for *cc*.
    Excludes Postal Vote rows and Abroad Voters rows (already covered by the abroad matrix).
    Returns empty string if no rows found.
    """
    special_src = RESOURCES / "special_raw.csv"
    if not special_src.exists():
        return ""
    df = pd.read_csv(special_src, dtype=str, low_memory=False, na_values=[''], keep_default_na=False)
    mask = df["gisco_id"].astype(str).str.startswith(f"{cc}_")
    if "type" in df.columns:
        type_l = df["type"].astype(str).str.lower()
        mask &= type_l != "postal vote"
        mask &= type_l != "abroad voters"
        mask &= type_l != "voters abroad"
    sub = df[mask].copy()
    if sub.empty:
        return ""

    # Fixed-width column specs: (csv_col, tex_width, max_chars, header_label)
    _COL_DEFS = [
        ("gisco_id", "p{2cm}",   None, "Code"),
        ("name_or",  "p{6cm}",   36,   "Name"),
        ("type",     "p{4.5cm}", 28,   "Type"),
        ("parent",   "p{2cm}",   None, "Parent"),
    ]
    show = [(col, width, mx, lbl) for col, width, mx, lbl in _COL_DEFS if col in sub.columns]
    show_cols = [col for col, *_ in show]
    sub = sub[show_cols].drop_duplicates()

    def _cell(val: str, max_chars: int | None) -> str:
        if max_chars and len(val) > max_chars:
            val = val[: max_chars - 2] + ".."
        return _tex_escape(val)

    col_spec = "@{}" + "".join(w for _, w, *_ in show) + "@{}"
    header = " & ".join(f"\\textbf{{{lbl}}}" for _, _, _, lbl in show)
    rows = []
    for _, row in sub.iterrows():
        cells = " & ".join(
            _cell(str(row[col]) if pd.notna(row[col]) else "", mx)
            for col, _, mx, _ in show
        )
        rows.append(cells + " \\\\")

    lines = [
        "\\paragraph{Special electoral units.}",
        f"\\begin{{longtable}}{{{col_spec}}}",
        "\\toprule",
        header + " \\\\",
        "\\midrule",
        "\\endhead",
        "\\bottomrule",
        "\\endlastfoot",
    ] + rows + ["\\end{longtable}"]
    return "\n".join(lines) + "\n"


def _classify_election_file_level(
    csv_path: Path,
    lau_codes: set[str],
    special_type_by_code: dict[str, str],
) -> str:
    """Classify one election file as municipal / constituency / higher / mixed."""
    try:
        df = pd.read_csv(csv_path, dtype={"gisco_id": str}, low_memory=False, usecols=["gisco_id"], na_values=[''], keep_default_na=False)
    except Exception:
        return "unknown"

    municipal = 0
    constituency = 0
    higher = 0

    gids = set(df["gisco_id"].dropna().astype(str).str.strip())
    for gid in gids:
        if not gid:
            continue
        if gid in lau_codes:
            municipal += 1
            continue

        st = special_type_by_code.get(gid, "")
        st_l = st.lower()
        if "constituency" in st_l:
            constituency += 1
        else:
            # Auxiliary special rows should not define territorial granularity.
            if st_l in {
                "postal vote",
                "voters abroad",
                "e-voting",
                "aggregate",
                "special municipality",
                "ships at sea",
                "other",
            }:
                continue
            higher += 1

    total = municipal + constituency + higher
    if total <= 0:
        return "unknown"

    # Use plurality: whichever level has the most rows is the classification.
    # Municipal always wins ties (extra canton/higher rows are usually aggregates).
    if municipal >= constituency and municipal >= higher:
        return "municipal"
    if constituency >= higher:
        return "constituency"
    return "higher"


def build_coverage_non_municipal_flags(country_stats: dict[str, dict]) -> dict[str, dict[tuple[str, int], str]]:
    """Return CC -> {(election_type, year): level} for non-municipal availability.

    Years are flagged when the dominant geography is not municipal.
    Stored values are one of: constituency, higher, mixed, unknown.
    """
    communes_src = RESOURCES / "communes.csv"
    special_src = RESOURCES / "special_raw.csv"

    lau_codes: set[str] = set()
    special_type_by_code: dict[str, str] = {}

    if communes_src.exists():
        cdf = pd.read_csv(communes_src, dtype=str, low_memory=False, usecols=["gisco_id", "level"], na_values=[''], keep_default_na=False)
        mask = cdf["level"].astype(str).str.upper().str.startswith("LAU", na=False)
        lau_codes = set(cdf.loc[mask, "gisco_id"].dropna().astype(str).str.strip())

    if special_src.exists():
        sdf = pd.read_csv(special_src, dtype=str, low_memory=False, na_values=[''], keep_default_na=False)
        if "gisco_id" in sdf.columns and "type" in sdf.columns:
            for _, r in sdf[["gisco_id", "type"]].dropna(subset=["gisco_id"]).iterrows():
                special_type_by_code[str(r["gisco_id"]).strip()] = str(r.get("type") or "")

    def _iter_country_files(cc: str):
        dom = OUTPUT / cc
        if dom.is_dir():
            for p in sorted(dom.glob("*.csv")):
                lower = p.name.lower()
                if "_errors" in lower or "_special" in lower or "briefwahl" in lower:
                    continue
                if _YEAR_PREFIX_RE.match(p.name):
                    yield p, "Nat"
        eu = OUTPUT / f"EU_{cc}"
        if eu.is_dir():
            for p in sorted(eu.glob("*.csv")):
                lower = p.name.lower()
                if "_errors" in lower or "_special" in lower or "briefwahl" in lower:
                    continue
                if _YEAR_PREFIX_RE.match(p.name):
                    yield p, "EP"

    out: dict[str, dict[tuple[str, int], str]] = {}
    for cc in country_stats:
        per_year: dict[tuple[str, int], list[str]] = {}
        for p, prefix in _iter_country_files(cc):
            m = _YEAR_PREFIX_RE.match(p.name)
            if not m:
                continue
            year = m.group(1)
            level = _classify_election_file_level(p, lau_codes, special_type_by_code)
            election_type = "national" if prefix == "Nat" else "european"
            per_year.setdefault((election_type, int(year)), []).append(level)

        flagged: dict[tuple[str, int], str] = {}
        for key, levels in per_year.items():
            normalized = [lvl for lvl in levels if lvl != "municipal"]
            if not normalized:
                continue
            if "mixed" in normalized:
                flagged[key] = "mixed"
            elif "higher" in normalized:
                flagged[key] = "higher"
            elif "constituency" in normalized:
                flagged[key] = "constituency"
            else:
                flagged[key] = normalized[0]
        out[cc] = flagged
    return out


def _month_suffix(months: set[int]) -> str:
    """Return ' (Mon, Mon)' when a year has 2+ distinct months, else ''."""
    valid = sorted(m for m in months if 1 <= m <= 12)
    if len(valid) < 2:
        return ""
    return " (" + ", ".join(calendar.month_abbr[m] for m in valid) + ")"


def _marked_years_str(
    years: list[int],
    election_type: str,
    flags: dict[tuple[str, int], str],
    year_months: dict[int, set[int]] | None = None,
) -> str:
    """Format years, appending month abbreviations for multi-month years and '*' for non-municipal."""
    if not years:
        return "---"
    out = []
    for year in years:
        suffix = _month_suffix((year_months or {}).get(year, set()))
        marker = "*" if (election_type, year) in flags else ""
        out.append(f"{year}{suffix}{marker}")
    return ", ".join(out)


# ── statistics ──────────────────────────────────────────────────────────────
def _load_file_month_map() -> dict[str, int]:
    """Return output/file_path -> calendar month from data_files_reference.csv."""
    ref = ROOT / "data_files_reference.csv"
    if not ref.exists():
        return {}
    result: dict[str, int] = {}
    try:
        df = pd.read_csv(ref, dtype=str, low_memory=False)
        for _, row in df.iterrows():
            fp = str(row.get("file_path") or "").strip()
            date = str(row.get("election_date") or "").strip()
            if fp and len(date) >= 7:
                try:
                    result[fp] = int(date[5:7])
                except ValueError:
                    pass
    except Exception:
        pass
    return result


def _collect_elections(country: str) -> list[dict]:
    """Return list of {year, month, election_type} dicts for a country."""
    file_month = _load_file_month_map()
    elections = []

    def _parse_year(filename: str) -> int | None:
        lower = filename.lower()
        if "_errors" in lower or "_special" in lower or "briefwahl" in lower:
            return None
        m = _YEAR_RE.match(filename) or _YEAR_PREFIX_RE.match(filename)
        return int(m.group(1)) if m else None

    dom = OUTPUT / country
    if dom.is_dir():
        for csv in sorted(dom.glob("*.csv")):
            year = _parse_year(csv.name)
            if year is not None:
                fp = str(csv.relative_to(ROOT))
                elections.append({
                    "year": year,
                    "month": file_month.get(fp, 0),
                    "election_type": "national",
                })
    eu = OUTPUT / f"EU_{country}"
    if eu.is_dir():
        for csv in sorted(eu.glob("*.csv")):
            year = _parse_year(csv.name)
            if year is not None:
                fp = str(csv.relative_to(ROOT))
                elections.append({
                    "year": year,
                    "month": file_month.get(fp, 0),
                    "election_type": "european",
                })
    return elections


def _contest_counts() -> tuple[dict[str, int], dict[str, int]]:
    """Per-country counts of distinct election *contests*, matching the paper.

    A contest may span several result files: the two rounds of a two-round
    system and the several ballots of one election (e.g. the German first/second
    vote) count once. National contests are therefore deduplicated by election
    date (keeping round 1) and European ones by year, but two elections held in
    the same year (e.g. Greece 2012, Spain 2019) count separately -- which the
    plain distinct-year count used elsewhere would wrongly collapse.
    """
    ref = ROOT / "data_files_reference.csv"
    if not ref.exists():
        return {}, {}
    df = pd.read_csv(ref, dtype=str)
    df["is_eu"] = df["file_path"].apply(lambda p: "/EU_" in str(p))

    def _year(row) -> str | None:
        d = row["election_date"]
        if isinstance(d, str) and d.strip():
            return d[:4]
        m = re.search(r"(\d{4})", str(row["file_path"]).split("/")[-1])
        return m.group(1) if m else None

    df["year"] = df.apply(_year, axis=1)
    eu = df[df["is_eu"]].drop_duplicates(["country_code", "year"])
    nat = df[~df["is_eu"] & (df["round"].isna() | df["round"].isin(["1", "1.0"]))] \
        .drop_duplicates(["country_code", "election_date"])
    return (nat["country_code"].value_counts().to_dict(),
            eu["country_code"].value_counts().to_dict())


def compute_stats() -> dict:
    """Compute dataset-wide and per-country statistics."""
    all_countries: set[str] = set()
    for p in OUTPUT.iterdir():
        if p.is_dir():
            cc = p.name[3:] if p.name.startswith("EU_") else p.name
            all_countries.add(cc)

    # Contest counts consistent with the paper (see _contest_counts); fall back
    # to distinct-year counts per country when the reference file is missing.
    nat_counts, eu_counts = _contest_counts()

    country_stats = {}
    n_national = 0
    n_eu = 0
    year_min = 9999
    year_max = 0

    for cc in sorted(all_countries):
        elecs = _collect_elections(cc)
        nat = [e for e in elecs if e["election_type"] == "national"]
        eu = [e for e in elecs if e["election_type"] == "european"]
        nat_years = sorted({e["year"] for e in nat})
        eu_years = sorted({e["year"] for e in eu})
        # Build year → set of months (only non-zero months, i.e. files with a month suffix)
        nat_year_months: dict[int, set[int]] = {}
        for e in nat:
            if e["month"]:
                nat_year_months.setdefault(e["year"], set()).add(e["month"])
        eu_year_months: dict[int, set[int]] = {}
        for e in eu:
            if e["month"]:
                eu_year_months.setdefault(e["year"], set()).add(e["month"])
        country_stats[cc] = {
            "name": _COUNTRY_NAMES.get(cc, cc),
            "national_years": nat_years,
            "european_years": eu_years,
            "national_year_months": nat_year_months,
            "european_year_months": eu_year_months,
            "n_national": nat_counts.get(cc, len(nat_years)),
            "n_european": eu_counts.get(cc, len(eu_years)),
            "elections": elecs,
        }
        n_national += nat_counts.get(cc, len(nat_years))
        n_eu += eu_counts.get(cc, len(eu_years))
        for e in elecs:
            year_min = min(year_min, e["year"])
            year_max = max(year_max, e["year"])

    # Geo stats
    n_communes = 0
    n_special = 0
    communes_src = RESOURCES / "communes.csv"
    special_src = RESOURCES / "special_raw.csv"
    if communes_src.exists():
        n_communes = sum(1 for _ in open(communes_src)) - 1
    if special_src.exists():
        n_special = sum(1 for _ in open(special_src)) - 1

    # Parties stats
    n_parties = 0
    parties_src = ROOT / "parties_final.csv"
    if parties_src.exists():
        n_parties = sum(1 for _ in open(parties_src)) - 1

    # Data-point count: sum of (ncols - 2) * nrows over all election CSVs
    # The -2 subtracts the gisco_id and name identifier columns.
    n_data_points = 0
    for folder in OUTPUT.iterdir():
        if not folder.is_dir():
            continue
        for csv in folder.glob("*.csv"):
            if not _YEAR_RE.match(csv.name):
                continue
            try:
                with open(csv, encoding="utf-8", errors="replace") as fh:
                    header = fh.readline()
                    ncols = len(header.split(","))
                    nrows = sum(1 for _ in fh)
                n_data_points += max(0, ncols - 2) * nrows
            except Exception:
                pass

    return {
        "n_countries": len(all_countries),
        "year_min": year_min if year_min < 9999 else "?",
        "year_max": year_max if year_max > 0 else "?",
        "n_national_elections": n_national,
        "n_eu_elections": n_eu,
        "n_communes": n_communes,
        "n_special": n_special,
        "n_parties": n_parties,
        "n_data_points": n_data_points,
        "country_stats": country_stats,
    }


# ── template loading ─────────────────────────────────────────────────────────
def _load_template_fragment(name: str) -> str:
    path = TEMPLATES_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"% Template {name} not found\n"


def _load_country_template(cc: str) -> str:
    specific = TEMPLATES_DIR / "countries" / f"{cc}.tex"
    if specific.exists():
        return specific.read_text(encoding="utf-8")
    default = TEMPLATES_DIR / "countries" / "_default.tex"
    if default.exists():
        return default.read_text(encoding="utf-8")
    return ""


# ── LaTeX generation ─────────────────────────────────────────────────────────
REVISION_ID = 1

# \setmainfont{TeX Gyre Termes}
#\setsansfont[Scale=0.92]{TeX Gyre Heros}
#\setmonofont[Scale=0.88]{DejaVu Sans Mono}

MAIN_TEMPLATE = r"""
\documentclass[a4paper,11pt]{article}
\usepackage{fontspec}
\tracinglostchars=2 
\setmainfont{Tempora}[Ligatures=TeX]
\usepackage[english]{babel}
\usepackage{geometry}
\geometry{margin=2.5cm}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage{colortbl}
\usepackage{rotating}
\usepackage{hyperref}
\hypersetup{
    colorlinks=true,
    linkcolor=black,
    urlcolor=blue,
    pdftitle={BLUE\_DB Codebook},
    pdfauthor={BLUE\_DB Team},
}
\usepackage{titlesec}
\usepackage{fancyhdr}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small BLUE\_DB Codebook}
\fancyhead[R]{\small \thepage}
\renewcommand{\headrulewidth}{0.4pt}
\usepackage{xcolor}
\usepackage{tabularx}
\usepackage{enumitem}
\usepackage{parskip}

% --- macros ---
\newcommand{\bluepath}[1]{\texttt{#1}}
\newcommand{\bluevar}[1]{\texttt{#1}}

\begin{document}

% =====================================================================
%  TITLE PAGE
% =====================================================================
\begin{titlepage}
\centering
\vspace*{3cm}
{\Huge\bfseries BLUE\_DB\\[0.5em]}
\vspace{0.5em}
{\Large\bfseries Basic Local Units Election Database\\[0.5em]}
\vspace{0.5em}
{\large The Electoral Database of the\\
\textit{Electoral Bulletins of the European Union} (BLUE) project}\\
\vspace{1.5cm}
{\large Codebook}\\[2em]
{\large Version: v\VAR{year_max}.\VAR{revision_id}}\\[1em]
{\large \today}
\vspace{3cm}

\begin{tabular}{ll}
\textbf{Countries:}           & \VAR{n_countries} \\
\textbf{Period:}              & \VAR{year_min}--\VAR{year_max} \\
\textbf{National elections:}  & \VAR{n_national_elections} \\
\textbf{EP elections:}        & \VAR{n_eu_elections} \\
\textbf{Geographic units:}    & \VAR{n_communes} LAUs \\
                               & \VAR{n_special} special units \\
\textbf{Parties:}             & \VAR{n_parties} \\
\textbf{Data points:}         & \VAR{n_data_points} \\
\end{tabular}

\vfill
\end{titlepage}

\tableofcontents
\clearpage

% =====================================================================
%  1. INTRODUCTION
% =====================================================================
\section{Introduction}
\label{sec:intro}

\VAR{fragment_intro}

% =====================================================================
%  2. DATA SOURCES
% =====================================================================
\section{Parliaments and Data Sources}
\label{sec:sources}

Table~\ref{tab:sources} lists, for each country, the name of the directly
elected chamber whose results are collected in BLUE\_DB, together with the
primary data source (official electoral authority or statistics office).
In bicameral systems, the focus is on the lower house, which in European
democracy is always directly elected and often holds more authority
than the upper house.

\VAR{data_sources_table}
\label{tab:sources}

For the 2019 and 2024 European Parliament elections, the local-level data
is taken from \href{https://zenodo.org/records/14569325}{BLUE\_EP}\footnote{
Hublet., F. (2026). BLUE\_EP: A Dataset of Municipality-Level Results of European Parliament Elections. \emph{European Political Science}.}.

The typology of administrative units is derived from Eurostat's
\href{https://ec.europa.eu/eurostat/web/gisco}{Geographic Information System of the Commission (GISCO)},
which includes the
\href{https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/local-administrative-units}{Local Administrative Unit (LAU)}
typology for municipal-level units and
\href{https://ec.europa.eu/eurostat/web/nuts/}{Nomenclature of Territorial Statistical Units (NUTS)}
for regional-level units.
All GISCO data can be accessed at \href{https://gisco-services.ec.europa.eu/distribution/v2/}{\texttt{gisco-services.ec.europa.eu/distribution/v2/}}.

Other sources used to build the party taxonomy include
\href{https://en.wikipedia.org}{Wikipedia}, \href{https://wikidata.org}{Wikidata},
the \href{https://partyfacts.herokuapp.com/}{Party Facts} database,
and the European Parliament's \href{https://www.europarl.europa.eu/meps/en/full-list/all}{Database of MEPs}.

% =====================================================================
%  8. COVERAGE TABLE
% =====================================================================
\section{Country Coverage}
\label{sec:coverage}

The following table summarizes the elections included per country.
An asterisk (*) marks elections not primarily available at municipal level
(e.g. constituency-level, higher-level, or mixed geographic coverage).

\begin{longtable}{llp{5.5cm}p{5.5cm}}
\toprule
	\textbf{CC} & \textbf{Country} & \textbf{National elections} & \textbf{EP elections} \\
\midrule
\endhead
\bottomrule
\endlastfoot
\VAR{coverage_rows}
\end{longtable}

Results in \textbf{IE}, \textbf{IS}, \textbf{MT}, and \textbf{SI} are only available at the constituency level.
Early elections in \textbf{BE} are available at the level of \emph{electoral cantons}.
Results of the 2004 European Parliament election in \textbf{DE} are only reported for 
\emph{Landkreise} (NUTS 3).

\label{tab:coverage}

% =====================================================================
%  3. DATASET STRUCTURE
% =====================================================================
\section{Dataset Structure}
\label{sec:structure}

\VAR{fragment_overview}

% =====================================================================
%  5. VARIABLE DESCRIPTIONS
% =====================================================================
\section{Variable Descriptions}
\label{sec:variables}

\subsection{Geographic taxonomy}
\label{sec:variables_geo}

\VAR{fragment_variables_geo}

\subsection{Party taxonomy}
\label{sec:variables_parties}

\VAR{fragment_variables_parties}

\subsection{Election files}
\label{sec:variables_elections}

\VAR{fragment_variables_elections}

% =====================================================================
%  6. GEOGRAPHIC TAXONOMY
% =====================================================================
\section{Geographic Taxonomy}
\label{sec:geo}

\VAR{fragment_geo_typology}

\subsection{Overseas Countries and Territories}
\label{sec:oct}

\VAR{fragment_geo_oct}

\subsection{Voters Abroad}
\label{sec:abroad}

\VAR{fragment_geo_abroad}

\subsection{Postal Vote and E-Voting}
\label{sec:yy}

\VAR{fragment_geo_yy}

\subsection{Electoral Constituencies}
\label{sec:constituencies}

\VAR{fragment_geo_constituencies}

% =====================================================================
%  7. PARTIES TYPOLOGY
% =====================================================================
\section{Parties Taxonomy}
\label{sec:parties}

\VAR{fragment_parties_typology}

% =====================================================================
%  9. COUNTRY CHAPTERS
% =====================================================================
\section{Country Notes}
\label{ch:countries}

\VAR{country_sections}

\end{document}
"""


def _years_str(years: list[int], year_months: dict[int, set[int]] | None = None) -> str:
    """Format a list of years as a compact string, e.g. '2002, 2006, 2010–2024'.

    Years that appear more than once (multi-month elections) break any run and
    show their month abbreviations: '2024 (Jun, Jul)'.
    """
    if not years:
        return "---"
    result = []
    i = 0
    while i < len(years):
        suffix = _month_suffix((year_months or {}).get(years[i], set()))
        if suffix:
            # Multi-month year: always display individually, breaks any run
            result.append(f"{years[i]}{suffix}")
            i += 1
            continue
        j = i
        while (
            j + 1 < len(years)
            and years[j + 1] == years[j] + 1
            and not _month_suffix((year_months or {}).get(years[j + 1], set()))
        ):
            j += 1
        if j - i >= 2:
            result.append(f"{years[i]}--{years[j]}")
        else:
            result.extend(str(y) for y in years[i:j+1])
        i = j + 1
    return ", ".join(result)


def _apply_stats(text: str, stats: dict) -> str:
    """Replace all \\VAR{stat_name} tokens with computed values."""
    scalar_subs = {
        "VAR{n_countries}": f"{stats['n_countries']:,}",
        "VAR{year_min}": str(stats["year_min"]),
        "VAR{year_max}": str(stats["year_max"]),
        "VAR{n_national_elections}": f"{stats['n_national_elections']:,}",
        "VAR{n_eu_elections}": f"{stats['n_eu_elections']:,}",
        "VAR{n_communes}": f"{stats['n_communes']:,}",
        "VAR{n_special}": f"{stats['n_special']:,}",
        "VAR{n_parties}": f"{stats['n_parties']:,}",
        "VAR{n_data_points}": f"{stats['n_data_points']:,}",
        "VAR{revision_id}": str(REVISION_ID),
    }
    for key, value in scalar_subs.items():
        text = text.replace(f"\\{key}", value)
    return text


def render_tex(stats: dict) -> str:
    """Fill in the main LaTeX template with computed stats and fragments."""

    # --- coverage table rows ---
    rows = []
    non_municipal_flags = build_coverage_non_municipal_flags(stats["country_stats"])
    for cc, cs in sorted(stats["country_stats"].items(), key=lambda x: x[1]["name"]):
        name = _tex_escape(cs["name"])
        flags = non_municipal_flags.get(cc, {})
        nat = _tex_escape(_marked_years_str(cs["national_years"], "national", flags, cs.get("national_year_months")))
        eu = _tex_escape(_marked_years_str(cs["european_years"], "european", flags, cs.get("european_year_months")))
        rows.append("\\texttt{{{}}} & {} & {} & {} \\\\".format(cc, name, nat, eu))
    coverage_rows = "\n".join(rows)

    # --- per-country sections ---
    country_sections_parts = []
    for cc, cs in sorted(stats["country_stats"].items(), key=lambda x: x[1]["name"]):
        name = _tex_escape(cs["name"])
        annotation = _apply_stats(_load_country_template(cc), stats).strip()

        # Stats summary
        nat_str = _years_str(cs["national_years"], cs.get("national_year_months")) or "none"
        eu_str = _years_str(cs["european_years"], cs.get("european_year_months")) or "none"
        summary = (
            f"\\paragraph{{Coverage.}} "
            f"National elections: {_tex_escape(nat_str)}. "
            f"European Parliament elections: {_tex_escape(eu_str)}."
        )

        # Data source paragraph
        src_para = ""
        if cc in _DATA_SOURCES:
            parl, source, url = _DATA_SOURCES[cc]
            src_para = (
                f"\\paragraph{{Parliament and data source.}} "
                f"{_tex_escape(parl)}.  "
                f"Data from: \\href{{{url}}}{{{_tex_escape(source)}}}."
            )

        # Special units table (excluding Postal Vote)
        special_table = build_country_special_units_table(cc)

        block = (
            f"\\subsection{{{name} (\\texttt{{{cc}}})}}\n"
            f"\\label{{sec:country_{cc}}}\n\n"
            f"{summary}\n\n"
            f"{src_para}\n\n"
            f"{annotation}\n\n"
            f"{special_table}\n"
        )
        country_sections_parts.append(block)
    country_sections = "\n\\bigskip\n".join(country_sections_parts)

    # --- Load fragments and apply stats substitution to each ---
    active_countries = set(stats["country_stats"].keys())

    geo_oct = (
        _load_template_fragment("special_units_oct.tex")
        + "\n\n" + build_overseas_extra_nuts_table()
    )
    geo_abroad = (
        _load_template_fragment("special_units_abroad.tex")
        + "\n\n" + build_abroad_matrix(active_countries)
    )

    parties_typology = _load_template_fragment("parties_typology.tex")
    parties_typology = parties_typology.replace("\\VAR{ep_groups_table}", build_ep_groups_table())
    parties_typology = parties_typology.replace("\\VAR{european_parties_table}", build_european_parties_table())
    parties_typology = parties_typology.replace("\\VAR{ideology_table}", build_ideology_table())
    parties_typology += "\n\n" + build_parties_country_counts_table()

    fragments = {
        "fragment_intro": _apply_stats(_load_template_fragment("intro.tex"), stats),
        "fragment_overview": _apply_stats(_load_template_fragment("overview.tex"), stats),
        "fragment_variables_geo": _load_template_fragment("variables_geo.tex"),
        "fragment_variables_elections": _load_template_fragment("variables_elections.tex"),
        "fragment_variables_parties": _load_template_fragment("variables_parties.tex"),
        "fragment_geo_typology": _load_template_fragment("geo_typology.tex"),
        "fragment_geo_oct": geo_oct,
        "fragment_geo_abroad": geo_abroad,
        "fragment_geo_yy": _load_template_fragment("special_units_yy.tex"),
        "fragment_geo_constituencies": _load_template_fragment("special_units_constituencies.tex"),
        "fragment_parties_typology": parties_typology,
        "data_sources_table": build_data_sources_table(active_countries),
        "coverage_rows": coverage_rows,
        "country_sections": country_sections,
    }

    # --- Substitute into main template (stats first, then fragments) ---
    text = MAIN_TEMPLATE
    text = _apply_stats(text, stats)
    for key, value in fragments.items():
        text = text.replace(f"\\VAR{{{key}}}", value)
    return text


def compile_tex(tex_path: Path) -> bool:
    """Run xelatex twice in the codebook directory. Returns True on success."""
    cwd = tex_path.parent
    cmd = ["xelatex", "-interaction=nonstopmode", tex_path.name]
    for run in (1, 2):
        print(f"  xelatex pass {run}\u2026")
        result = subprocess.run(cmd, cwd=cwd, capture_output=True)
        if result.returncode != 0:
            # Print last 40 lines of log for diagnosis
            log_path = tex_path.with_suffix(".log")
            if log_path.exists():
                lines = log_path.read_text(errors="replace").splitlines()
                print("\n  --- xelatex log (last 40 lines) ---")
                print("\n".join(lines[-40:]))
            print(f"\n  ERROR: xelatex failed on pass {run} (exit {result.returncode})")
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Build BLUE_DB codebook PDF")
    parser.add_argument("--no-compile", action="store_true", help="Generate .tex only, skip xelatex")
    parser.add_argument("--open", action="store_true", help="Open PDF after compilation")
    args = parser.parse_args()

    CODEBOOK_DIR.mkdir(parents=True, exist_ok=True)

    print("=== Building BLUE_DB codebook ===\n")

    print("[1/3] Computing statistics…")
    stats = compute_stats()
    print(
        f"      {stats['n_countries']} countries, "
        f"{stats['n_national_elections']} national + "
        f"{stats['n_eu_elections']} EP elections, "
        f"period {stats['year_min']}–{stats['year_max']}"
    )

    print("[2/3] Rendering LaTeX…")
    tex = render_tex(stats)
    TEX_OUT.write_text(tex, encoding="utf-8")
    print(f"      Written: {TEX_OUT.relative_to(ROOT)}")

    if args.no_compile:
        print("\nSkipped xelatex (--no-compile).")
        return

    print("[3/3] Compiling PDF…")
    ok = compile_tex(TEX_OUT)
    if ok:
        print(f"\nDone.  PDF: {PDF_OUT.relative_to(ROOT)}")
        if args.open:
            subprocess.Popen(["xdg-open", str(PDF_OUT)])
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
