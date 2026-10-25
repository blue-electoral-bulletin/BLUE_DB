# Location of the BLUE_DB data directories (port of blue_db/_paths.py).
#
# The published distribution (dist/) and the constituency shapefiles (maps/)
# are far too large to ship inside the package, so they are
# located at run time.  Search order, first hit wins:
#
# 1. an explicit path passed to BlueDB() / BlueGeo();
# 2. the environment variables BLUE_DB_DIST / BLUE_DB_MAPS;
# 3. $BLUE_DB_HOME/<name>;
# 4. <name>/ or BLUE_DB/<name>/ in the working directory or any parent (which
#    covers running from a source checkout);
# 5. the per-user data directory, shared with the Python package
#    ($XDG_DATA_HOME/blue_db, ~/Library/Application Support/blue_db,
#    %LOCALAPPDATA%\blue_db).

ENV_HOME  <- "BLUE_DB_HOME"
ENV_DIST  <- "BLUE_DB_DIST"
ENV_MAPS  <- "BLUE_DB_MAPS"
ENV_CACHE <- "BLUE_DB_CACHE"

.env_path <- function(var) {
  v <- Sys.getenv(var, "")
  if (nzchar(v)) path.expand(v) else NULL
}

.os <- function() {
  if (.Platform$OS.type == "windows") "windows"
  else if (Sys.info()[["sysname"]] == "Darwin") "darwin"
  else "unix"
}

#' Per-user directory where an unpacked BLUE_DB distribution is expected
#'
#' The same directory the Python package uses, so one download serves both.
#' @return A path.
#' @export
user_data_dir <- function() {
  base <- switch(.os(),
    windows = .env_path("LOCALAPPDATA") %||% file.path(path.expand("~"), "AppData", "Local"),
    darwin  = file.path(path.expand("~"), "Library", "Application Support"),
    .env_path("XDG_DATA_HOME") %||% file.path(path.expand("~"), ".local", "share"))
  file.path(base, "blue_db")
}

.user_cache_dir <- function() {
  base <- switch(.os(),
    windows = .env_path("LOCALAPPDATA") %||% file.path(path.expand("~"), "AppData", "Local"),
    darwin  = file.path(path.expand("~"), "Library", "Caches"),
    .env_path("XDG_CACHE_HOME") %||% file.path(path.expand("~"), ".cache"))
  file.path(base, "blue_db")
}

`%||%` <- function(a, b) if (is.null(a)) b else a

# The working directory and each of its parents.
.parents <- function() {
  out <- character()
  cur <- normalizePath(getwd(), mustWork = FALSE)
  repeat {
    out <- c(out, cur)
    up <- dirname(cur)
    if (identical(up, cur)) break
    cur <- up
  }
  out
}

.env_paths <- function(name, env_var) {
  c(.env_path(env_var),
    if (!is.null(.env_path(ENV_HOME))) file.path(.env_path(ENV_HOME), name))
}

.candidates <- function(name, env_var) {
  out <- .env_paths(name, env_var)
  for (p in .parents()) out <- c(out, file.path(p, name), file.path(p, "BLUE_DB", name))
  c(out, file.path(user_data_dir(), name), file.path(user_data_dir(), "BLUE_DB", name))
}

.not_found_message <- function(name, env_var, tried) {
  shown <- paste0("  - ", utils::head(tried, 8), collapse = "\n")
  paste0(
    "BLUE_DB ", name, "/ directory not found.  Locations tried:\n", shown, "\n",
    "\nFix this by either:\n",
    "  * passing the path explicitly, e.g. BlueDB(dist = '/data/BLUE_DB/dist');\n",
    "  * setting ", env_var, "=/path/to/", name, ";\n",
    "  * setting ", ENV_HOME, "=/path/to/BLUE_DB (holding dist/ and maps/);\n",
    "  * unpacking the Zenodo archive into ", user_data_dir(), ".\n",
    "Run `blue-db info` (or bluedb::blue_info()) to see where the library is looking.")
}

# Signal the R counterpart of the Python BlueDataNotFound exception.
.data_not_found <- function(msg) {
  stop(structure(class = c("blue_data_not_found", "error", "condition"),
                 list(message = msg, call = NULL)))
}

#' Locate the BLUE_DB data directories
#'
#' `dist_dir()` resolves the `dist/` distribution folder and signals a
#' condition of class `blue_data_not_found` when it cannot be found.
#' `maps_dir()` resolves the optional `maps/` shapefile folder (a missing one
#' is not an error: geometries then come from GISCO alone).  `cache_dir()` is
#' the download cache for GISCO tiles, shared with the Python package.
#'
#' @param explicit An explicit path, which takes precedence.
#' @return A path.
#' @export
dist_dir <- function(explicit = NULL) {
  if (!is.null(explicit)) {
    path <- path.expand(explicit)
    if (!file.exists(path)) .data_not_found(paste("dist folder not found:", path))
    return(path)
  }
  tried <- .candidates("dist", ENV_DIST)
  configured <- .env_paths("dist", ENV_DIST)
  for (path in tried) {
    if (file.exists(file.path(path, "elections", "index.csv"))) return(path)
    if (path %in% configured) {
      # An explicitly configured location that does not hold the data is an
      # error, not a reason to silently fall back to another copy.
      src <- if (identical(.env_path(ENV_DIST), path)) ENV_DIST else ENV_HOME
      .data_not_found(paste0(src, " points at ", path,
                             ", which does not contain elections/index.csv."))
    }
  }
  .data_not_found(.not_found_message("dist", ENV_DIST, tried))
}

#' @rdname dist_dir
#' @export
maps_dir <- function(explicit = NULL) {
  if (!is.null(explicit)) return(path.expand(explicit))
  tried <- .candidates("maps", ENV_MAPS)
  for (path in tried) if (dir.exists(path)) return(path)
  tried[[1]]
}

#' @rdname dist_dir
#' @export
cache_dir <- function(explicit = NULL) {
  if (!is.null(explicit)) return(path.expand(explicit))
  if (!is.null(.env_path(ENV_CACHE))) return(.env_path(ENV_CACHE))
  if (!is.null(.env_path(ENV_HOME))) return(file.path(.env_path(ENV_HOME), ".cache", "geo"))
  # Reuse the cache of the checkout holding dist/ (found from the working
  # directory or the located dist/), so tiles already downloaded by the build
  # scripts or the Python package are not fetched again.
  roots <- .parents()
  d <- tryCatch(dist_dir(), error = function(e) NULL)
  if (!is.null(d)) roots <- c(dirname(normalizePath(d, mustWork = FALSE)), roots)
  for (p in roots) {
    if (file.exists(file.path(p, "dist", "elections", "index.csv")) &&
        dir.exists(file.path(p, ".cache", "geo")))
      return(file.path(p, ".cache", "geo"))
  }
  file.path(.user_cache_dir(), "geo")
}

# Files under resources/ (overrides, foreign constituencies, ...): searched in
# $BLUE_DB_HOME, the folder holding dist/, and the working directory and its
# parents -- the order of the Python package after its own source checkout.
.resource <- function(name) {
  bases <- .env_path(ENV_HOME)
  d <- tryCatch(dist_dir(), error = function(e) NULL)
  if (!is.null(d)) bases <- c(bases, dirname(normalizePath(d, mustWork = FALSE)))
  bases <- c(bases, .parents())
  for (b in unique(bases)) {
    p <- file.path(b, "resources", name)
    if (file.exists(p)) return(p)
  }
  NULL
}
