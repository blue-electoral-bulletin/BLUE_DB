# Package metadata and the `blue-db` command line (port of blue_db/cli.py).

#' BLUE_DB metadata
#'
#' `BLUE_DB_DOI`, `BLUE_DB_WEBSITE`, `BLUE_DB_GITHUB` and `BLUE_DB_CITATION`
#' identify the dataset; cite it with `BLUE_DB_CITATION`.
#' @name blue_db_metadata
#' @format Character strings.
NULL

#' @rdname blue_db_metadata
#' @export
BLUE_DB_DOI <- "10.5281/zenodo.21434111"
#' @rdname blue_db_metadata
#' @export
BLUE_DB_WEBSITE <- "https://electionatlas.eu"
#' @rdname blue_db_metadata
#' @export
BLUE_DB_GITHUB <- "https://github.com/blue-electoral-bulletin/BLUE_DB"
#' @rdname blue_db_metadata
#' @export
BLUE_DB_CITATION <- paste0(
  "Hublet, F., & Chiecchio, N. (2026). BLUE_DB \u2014 Basic Local Units Election ",
  "Database. Paris: Groupe d'\u00e9tudes g\u00e9opolitiques. ",
  "https://doi.org/10.5281/zenodo.21434111")

.version <- function() as.character(utils::packageVersion("bluedb"))

#' Show version, data locations and dataset size
#'
#' The R counterpart of `blue-db info`.
#' @param dist Optional path to the `dist/` folder.
#' @return The located `dist/` folder (invisibly), or `NULL`.
#' @export
blue_info <- function(dist = NULL) {
  cat("blue-db ", .version(), " (R)\n", sep = "")
  cat("website  ", BLUE_DB_WEBSITE, "\n", sep = "")
  cat("github   ", BLUE_DB_GITHUB, "\n", sep = "")
  cat("doi      https://doi.org/", BLUE_DB_DOI, "\n", sep = "")
  cat("cite     ", BLUE_DB_CITATION, "\n\n", sep = "")
  d <- tryCatch(dist_dir(dist), blue_data_not_found = function(e) {
    cat("dist     NOT FOUND\n\n", conditionMessage(e), "\n", sep = "")
    NULL
  })
  if (!is.null(d)) cat("dist     ", d, "\n", sep = "")
  m <- maps_dir()
  cat("maps     ", m, if (dir.exists(m)) "" else "  (missing; GISCO only)", "\n", sep = "")
  cat("cache    ", cache_dir(), "\n", sep = "")
  cat("external ", external_dir(), "  (Jersey and Guernsey results, fetched on demand)\n", sep = "")
  cat("\nenv      ", paste(ENV_HOME, ENV_DIST, ENV_MAPS, ENV_CACHE, sep = ", "), "\n", sep = "")
  cat("unpack the Zenodo archive into ", user_data_dir(), " to install the data\n", sep = "")
  if (!is.null(d)) {
    db <- BlueDB(d)
    idx <- db$index
    cat(sprintf("\n%d election files, %d countries, %d-%d, %d parties\n",
                nrow(idx), length(unique(idx$country_code)),
                min(idx$year, na.rm = TRUE), max(idx$year, na.rm = TRUE), nrow(db$parties)))
  }
  invisible(d)
}

.usage <- "usage: blue-db [--version] [--dist DIST] [--no-external] {info,elections,results} ...

BLUE_DB \u2014 municipality-level European election results.

commands:
  info        show version, data locations and dataset size
  elections   list elections from the index
  results     export election results as CSV

filters (elections, results):
  -c, --country XX     ISO-2 country code; repeatable
  -t, --type TYPE      national | european
  -y, --year YEAR      election year; repeatable
  --vote-type VT       e.g. party, single_member, Zweitstimme
  --round {1,2}        election round
  -o, --out FILE       write CSV here instead of stdout
  --no-external        leave out the Jersey and Guernsey results, which are
                       not under CC BY 4.0

results only:
  --latest             keep only the most recent election per country
  -l, --level LEVEL    LAU (default) | NUTS3 | NUTS2 | NUTS1 | NUTS0
  --by GROUP           ideology | ep_group | eu_party
  --shares             convert vote counts to shares of valid votes
"

