# Data-access layer for the BLUE_DB distribution (port of blue_db/blue_db.py).

# Columns in every election CSV that are not party vote columns.
.META <- c("gisco_id", "name", "registered", "turnout", "invalid", "blank")
.STATS <- c("registered", "turnout", "invalid", "blank")
.GEO_LEVELS <- c("LAU", "NUTS3", "NUTS2", "NUTS1", "NUTS0")

# pandas' default missing-value markers, so files read here match the Python
# package value for value.
.PANDAS_NA <- c("", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN",
                "-nan", "1.#IND", "1.#QNAN", "<NA>", "N/A", "NA", "NULL", "NaN",
                "None", "n/a", "nan", "null")

# Vote-type priority: lower = preferred for cross-country comparison.  A blank
# vote type (pure PR) ranks 0; types not listed rank 5.
.VOTE_TYPE_PRIORITY <- c(
  Zweitstimme   = 1L,   # DE second vote (list)
  party         = 1L,   # HU party list
  national      = 1L,   # AD national list
  Erststimme    = 9L,   # DE first vote (constituency)
  single_member = 9L,   # HU/FR single-member
  parish        = 9L)   # AD parish (district) seats

# A contest is one (country, date, type): national and EP elections held on the
# same day (BE 2014, LU 2009, ...) are two contests, not two ballots.
.CONTEST <- c("country_code", "election_date", "election_type")

.read_csv <- function(path, na = .PANDAS_NA, character = NULL) {
  cc <- NA
  if (!is.null(character)) cc <- stats::setNames(rep("character", length(character)), character)
  utils::read.csv(path, check.names = FALSE, stringsAsFactors = FALSE,
                  na.strings = na, colClasses = cc, encoding = "UTF-8",
                  fileEncoding = "UTF-8")
}

.vote_type_rank <- function(vt) {
  r <- unname(.VOTE_TYPE_PRIORITY[vt])
  r[is.na(r)] <- 5L
  r[is.na(vt) | vt == ""] <- 0L
  r
}

# Rows of `df` in the order given by the keys (stable).
.order_by <- function(df, keys) {
  if (!nrow(df)) return(df)
  df[do.call(order, c(unname(as.list(df[keys])), method = "radix")), , drop = FALSE]
}

.key <- function(df, keys) do.call(paste, c(lapply(df[keys], as.character), sep = "\r"))

# First (or last) row per group, then sorted by the group keys -- the pandas
# `groupby(keys).head(1)` / `.tail(1)` followed by `sort_values(keys)`.
.one_per_group <- function(df, keys, last = FALSE) {
  df <- df[!duplicated(.key(df, keys), fromLast = last), , drop = FALSE]
  .order_by(df, keys)
}

# Bind data frames, filling columns missing from some with NA (pandas concat).
.bind_rows <- function(parts) {
  parts <- Filter(Negate(is.null), parts)
  if (!length(parts)) return(data.frame())
  cols <- unique(unlist(lapply(parts, names)))
  full <- Filter(function(p) nrow(p) > 0, parts)
  if (!length(full)) {
    out <- parts[[1]]
    for (c in setdiff(cols, names(out))) out[[c]] <- logical()
    return(out[cols])
  }
  parts <- lapply(full, function(p) {
    for (c in setdiff(cols, names(p))) p[[c]] <- NA
    p[cols]
  })
  out <- do.call(rbind, c(parts, make.row.names = FALSE))
  rownames(out) <- NULL
  out
}

# Python truthiness of a parsed JSON value.
.truthy <- function(x) {
  !is.null(x) && length(x) > 0 && !(is.character(x) && !nzchar(x[[1]])) &&
    !(is.numeric(x) && x[[1]] == 0) && !isFALSE(x)
}
.pystr <- function(x) if (is.numeric(x) && x == round(x)) format(x, scientific = FALSE) else as.character(x)