.parse_args <- function(argv) {
  a <- list(country = NULL, year = NULL, level = "LAU", latest = FALSE, shares = FALSE,
            no_external = FALSE)
  flags <- c("--latest" = "latest", "--shares" = "shares", "--no-external" = "no_external")
  opts <- c("--dist" = "dist", "--country" = "country", "-c" = "country",
            "--type" = "type", "-t" = "type", "--year" = "year", "-y" = "year",
            "--vote-type" = "vote_type", "--round" = "round", "--out" = "out",
            "-o" = "out", "--level" = "level", "-l" = "level", "--by" = "by")
  repeatable <- c("country", "year")
  i <- 1
  while (i <= length(argv)) {
    arg <- argv[i]
    val <- NULL
    if (grepl("^--[^=]+=", arg)) {
      val <- sub("^[^=]*=", "", arg)
      arg <- sub("=.*$", "", arg)
    }
    if (arg %in% c("-h", "--help")) return(list(help = TRUE))
    if (arg == "--version") return(list(version = TRUE))
    if (arg %in% names(flags)) {
      a[[flags[[arg]]]] <- TRUE
    } else if (arg %in% names(opts)) {
      if (is.null(val)) {
        i <- i + 1
        if (i > length(argv)) stop("argument ", arg, ": expected one argument")
        val <- argv[i]
      }
      key <- opts[[arg]]
      a[[key]] <- if (key %in% repeatable) c(a[[key]], val) else val
    } else if (!startsWith(arg, "-") && is.null(a$command)) {
      a$command <- arg
    } else {
      stop("unrecognized argument: ", arg)
    }
    i <- i + 1
  }
  if (!is.null(a$year)) a$year <- as.integer(a$year)
  if (!is.null(a$round)) {
    a$round <- as.integer(a$round)
    if (!(a$round %in% 1:2)) stop("argument --round: invalid choice (choose from 1, 2)")
  }
  if (!is.null(a$type) && !(a$type %in% c("national", "european")))
    stop("argument --type: invalid choice: ", a$type)
  if (!(a$level %in% .GEO_LEVELS)) stop("argument --level: invalid choice: ", a$level)
  if (!is.null(a$by) && !(a$by %in% c("ideology", "ep_group", "eu_party")))
    stop("argument --by: invalid choice: ", a$by)
  a
}

.write_out <- function(df, out) {
  if (!is.null(out)) {
    dir.create(dirname(out), recursive = TRUE, showWarnings = FALSE)
    utils::write.csv(df, out, row.names = FALSE, na = "", fileEncoding = "UTF-8")
    message(nrow(df), " rows -> ", out)
  } else {
    utils::write.csv(df, stdout(), row.names = FALSE, na = "")
  }
}

#' Run the `blue-db` command line
#'
#' Same commands and options as the Python `blue-db` (`info`, `elections`,
#' `results`).  The package installs it as `exec/blue-db`, an `Rscript` script.
#' @param argv Command-line arguments.
#' @return An exit status (0 on success, 2 when the data cannot be found).
#' @export
cli_main <- function(argv = commandArgs(trailingOnly = TRUE)) {
  a <- tryCatch(.parse_args(argv), error = function(e) {
    message("blue-db: error: ", conditionMessage(e))
    NULL
  })
  if (is.null(a)) return(invisible(2L))
  if (isTRUE(a$help)) { cat(.usage); return(invisible(0L)) }
  if (isTRUE(a$version)) { cat("blue-db", .version(), "\n"); return(invisible(0L)) }
  if (is.null(a$command) || !(a$command %in% c("info", "elections", "results"))) {
    message(.usage, "\nblue-db: error: choose a command: info, elections, results")
    return(invisible(2L))
  }
  tryCatch({
    if (a$command == "info") {
      blue_info(a$dist)
    } else {
      db <- BlueDB(a$dist, external = !a$no_external)
      df <- if (a$command == "elections") {
        db$elections(countries = a$country, election_type = a$type, years = a$year,
                     vote_type = a$vote_type, round = a$round)
      } else {
        r <- db$results(countries = a$country, election_type = a$type, years = a$year,
                        vote_type = a$vote_type, round = a$round,
                        latest_per_country = a$latest, geo_level = a$level, aggregate_by = a$by)
        if (a$shares) db$vote_shares(r) else r
      }
      .write_out(df, a$out)
    }
    invisible(0L)
  }, blue_data_not_found = function(e) {
    message("error: ", conditionMessage(e))
    invisible(2L)
  })
}