#' Open the BLUE_DB distribution
#'
#' Creates the access object for a BLUE_DB `dist/` folder.  Methods are called
#' with `$`, as in the Python package:
#'
#' * `db$elections(countries, election_type, years, vote_type, round)` -- a
#'   filtered view of the election index.
#' * `db$results(countries, election_type, years, vote_type, round,
#'   latest_per_country, latest_per_country_year, elections_df, geo_level,
#'   aggregate_by)` -- one row per (election, geographic unit) and one column
#'   per party.  Aggregate to `geo_level` `"LAU"` (default), `"NUTS3"`,
#'   `"NUTS2"`, `"NUTS1"` or `"NUTS0"`, and collapse parties with
#'   `aggregate_by` = `"ideology"`, `"ep_group"` or `"eu_party"`.
#' * `db$canonical_elections(countries, election_type, years)` -- one file per
#'   contest, preferring list votes and first rounds.
#' * `db$vote_shares(df)` -- append `<col>_pct` columns.
#' * `db$fill_forward(df, years)` -- carry each country's last election forward
#'   over years without one.
#'
#' Reference tables are available as fields: `db$index`, `db$parties`,
#' `db$ep_groups`, `db$eu_parties`, `db$nuts`, `db$laus`, `db$special`,
#' `db$geo_chain`, `db$lau_periods`, `db$nuts_end_years` and
#' `db$nuts_successors`.
#'
#' Unless a `round` is given, `results()` keeps the first round of each
#' contest; unless a `vote_type` is given, it keeps the ballot preferred for
#' cross-country comparison (the list vote over constituency votes).
#'
#' The Jersey and Guernsey results are not under CC BY 4.0 and not part of the
#' Zenodo record.  When they are not in `dist/`, they are downloaded from the
#' GitHub repository on first use (see [external_dir()]), and their
#' publishers' terms are shown once per session.
#'
#' @param dist Path to the `dist/` folder; located automatically when omitted
#'   (see [dist_dir()]).
#' @param external Include the Jersey and Guernsey results; `FALSE` leaves them
#'   out of the index altogether.
#' @return An object of class `BlueDB`.
#' @examples
#' \dontrun{
#' db <- BlueDB()
#' df <- db$results(election_type = "national", latest_per_country = TRUE,
#'                  geo_level = "NUTS0", aggregate_by = "ideology")
#' pt <- db$results(countries = "PT", election_type = "national",
#'                  years = 2022, geo_level = "LAU")
#' }
#' @export
BlueDB <- function(dist = NULL, external = TRUE) {
  self <- new.env(parent = emptyenv())
  self$dist <- dist_dir(dist)
  self$external <- isTRUE(external)
  cache <- new.env(parent = emptyenv())        # lazily loaded tables
  labels <- new.env(parent = emptyenv())       # per-party lookups

  lazy <- function(name, build) {
    function() {
      if (is.null(cache[[name]])) cache[[name]] <- build()
      cache[[name]]
    }
  }
  f <- function(...) file.path(self$dist, ...)

  # --- reference tables -------------------------------------------------------
  index <- lazy("index", function() {
    df <- .read_csv(f("elections", "index.csv"))
    df$election_date <- as.Date(df$election_date)
    df$year <- as.integer(format(df$election_date, "%Y"))
    df$election_type <- ifelse(grepl("/EU_", df$file_path, fixed = TRUE), "european", "national")
    df$round <- as.numeric(df$round)
    df$vote_type <- as.character(df$vote_type)
    # Indexes built before the columns existed: derive them.
    ext <- df$country_code %in% names(.EXTERNAL)
    if (is.null(df$distribution)) df$distribution <- ifelse(ext, "external", "included")
    if (is.null(df$licence))
      df$licence <- vapply(df$country_code, function(cc)
        if (cc %in% names(.EXTERNAL)) .EXTERNAL[[cc]]$licence else "CC-BY-4.0",
        "", USE.NAMES = FALSE)
    if (!self$external) df <- df[df$distribution != "external", , drop = FALSE]
    rownames(df) <- NULL
    df
  })
  parties    <- lazy("parties", function() .read_csv(f("parties", "parties.csv"), na = ""))
  ep_groups  <- lazy("ep_groups", function() .read_csv(f("parties", "ep_groups.csv")))
  eu_parties <- lazy("eu_parties", function() .read_csv(f("parties", "eu_parties.csv"), na = ""))
  nuts    <- lazy("nuts", function() .read_csv(f("geo", "nuts.csv"), character = c("gisco_id", "parent")))
  laus    <- lazy("laus", function() .read_csv(f("geo", "laus.csv"), character = c("gisco_id", "parent")))
  special <- lazy("special", function() .read_csv(f("geo", "special.csv"), character = c("gisco_id", "parent")))

  # --- geographic hierarchy ---------------------------------------------------
  # Internally the chain is one named vector per level (gisco_id -> ancestor),
  # `known` the IDs that have an entry and `nonempty` those whose entry is not
  # empty; the `geo_chain` field exposes it as a data frame.
  chain <- lazy("chain", function() {
    lv <- list(LAU = character(), NUTS0 = character(), NUTS1 = character(),
               NUTS2 = character(), NUTS3 = character())
    put <- function(ids, level, values) lv[[level]][ids] <<- values

    n <- nuts()
    is_nuts <- grepl("NUTS", n$level %||% character(), fixed = TRUE) & !is.na(n$level)
    nid <- as.character(n$gisco_id[is_nuts])
    nlvl <- stats::setNames(as.integer(sub(".*\\s", "", n$level[is_nuts])), nid)
    np <- n$parent[is_nuts]
    # A parent may be a versioned row id ("HR02(2021)") rather than a code.
    id2gid <- stats::setNames(as.character(n$gisco_id), as.character(n$id))
    id2gid <- id2gid[!duplicated(names(id2gid), fromLast = TRUE)]
    np <- ifelse(np %in% names(id2gid), unname(id2gid[np]), np)
    keep <- !is.na(np) & nzchar(np)
    npar <- stats::setNames(np[keep], nid[keep])
    # Several rows may share a code: the last one wins, as in a Python dict.
    nlvl <- nlvl[!duplicated(names(nlvl), fromLast = TRUE)]
    npar <- npar[!duplicated(names(npar), fromLast = TRUE)]
    nid <- unique(nid)
    known <- unique(c(nid, unname(npar)))
    # each region's ancestors, the furthest one winning at a repeated level
    for (g in nid) {
      cur <- g
      for (i in seq_len(20)) {
        if (is.na(cur)) break
        l <- nlvl[cur]
        if (!is.na(l)) lv[[paste0("NUTS", l)]][g] <- cur
        cur <- unname(npar[cur])
      }
    }

    l <- laus()
    l <- l[!duplicated(l$gisco_id, fromLast = TRUE), , drop = FALSE]
    lid <- as.character(l$gisco_id)
    lpar <- l$parent
    put(lid, "LAU", lid)
    for (lev in c("NUTS0", "NUTS1", "NUTS2", "NUTS3")) {
      v <- unname(lv[[lev]][lpar])
      ok <- !is.na(v)
      lv[[lev]][lid[ok]] <- v[ok]
      lv[[lev]] <- lv[[lev]][!(names(lv[[lev]]) %in% lid[!ok])]
    }
    known <- unique(c(known, lid))

    # Special units inherit their parent's chain.  The first row of a unit
    # wins, units already in the chain are skipped, and a parent that is
    # itself a special unit counts only once it has been processed (the
    # Python loop is sequential), so those few are handled in file order.
    s <- special()
    sid <- as.character(s$gisco_id)
    take <- !duplicated(sid) & !(sid %in% known)
    sid <- sid[take]
    sp <- s$parent[take]
    from_known <- !is.na(sp) & sp %in% known
    for (lev in names(lv)) {
      v <- unname(lv[[lev]][sp[from_known]])
      ok <- !is.na(v)
      lv[[lev]][sid[from_known][ok]] <- v[ok]
    }
    later <- which(!from_known & !is.na(sp) & sp %in% sid)
    seen_at <- stats::setNames(seq_along(sid), sid)
    for (i in later) {
      p <- sp[i]
      if (seen_at[[p]] < i) {
        for (lev in names(lv)) if (!is.na(lv[[lev]][p])) lv[[lev]][sid[i]] <- lv[[lev]][[p]]
      } else if (nchar(p) == 2) {
        lv$NUTS0[sid[i]] <- p
      }
    }
    bare <- !from_known & !is.na(sp) & !(seq_along(sid) %in% later) & nchar(sp) == 2
    lv$NUTS0[sid[bare]] <- sp[bare]
    known <- c(known, sid)
    nonempty <- unique(unlist(lapply(lv, names)))
    list(levels = lv, known = known, nonempty = nonempty)
  })

  geo_chain <- function() {
    ch <- chain()
    ids <- ch$known
    out <- data.frame(gisco_id = ids, stringsAsFactors = FALSE)
    for (lev in .GEO_LEVELS) out[[lev]] <- unname(ch$levels[[lev]][ids])
    out
  }

  lau_periods <- lazy("lau_periods", function() {
    l <- laus()
    keep <- !is.na(l$parent) & nzchar(trimws(l$parent))
    yr <- function(v) suppressWarnings(as.integer(as.numeric(v)))
    data.frame(gisco_id = as.character(l$gisco_id[keep]),
               start_year = yr(l$start_year[keep]),
               end_year = yr(l$end_year[keep]),
               parent = trimws(l$parent[keep]), stringsAsFactors = FALSE)
  })

  # LAU's NUTS parent valid in `year` (end-date exclusive), or NA.
  lau_parent_at <- function(ids, year) {
    p <- lau_periods()
    p <- p[p$gisco_id %in% ids, , drop = FALSE]
    n <- table(p$gisco_id)
    out <- stats::setNames(rep(NA_character_, length(ids)), ids)
    single <- p[p$gisco_id %in% names(n)[n == 1], , drop = FALSE]
    out[single$gisco_id] <- single$parent
    multi <- p[p$gisco_id %in% names(n)[n > 1], , drop = FALSE]
    cover <- (is.na(multi$start_year) | year >= multi$start_year) &
             (is.na(multi$end_year) | year < multi$end_year)
    multi <- multi[cover, , drop = FALSE]
    multi <- multi[!duplicated(multi$gisco_id), , drop = FALSE]
    out[multi$gisco_id] <- multi$parent
    out
  }

  nuts_end_years <- lazy("nuts_end_years", function() {
    n <- nuts()
    ok <- !is.na(n$end_year)
    stats::setNames(as.integer(n$end_year[ok]), n$gisco_id[ok])
  })

  # Each obsolete NUTS id -> its active successor (multi-hop chains resolved).
  nuts_successors <- lazy("nuts_successors", function() {
    n <- nuts()
    n <- n[!is.na(n$predecessors), , drop = FALSE]
    raw <- character()
    for (i in seq_len(nrow(n))) {
      for (pred in trimws(strsplit(as.character(n$predecessors[i]), ",", fixed = TRUE)[[1]]))
        if (nzchar(pred)) raw[pred] <- as.character(n$gisco_id[i])
    }
    res <- raw
    for (old in names(raw)) {
      cur <- old
      seen <- character()
      while (!is.na(raw[cur]) && !(cur %in% seen)) {
        seen <- c(seen, cur)
        cur <- raw[[cur]]
      }
      res[old] <- cur
    }
    res
  })

  # The latest NUTS id, following renumberings (CZ061 -> CZ063 in 2008), so
  # results join against the current vintage regardless of election year.
  active_nuts <- function(ids) {
    succ <- nuts_successors()
    vapply(ids, function(id) {
      seen <- character()
      while (!(id %in% seen) && !is.na(succ[id])) {
        seen <- c(seen, id)
        id <- succ[[id]]
      }
      id
    }, character(1), USE.NAMES = FALSE)
  }

  # --- party attributes -------------------------------------------------------
  party_row <- function(party_id, country) {
    df <- parties()
    cand <- which(df$party_id == party_id)
    if (!length(cand)) return(NULL)
    if (nzchar(country) && "region" %in% names(df)) {
      m <- cand[!is.na(df$region[cand]) & df$region[cand] == country]
      if (length(m)) return(df[m[1], , drop = FALSE])
    }
    df[cand[1], , drop = FALSE]
  }

  spells_of <- function(raw) {
    if (is.null(raw) || is.na(raw)) return(list())
    tryCatch(jsonlite::fromJSON(as.character(raw), simplifyVector = FALSE),
             error = function(e) list())
  }

  memo <- function(key, compute) {
    if (is.null(labels[[key]])) labels[[key]] <- compute()
    labels[[key]]
  }

  ideology <- function(party_id, country) {
    memo(paste("i", party_id, country, sep = "\r"), function() {
      row <- party_row(party_id, country)
      if (is.null(row)) return("unknown")
      v <- row$ideology
      if (is.na(v)) "nan" else as.character(v)   # str(NaN), as in Python
    })
  }

  ep_group <- function(party_id, year, country) {
    memo(paste("g", party_id, year, country, sep = "\r"), function() {
      row <- party_row(party_id, country)
      if (is.null(row)) return("unknown")
      for (spell in spells_of(row$ep_group)) {
        ys <- if (.truthy(spell$year_start)) .pystr(spell$year_start) else "0"
        y0 <- if (nzchar(substr(ys, 1, 4))) as.integer(substr(ys, 1, 4)) else 0L
        if (is.na(y0)) stop("invalid year_start in ep_group of ", party_id)
        y1 <- if (.truthy(spell$year_end)) as.integer(substr(.pystr(spell$year_end), 1, 4)) else 9999L
        if (y0 <= year && year < y1)
          return(if (.truthy(spell$party_id)) as.character(spell$party_id) else "unknown")
      }
      "unknown"
    })
  }

  eu_party <- function(party_id, year, country) {
    memo(paste("e", party_id, year, country, sep = "\r"), function() {
      row <- party_row(party_id, country)
      if (is.null(row)) return("unknown")
      for (spell in spells_of(row$european_party)) {
        d0 <- substr(if (.truthy(spell$date_start)) .pystr(spell$date_start) else "", 1, 4)
        d1 <- substr(if (.truthy(spell$date_end)) .pystr(spell$date_end) else "", 1, 4)
        y0 <- if (grepl("^[0-9]+$", d0)) as.integer(d0) else 0L
        y1 <- if (grepl("^[0-9]+$", d1)) as.integer(d1) else 9999L
        if (y0 <= year && year < y1)
          return(if (.truthy(spell$organization)) as.character(spell$organization) else "unknown")
      }
      "unknown"
    })
  }

  group_label <- function(party_id, year, aggregate_by, country) {
    switch(aggregate_by,
           ideology = ideology(party_id, country),
           ep_group = ep_group(party_id, year, country),
           eu_party = eu_party(party_id, year, country),
           party_id)
  }

  # --- election selection -----------------------------------------------------
  self$elections <- function(countries = NULL, election_type = NULL, years = NULL,
                             vote_type = NULL, round = NULL) {
    df <- index()
    if (!is.null(countries)) df <- df[df$country_code %in% countries, , drop = FALSE]
    if (!is.null(election_type)) df <- df[df$election_type %in% election_type[1], , drop = FALSE]
    if (!is.null(years)) df <- df[df$year %in% years, , drop = FALSE]
    if (!is.null(vote_type)) {
      vt <- df$vote_type
      vt[is.na(vt)] <- ""
      df <- df[vt == vote_type, , drop = FALSE]
    }
    if (!is.null(round)) df <- df[!is.na(df$round) & df$round == as.numeric(round), , drop = FALSE]
    rownames(df) <- NULL
    df
  }

  self$canonical_elections <- function(countries = NULL, election_type = NULL, years = NULL) {
    df <- self$elections(countries = countries, election_type = election_type, years = years)
    df$.vt <- .vote_type_rank(df$vote_type)
    df$.rd <- ifelse(is.na(df$round), 0L, as.integer(df$round))   # round 1 preferred
    df <- .order_by(df, c(".vt", ".rd"))
    df <- .one_per_group(df, .CONTEST)
    df$.vt <- NULL
    df$.rd <- NULL
    rownames(df) <- NULL
    df
  }

  # One canonical ballot per contest: later rounds dropped, and the preferred
  # vote type kept when several share one contest (e.g. the German two votes).
  first_round_ballots <- function(df, pick_vote_type = TRUE) {
    if ("round" %in% names(df)) df <- df[is.na(df$round) | df$round <= 1, , drop = FALSE]
    if (pick_vote_type && "vote_type" %in% names(df)) {
      df$.vt <- .vote_type_rank(df$vote_type)
      df <- .one_per_group(.order_by(df, ".vt"), .CONTEST)
      df$.vt <- NULL
    }
    rownames(df) <- NULL
    df
  }

  # --- results ----------------------------------------------------------------
  self$results <- function(countries = NULL, election_type = NULL, years = NULL,
                           vote_type = NULL, round = NULL,
                           latest_per_country = FALSE, latest_per_country_year = FALSE,
                           elections_df = NULL, geo_level = "LAU", aggregate_by = NULL) {
    geo_level <- match.arg(geo_level, .GEO_LEVELS)
    if (!is.null(aggregate_by))
      aggregate_by <- match.arg(aggregate_by, c("ideology", "ep_group", "eu_party"))
    if (!is.null(elections_df)) {
      elecs <- elections_df
    } else {
      elecs <- self$elections(countries = countries, election_type = election_type,
                              years = years, vote_type = vote_type, round = round)
      # First round and preferred vote type unless pinned, so that
      # latest_per_country picks e.g. the French first round, not the runoff.
      if (is.null(round)) elecs <- first_round_ballots(elecs, pick_vote_type = is.null(vote_type))
    }
    if (isTRUE(latest_per_country_year)) {
      elecs <- .one_per_group(.order_by(elecs, "election_date"), c("country_code", "year"), last = TRUE)
    } else if (isTRUE(latest_per_country)) {
      elecs <- .one_per_group(.order_by(elecs, "election_date"), "country_code", last = TRUE)
    }
    parts <- vector("list", nrow(elecs))
    for (i in seq_len(nrow(elecs))) {
      row <- elecs[i, , drop = FALSE]
      parts[[i]] <- tryCatch(load_one(row, geo_level, aggregate_by), error = function(e) {
        warning("Skipping ", row$file_path, ": ", conditionMessage(e), call. = FALSE)
        NULL
      })
    }
    .bind_rows(parts)
  }

  # Local path of an election file, fetching external ones if needed.
  election_file <- function(row) {
    path <- file.path(self$dist, row$file_path)
    if (!identical(row$distribution, "external")) return(path)
    .external_notice(row$country_code)
    if (file.exists(path)) path else .fetch_external(row$file_path)
  }

  load_one <- function(row, geo_level, aggregate_by) {
    df <- .read_csv(election_file(row), character = "gisco_id")
    party_cols <- setdiff(names(df), .META)
    year <- as.integer(row$year)
    num <- function(x) { x <- suppressWarnings(as.numeric(x)); x[is.na(x)] <- 0; x }
    for (c in c(party_cols, intersect(.STATS, names(df)))) df[[c]] <- num(df[[c]])

    country <- if (is.na(row$country_code)) "" else as.character(row$country_code)
    if (!is.null(aggregate_by)) {
      lbl <- vapply(party_cols, group_label, character(1), year = year,
                    aggregate_by = aggregate_by, country = country, USE.NAMES = FALSE)
      groups <- unique(lbl)
      sums <- lapply(groups, function(g) rowSums(df[party_cols[lbl == g]]))
      df <- df[setdiff(names(df), party_cols)]
      for (k in seq_along(groups)) df[[groups[k]]] <- sums[[k]]
      value_cols <- groups
    } else {
      value_cols <- party_cols
    }

    if (geo_level != "LAU") {
      ch <- chain()
      ids <- unique(df$gisco_id)
      parent <- lau_parent_at(ids, year)
      lvl <- ch$levels[[geo_level]]
      flat <- unname(lvl[ids])
      via <- ifelse(parent %in% ch$nonempty, unname(lvl[parent]),
                    if (geo_level == "NUTS3") parent else NA_character_)
      nuts_id <- ifelse(!is.na(parent), via, flat)
      nuts_id[is.na(nuts_id) | nuts_id == ""] <- ""
      has <- nuts_id != ""
      nuts_id[has] <- active_nuts(nuts_id[has])
      geo <- stats::setNames(nuts_id, ids)[df$gisco_id]
      keep <- geo != ""
      cols <- c(intersect(.STATS, names(df)), value_cols)
      if (any(keep)) {
        sums <- rowsum(as.matrix(df[keep, cols, drop = FALSE]), geo[keep], reorder = FALSE)
        sums <- sums[order(rownames(sums), method = "radix"), , drop = FALSE]  # pandas order
        df <- data.frame(geo_id = rownames(sums), sums, check.names = FALSE,
                         stringsAsFactors = FALSE)
      } else {                                  # nothing in this level: no rows
        df <- data.frame(geo_id = character(), df[0, cols, drop = FALSE],
                         check.names = FALSE, stringsAsFactors = FALSE)
      }
      df$geo_name <- rep("", nrow(df))
    } else {
      names(df)[names(df) == "gisco_id"] <- "geo_id"
      if ("name" %in% names(df)) names(df)[names(df) == "name"] <- "geo_name"
      else df$geo_name <- ""
    }

    n <- nrow(df)
    df$country_code <- rep(row$country_code, n)
    df$election_date <- rep(row$election_date, n)
    df$year <- rep(year, n)
    df$election_type <- rep(row$election_type, n)
    df$geo_level <- rep(geo_level, n)
    if (!n) df$geo_name <- character()
    front <- c("country_code", "election_date", "year", "election_type",
               "geo_id", "geo_name", "geo_level", intersect(.STATS, names(df)))
    rownames(df) <- NULL
    df[c(front, setdiff(names(df), front))]
  }

  # --- helpers ----------------------------------------------------------------
  self$vote_shares <- function(df) {
    meta_like <- c("country_code", "election_date", "year", "election_type",
                   "geo_id", "geo_name", "geo_level", .STATS)
    val <- setdiff(names(df), meta_like)
    total <- rowSums(df[val], na.rm = TRUE)   # pandas skips missing values
    total[total == 0] <- NaN
    for (c in val) df[[paste0(c, "_pct")]] <- df[[c]] / total * 100
    df
  }

  self$fill_forward <- function(df, years = NULL) {
    if (is.null(years)) years <- seq(min(df$year), max(df$year))
    locf <- function(x) {
      idx <- cummax(ifelse(is.na(x), 0L, seq_along(x)))
      x[idx > 0] <- x[idx[idx > 0]]
      x
    }
    parts <- lapply(unique(df$country_code), function(cc) {
      sub <- .order_by(df[df$country_code == cc, , drop = FALSE], "year")
      if (anyDuplicated(sub$year)) stop("cannot reindex on a duplicate year (", cc, ")")
      out <- sub[match(years, sub$year), setdiff(names(sub), "year"), drop = FALSE]
      out[] <- lapply(out, locf)
      out <- cbind(data.frame(year = as.integer(years)), out)
      out$country_code <- cc
      out
    })
    .bind_rows(parts)
  }

  # --- fields -----------------------------------------------------------------
  fields <- list(index = index, parties = parties, ep_groups = ep_groups,
                 eu_parties = eu_parties, nuts = nuts, laus = laus, special = special,
                 geo_chain = geo_chain, lau_periods = lau_periods,
                 nuts_end_years = nuts_end_years, nuts_successors = nuts_successors)
  for (nm in names(fields)) makeActiveBinding(nm, fields[[nm]], self)
  self$.first_round_ballots <- first_round_ballots
  self$.vote_type_rank <- .vote_type_rank
  class(self) <- "BlueDB"
  self
}

#' @export
print.BlueDB <- function(x, ...) {
  cat("<BlueDB>", x$dist, "\n")
  invisible(x)
}

#' @export
format.BlueDB <- function(x, ...) paste("<BlueDB>", x$dist)
