# Geometry resolution for BLUE_DB electoral data (port of blue_db/blue_maps.py),
# returning sf objects in EPSG:3035 (ETRS89-LAEA Europe).
#
# Resolution priority for each gisco_id:
#   0. resources/geo_overrides.csv -- hand-maintained references, always win;
#   1. maps/ shapefiles -- maps/{CC}[_{YEAR}].shp, maps/EU_{CC}_{YEAR}.shp;
#   2. GISCO municipalities -- LAU (2011+) or communes (2001-2010), at the
#      country's effective vintage, with neighbouring vintages as fallback;
#   2b. units of another territory reported with the election (FO, GL in DK);
#   3. GISCO NUTS (levels 3 -> 0), voters abroad as their host country, and
#      special units drawn as their NUTS parent when alone in it;
#   4. constituencies made of whole foreign countries, as their union.
#
# Downloads and caches (GeoJSON tiles, the communes GeoPackages, the vintage
# ID lists) use the same files and directory as the Python package.

.GISCO_LAU  <- "https://gisco-services.ec.europa.eu/distribution/v2/lau/"
.GISCO_COMM <- "https://gisco-services.ec.europa.eu/distribution/v2/communes/"
.GISCO_NUTS <- "https://gisco-services.ec.europa.eu/distribution/v2/nuts/"
.GISCO_CNTR <- "https://gisco-services.ec.europa.eu/distribution/v2/countries/"

# How far from an election's own vintage another may be consulted for units it
# lacks, how many such vintages to try, and the share of units the own vintage
# must resolve for the rest to count as churn rather than a broken vintage.
.MAX_VINTAGE_DISTANCE  <- 10
.MAX_FALLBACK_VINTAGES <- 3
.FALLBACK_THRESHOLD    <- 0.95

.LAU_YEARS  <- 2011:2024
.NUTS_YEARS <- c(2003, 2006, 2010, 2013, 2016, 2021, 2024)
.CNTR_YEARS <- c(2001, 2006, 2010, 2013, 2016, 2020, 2024)
.COMM_FILES <- c("2001" = "COMM_RG_01M_2001_3035", "2004" = "COMM_RG_2004_3035",
                 "2006" = "COMM_RG_2006_3035", "2008" = "COMM_RG_2008_3035",
                 "2010" = "COMM_RG_01M_2010_3035")
.COMM_YEARS <- as.integer(names(.COMM_FILES))
.MUNI_YEARS <- c(.COMM_YEARS, .LAU_YEARS)

# Continental Europe in EPSG:3035 (minx, miny, maxx, maxy): the mainland and
# Mediterranean islands, without far overseas territories.
.CONTINENTAL_BBOX <- c(2.4e6, 1.2e6, 7.6e6, 5.6e6)

# Antarctica and the uninhabited sub-Antarctic dependencies.
.ANTARCTIC <- c("AQ", "BV", "GS", "HM", "TF")
# NUTS spells two countries differently from ISO 3166-1.
.ISO_TO_NUTS <- c(GR = "EL", GB = "UK")
# Codes GISCO once published for states the results still use.
.HISTORIC_COUNTRIES <- list(CS = "YU", DD = "DE", SU = "RU")

.geo <- new.env(parent = emptyenv())   # module-level caches

.need_sf <- function() {
  if (!requireNamespace("sf", quietly = TRUE))
    stop("Geometries need the 'sf' package: install.packages(\"sf\")", call. = FALSE)
}

.memo <- function(key, compute, keep = Inf) {
  hit <- .geo[[key]]
  if (!is.null(hit)) return(hit)
  val <- compute()
  if (is.finite(keep)) {                # small LRU for the large tiles
    order <- c(setdiff(.geo$.lru %||% character(), key), key)
    while (length(order) > keep) {
      rm(list = order[1], envir = .geo)
      order <- order[-1]
    }
    .geo$.lru <- order
  }
  .geo[[key]] <- val
  val
}

.best_year <- function(requested, available) {
  c <- available[available <= requested]
  if (length(c)) max(c) else min(available)
}

# --- source references --------------------------------------------------------

#' References to polygons
#'
#' Resolution produces a *reference* to a polygon rather than the polygon: a
#' `kind` (`"maps"`, `"mapsset"`, `"lau"`, `"lauset"`, `"nuts"`, `"nutsset"`,
#' `"cntr"`, `"cntrset"`), a `layer` (a shapefile name, a vintage, or
#' `"vintage:level"`) and the `source_id` of the feature(s) in that layer.  The
#' text form is `"kind:layer#source_id"`, as in `geo_lookup.csv`.
#'
#' @param kind,layer,source_id Parts of the reference.
#' @param text A reference in text form.
#' @return An object of class `blue_source_ref`.
#' @export
source_ref <- function(kind, layer, source_id) {
  structure(list(kind = kind, layer = layer, source_id = source_id),
            class = "blue_source_ref")
}

#' @rdname source_ref
#' @export
parse_source_ref <- function(text) {
  head <- sub("#.*$", "", text)
  id <- if (grepl("#", text, fixed = TRUE)) sub("^[^#]*#", "", text) else ""
  kind <- sub(":.*$", "", head)
  layer <- if (grepl(":", head, fixed = TRUE)) sub("^[^:]*:", "", head) else ""
  source_ref(kind, layer, id)
}

#' @export
format.blue_source_ref <- function(x, ...) paste0(x$kind, ":", x$layer, "#", x$source_id)
#' @export
print.blue_source_ref <- function(x, ...) { cat(format(x), "\n"); invisible(x) }
#' @export
as.character.blue_source_ref <- function(x, ...) format(x)

# --- resources ----------------------------------------------------------------

.read_resource <- function(name, reader) {
  path <- .resource(name)
  if (is.null(path)) return(NULL)
  tryCatch(reader(path), error = function(e) {
    warning("Could not read ", path, ": ", conditionMessage(e), call. = FALSE)
    NULL
  })
}
.res_csv <- function(path) utils::read.csv(path, colClasses = "character", na.strings = character(),
                                           encoding = "UTF-8", fileEncoding = "UTF-8",
                                           check.names = FALSE)

.vintage_floors <- function() .memo("floors", function() {
  d <- .read_resource("geo_corrections.csv", .res_csv)
  if (is.null(d)) return(integer())
  cc <- toupper(trimws(d$country))
  raw <- trimws(d$min_vintage)
  ok <- nzchar(cc) & grepl("^[0-9]+$", raw)
  stats::setNames(as.integer(raw[ok]), cc[ok])
})

#' Municipal vintage used to draw a country
#'
#' `year` itself unless the country has a floor in
#' `resources/geo_corrections.csv` and the year falls below it.
#' @param country_code ISO-2 code.
#' @param year Election year.
#' @return An integer year.
#' @export
effective_vintage <- function(country_code, year) {
  if (is.null(country_code) || is.na(country_code) || !nzchar(country_code)) return(year)
  floor <- .vintage_floors()[toupper(country_code)]
  if (is.na(floor)) year else max(year, floor)
}

.geo_overrides <- function() .memo("overrides", function() {
  d <- .read_resource("geo_overrides.csv", .res_csv)
  if (is.null(d)) return(list())
  gid <- trimws(d$gisco_id)
  kind <- trimws(d$source_kind)
  ok <- nzchar(gid) & nzchar(kind) & !startsWith(gid, "#")
  d <- d[ok, , drop = FALSE]
  gid <- gid[ok]
  y0 <- trimws(d$year_from); y1 <- trimws(d$year_to)
  rows <- data.frame(
    gisco_id = gid,
    y0 = ifelse(grepl("^[0-9]+$", y0), as.integer(y0), 0L),
    y1 = ifelse(grepl("^[0-9]+$", y1), as.integer(y1), 9999L),
    etype = tolower(trimws(d$election_type)),
    kind = kind[ok], layer = trimws(d$source_layer),
    source_id = ifelse(nzchar(trimws(d$source_id)), trimws(d$source_id), gid),
    stringsAsFactors = FALSE)
  # narrowest window first, typed rows before untyped ones (stable)
  rows <- rows[order(rows$y1 - rows$y0, !nzchar(rows$etype), method = "radix"), ]
  split(rows, factor(rows$gisco_id, levels = unique(rows$gisco_id)))
})

#' The overriding reference for a unit in a year, if any
#'
#' @param gisco_id Unit ID.
#' @param year Election year.
#' @param election_type `"national"` or `"european"`.
#' @return A [source_ref()] or `NULL`.
#' @export
geo_override <- function(gisco_id, year, election_type = "national") {
  rows <- .geo_overrides()[[gisco_id]]
  if (is.null(rows)) return(NULL)
  hit <- rows$y0 <= year & year <= rows$y1 & rows$etype %in% c("", tolower(election_type))
  if (!any(hit)) return(NULL)
  r <- rows[which(hit)[1], ]
  source_ref(r$kind, r$layer, r$source_id)
}

.country_regions <- function() .memo("regions", function() {
  d <- .read_resource("country_regions.csv", .res_csv)
  if (is.null(d)) return(character())
  code <- trimws(d$country_code)
  stats::setNames(trimws(d$continent), code)[nzchar(code)]
})

.foreign_constituencies <- function() .memo("foreign", function() {
  d <- .read_resource("foreign_constituencies.csv", .res_csv)
  if (is.null(d)) return(list())
  gid <- trimws(d$gisco_id)
  mode <- tolower(trimws(d$mode))
  out <- list()
  for (i in which(nzchar(gid) & nzchar(mode))) {
    m <- trimws(strsplit(d$members[i], ";", fixed = TRUE)[[1]])
    out[[gid[i]]] <- list(mode = mode[i], members = m[nzchar(m)])
  }
  out
})

#' Countries making up a foreign constituency
#'
#' `character(0)` if `gisco_id` is not one.  `siblings` are the other unit IDs
#' of the same election: the countries they cover are excluded, so a
#' "remaining countries" unit does not swallow those counted separately.
#' @param gisco_id Unit ID.
#' @param siblings Other unit IDs of the election.
#' @return Sorted country codes.
#' @export
foreign_members <- function(gisco_id, siblings = character()) {
  fc <- .foreign_constituencies()
  entry <- fc[[gisco_id]]
  if (is.null(entry)) return(character())
  if (entry$mode == "list") return(sort(entry$members, method = "radix"))
  regions <- .country_regions()
  wanted <- if (identical(entry$members, "WORLD")) names(regions)
            else names(regions)[regions %in% entry$members]
  wanted <- setdiff(wanted, .ANTARCTIC)
  home <- toupper(strsplit(gisco_id, "_", fixed = TRUE)[[1]][1])
  wanted <- setdiff(wanted, if (!is.na(.ISO_TO_NUTS[home])) .ISO_TO_NUTS[[home]] else home)
  for (s in siblings) {
    if (identical(s, gisco_id)) next
    h <- abroad_host(s)
    if (!is.na(h)) wanted <- setdiff(wanted, h)
    other <- fc[[s]]
    if (!is.null(other) && other$mode == "list") wanted <- setdiff(wanted, other$members)
  }
  sort(unique(wanted), method = "radix")
}

# --- identifier rules -----------------------------------------------------------

# Left-pad a digit string with zeros (Python's str.zfill).
.zfill <- function(s, width) {
  s <- as.character(s)
  pad <- pmax(width - nchar(s), 0)
  paste0(strrep("0", pad), s)
}

.bg_ekatte <- function() .memo("bg_ekatte", function() {
  rows <- .read_resource("bg_ekatte.json", function(p) jsonlite::fromJSON(p, simplifyVector = FALSE))
  out <- character()
  for (r in rows) if (.truthy(r$ekatte) && .truthy(r$obshtina))
    out[.zfill(r$ekatte, 5)] <- as.character(r$obshtina)
  out
})

.bg_obshtina_id <- function(gisco_id) {
  m <- regmatches(gisco_id, regexec("^BG_([A-Z]{3}[0-9]{2})[0-9]{5}$", gisco_id))[[1]]
  if (length(m)) return(paste0("BG_", m[2]))
  m <- regmatches(gisco_id, regexec("^BG_([0-9]{5})$", gisco_id))[[1]]
  if (length(m)) {
    o <- .bg_ekatte()[.zfill(m[2], 5)]
    return(if (is.na(o)) NA_character_ else paste0("BG_", o))
  }
  NA_character_
}

.lt_municipalities <- function() .memo("lt_munis", function() {
  d <- .read_resource("lt_municipalities.csv", .res_csv)
  if (is.null(d)) return(character())
  src <- trimws(d$gisco_id); dst <- trimws(d$municipality)
  ok <- nzchar(src) & nzchar(dst) & src != dst
  stats::setNames(dst[ok], src[ok])
})

#' NUTS code of the country an abroad unit's voters live in
#'
#' Abroad units are coded `CC_CCZZxx`, `xx` the host country (`BG_BGZZDE`:
#' Bulgarians in Germany); Romania's files also use the bare `ROZZDE`.
#' @param gisco_id Unit ID.
#' @return A country code, or `NA` (also for pooled `ZZ98`/`ZZ99` units).
#' @export
abroad_host <- function(gisco_id) {
  m <- regmatches(gisco_id, regexec("(?i)^([A-Z]{2})(?:_\\1)?ZZ([A-Z]{2})$",
                                    trimws(gisco_id), perl = TRUE))[[1]]
  if (!length(m)) return(NA_character_)
  host <- toupper(m[3])
  if (!is.na(.ISO_TO_NUTS[host])) .ISO_TO_NUTS[[host]] else host
}

#' The ID a unit's geometry is published under
#'
#' Bulgaria's pre-2017 obshtina IDs (`BG_BLG5277058`, `BG_66665`) map to the
#' obshtina (`BG_BLG52`); Lithuania's 2004-2016 IDs to the modern
#' savivaldybe.  Everything else is returned unchanged.
#' @param gisco_id Unit IDs.
#' @return IDs.
#' @export
geo_join_id <- function(gisco_id) {
  vapply(as.character(gisco_id), function(g) {
    b <- .bg_obshtina_id(g)
    if (!is.na(b)) return(b)
    l <- .lt_municipalities()[g]
    if (!is.na(l)) unname(l) else g
  }, character(1), USE.NAMES = FALSE)
}

# BLUE_DB gisco_id -> the GISCO LAU GISCO_ID, where the conventions differ.
.normalize_lau_id <- function(gisco_id) {
  if (!grepl("_", gisco_id, fixed = TRUE)) return(NA_character_)
  cc <- sub("_.*$", "", gisco_id)
  local <- sub("^[^_]*_", "", gisco_id)
  switch(cc,
    NL = paste0("NL_GM", local),
    CH = paste0("CH_CH", local),
    LI = paste0("LI_LI", local),
    RO = if (grepl("^[+-]?[0-9]+$", local)) paste0("RO_", as.integer(local)) else NA_character_,
    BG = .bg_obshtina_id(gisco_id),
    NA_character_)
}

# Communes-dataset national code -> BLUE_DB gisco_id (pre-2011 vintages).
.commune_gisco_id <- function(cntr_code, comm_code, vintage) {
  if (is.na(comm_code) || nchar(comm_code) < 3) return(NA_character_)
  cc <- if (cntr_code == "GR") "EL" else cntr_code
  lau <- substring(comm_code, 3)
  ch <- function(s, i) substr(s, i, i)
  last <- function(s, n) substr(s, max(1, nchar(s) - n + 1), nchar(s))
  if (cc == "FR") {
    dep <- substr(lau, 3, 4)
    if (dep %in% c("97", "9A", "9B", "9C", "9D")) {
      if (nchar(lau) < 5) return(NA_character_)
      lau <- if (vintage %in% c(2001, 2004)) paste0("97", last(lau, 3))
             else paste0("97", ch(lau, 5), last(lau, 2))
    } else lau <- paste0(dep, last(lau, 3))
  } else if (cc == "DE") {
    if (nchar(lau) < 3) return(NA_character_)
    lau <- if (ch(lau, 3) != "0") paste0(substr(lau, 1, 3), substr(lau, 5, 6), last(lau, 3))
           else paste0(substr(lau, 1, 2), substr(lau, 4, 6), last(lau, 3))
  } else if (cc %in% c("IT", "CZ", "SK")) lau <- last(lau, 6)
  else if (cc %in% c("ES", "RO", "BE", "HR")) lau <- last(lau, 5)
  else if (cc %in% c("HU", "CH", "LT", "NL", "EE", "IS")) lau <- last(lau, 4)
  else if (cc == "FI") lau <- last(lau, 3)
  else if (cc == "PT") lau <- substr(lau, 2, nchar(lau) - 1)
  else if (cc == "LU") lau <- substr(lau, 1, 4)
  else if (cc == "MT") lau <- paste0("0", substring(lau, 2))
  else if (cc == "LI") lau <- paste0("0000", lau)
  .normalize_commune_id(toupper(paste0(cc, "_", lau)))
}

.normalize_commune_id <- function(s) {
  for (p in list(c("NL_GM", "NL_"), c("NL_NL", "NL_"), c("MF_", "FR_"),
                 c("LI_LI", "LI_"), c("MT_MT", "MT_")))
    s <- gsub(p[1], p[2], s, fixed = TRUE)
  if (startsWith(s, "CH")) {
    s <- gsub("CH_CH", "CH_", s, fixed = TRUE)
    num <- sub("^.*_", "", s)
    if (grepl("^[+-]?[0-9]+$", num)) s <- sprintf("CH_%04d", as.integer(num))
  }
  s
}

# Fallback IDs from the national statistical code, as (lengths, width).
.ALIAS_RULES <- list(DK = list(3, NA), RO = list(4:6, 6), PT = list(6, NA),
                     PL = list(9, NA), DE = list(8, NA), FR = list(5, NA))

.commune_alias_id <- function(cntr_code, nsi_code) {
  rule <- .ALIAS_RULES[[cntr_code]]
  if (is.null(rule) || is.na(nsi_code)) return(NA_character_)
  n <- trimws(nsi_code)
  if (substr(n, 1, 2) == cntr_code) n <- substring(n, 3)
  if (!grepl("^[0-9]+$", n) || !(nchar(n) %in% rule[[1]])) return(NA_character_)
  width <- rule[[2]]
  paste0(cntr_code, "_", if (is.na(width)) n else .zfill(n, width))
}

# --- GISCO download & cache -------------------------------------------------------

.to_3035 <- function(x) {
  if (attr(x, "sf_column") != "geometry") sf::st_geometry(x) <- "geometry"
  if (is.na(sf::st_crs(x))) sf::st_set_crs(x, 3035)
  else if (!identical(sf::st_crs(x)$epsg, 3035L)) sf::st_transform(x, 3035)
  else x
}

.download <- function(url, dest, timeout) {
  dir.create(dirname(dest), recursive = TRUE, showWarnings = FALSE)
  message("    Downloading ", url, " ...")
  old <- options(timeout = max(timeout, getOption("timeout")))
  on.exit(options(old))
  tmp <- paste0(dest, ".part")
  utils::download.file(url, tmp, mode = "wb", quiet = TRUE)
  file.rename(tmp, dest)
}

.fetch_geojson <- function(url, cache, fname, timeout = 120) {
  cp <- file.path(cache, fname)
  if (!file.exists(cp)) .download(url, cp, timeout)
  .to_3035(sf::st_read(cp, quiet = TRUE))
}

.slim <- function(x, id_col) {
  names(x)[names(x) == id_col] <- "gisco_id"
  x[, "gisco_id"]
}

# One row per gisco_id, merging the few multi-polygon units.
.dissolve_by_id <- function(x) {
  x <- x[, "gisco_id"]
  dup <- x$gisco_id %in% x$gisco_id[duplicated(x$gisco_id)]
  if (!any(dup)) return(x)
  d <- x[dup, ]
  ids <- unique(d$gisco_id)
  geoms <- lapply(split(sf::st_geometry(d), factor(d$gisco_id, levels = ids)),
                  function(g) sf::st_union(g)[[1]])
  rbind(x[!dup, ], sf::st_sf(gisco_id = ids, geometry = sf::st_sfc(unname(geoms), crs = sf::st_crs(x))))
}

.union <- function(geoms) {
  geoms <- Filter(Negate(is.null), geoms)
  if (!length(geoms)) return(NULL)
  sfc <- sf::st_sfc(geoms, crs = 3035)
  tryCatch(sf::st_union(sfc)[[1]], error = function(e)
    tryCatch(sf::st_union(sf::st_make_valid(sfc))[[1]], error = function(e2) {
      warning("Could not dissolve ", length(sfc), " geometries: ", conditionMessage(e2),
              call. = FALSE)
      NULL
    }))
}

.commune_dataset <- function(year, cache) {
  stem <- .COMM_FILES[[as.character(year)]]
  slim <- file.path(cache, paste0(stem, "_blue.gpkg"))
  if (file.exists(slim)) {
    x <- .to_3035(sf::st_read(slim, quiet = TRUE))
    x$is_alias <- as.logical(x$is_alias)
    return(x[, c("gisco_id", "is_alias")])
  }
  raw <- file.path(cache, paste0(stem, ".shp.zip"))
  if (!file.exists(raw)) .download(paste0(.GISCO_COMM, "shp/", stem, ".shp.zip"), raw, 600)
  # The .dbf encodings vary between vintages; only ASCII code columns are used.
  g <- .to_3035(sf::st_read(paste0("/vsizip/", raw), quiet = TRUE, options = "ENCODING=LATIN1"))
  code_col <- if ("COMM_CODE" %in% names(g)) "COMM_CODE" else "COMM_ID"
  g$gisco_id <- mapply(.commune_gisco_id, g$CNTR_CODE, g[[code_col]], year, USE.NAMES = FALSE)
  g <- g[!is.na(g$gisco_id) & !endsWith(g$gisco_id, "_UNK") & !startsWith(g$gisco_id, "CH_9"), ]
  # 2001 has each commune's coastal water as its own polygon (SURF_COVR "S").
  if ("SURF_COVR" %in% names(g)) {
    land <- g[is.na(g$SURF_COVR) | g$SURF_COVR != "S", ]
    g <- rbind(land, g[!(g$gisco_id %in% land$gisco_id), ])
  }
  primary <- .dissolve_by_id(g)
  primary$is_alias <- FALSE
  parts <- list(primary)
  if ("NSI_CODE" %in% names(g)) {
    alias <- mapply(.commune_alias_id, g$CNTR_CODE, g$NSI_CODE, USE.NAMES = FALSE)
    ok <- !is.na(alias) & alias != g$gisco_id & !(alias %in% primary$gisco_id)
    if (any(ok)) {
      groups <- split(g$gisco_id[ok], factor(alias[ok], levels = unique(alias[ok])))
      geom <- sf::st_geometry(primary)[match(unlist(groups), primary$gisco_id)]
      idx <- rep(seq_along(groups), lengths(groups))
      merged <- lapply(seq_along(groups), function(k) {
        gk <- geom[idx == k]
        if (length(gk) > 1) sf::st_union(gk)[[1]] else gk[[1]]
      })
      parts[[2]] <- sf::st_sf(gisco_id = names(groups), is_alias = TRUE,
                              geometry = sf::st_sfc(merged, crs = 3035))
    }
  }
  out <- do.call(rbind, parts)
  sf::st_write(out, slim, driver = "GPKG", quiet = TRUE)
  out
}

.muni_vintage <- function(y, cache) .memo(paste0("muni", y, cache), function() {
  if (y %in% .COMM_YEARS) return(.commune_dataset(y, cache))
  fname <- sprintf("LAU_RG_01M_%d_3035.geojson", y)
  x <- .fetch_geojson(paste0(.GISCO_LAU, "geojson/", fname), cache, fname)
  x <- .slim(x, if ("GISCO_ID" %in% names(x)) "GISCO_ID" else "gisco_id")
  x$is_alias <- FALSE
  x
}, keep = 6)

.muni_dataset <- function(year, cache) .muni_vintage(.best_year(year, .MUNI_YEARS), cache)

# The identifiers of a municipal vintage, cached to disk (shared with Python).
.muni_id_set <- function(year, cache) .memo(paste0("ids", year, cache), function() {
  path <- file.path(cache, sprintf("ids_muni_%d.txt", year))
  if (file.exists(path)) {
    ids <- tryCatch(scan(path, what = "", quiet = TRUE), error = function(e) NULL)
    if (!is.null(ids)) return(ids)
  }
  ids <- sort(unique(.muni_dataset(year, cache)$gisco_id), method = "radix")
  tryCatch({
    dir.create(cache, recursive = TRUE, showWarnings = FALSE)
    writeLines(ids, path, useBytes = TRUE)
  }, error = function(e) warning("Could not cache ID list for vintage ", year, call. = FALSE))
  ids
})

.nuts_dataset <- function(year, level, cache) {
  y <- .best_year(year, .NUTS_YEARS)
  .memo(paste0("nuts", y, "_", level, cache), function() {
    fname <- sprintf("NUTS_RG_01M_%d_3035_LEVL_%d.geojson", y, level)
    x <- .fetch_geojson(paste0(.GISCO_NUTS, "geojson/", fname), cache, fname)
    .slim(x, if ("NUTS_ID" %in% names(x)) "NUTS_ID" else "gisco_id")
  }, keep = 16)
}

.countries_dataset <- function(year, cache) {
  y <- .best_year(year, .CNTR_YEARS)
  .memo(paste0("cntr", y, cache), function() {
    fname <- sprintf("CNTR_RG_20M_%d_4326.geojson", y)
    x <- .fetch_geojson(paste0(.GISCO_CNTR, "geojson/", fname), cache, fname)
    col <- intersect(c("CNTR_ID", "CNTR_CODE"), names(x))[1]
    x <- .slim(x, col)
    if (anyDuplicated(x$gisco_id)) .dissolve_by_id(x) else x
  }, keep = 4)
}

.read_maps_shp <- function(path) .memo(paste0("maps", path), function() {
  x <- .to_3035(sf::st_read(path, quiet = TRUE))
  if (!("gisco_id" %in% names(x)) && "GISCO_ID" %in% names(x))
    names(x)[names(x) == "GISCO_ID"] <- "gisco_id"
  x[, "gisco_id"]
}, keep = 32)

.geom_of <- function(x, id) {
  i <- match(id, x$gisco_id)
  if (is.na(i)) NULL else sf::st_geometry(x)[[i]]
}

# --- maps/ files ----------------------------------------------------------------

.year_of <- function(p) as.integer(sub("^.*_([0-9]{4})\\.shp$", "\\1", basename(p)))

.maps_national <- function(maps, cc, year, cache = cache_dir()) {
  if (!is.null(year)) {
    cand <- .derived_glob(maps, cache, sprintf("%s_[0-9][0-9][0-9][0-9].shp", cc))
    cand <- cand[.year_of(cand) <= year]
    if (length(cand)) return(cand[length(cand)])
  }
  p <- .maps_path(maps, cache, paste0(cc, ".shp"))
  if (file.exists(p)) p else NULL
}

.maps_eu <- function(maps, cc, year, cache = cache_dir()) {
  cand <- .derived_glob(maps, cache, sprintf("EU_%s_*.shp", cc))
  if (!length(cand)) return(NULL)
  ok <- cand[!is.na(.year_of(cand)) & .year_of(cand) <= year]
  if (length(ok)) ok[length(ok)] else cand[1]
}

.special_parents <- function() .memo("special_parents", function() {
  d <- tryCatch(dist_dir(), error = function(e) NULL)
  path <- if (!is.null(d)) file.path(d, "geo", "special.csv") else NULL
  if (is.null(path) || !file.exists(path)) return(character())
  s <- .res_csv(path)
  ok <- nzchar(trimws(s$gisco_id)) & nzchar(trimws(s$parent))
  p <- stats::setNames(trimws(s$parent[ok]), trimws(s$gisco_id[ok]))
  p[!duplicated(names(p), fromLast = TRUE)]
})

# --- the resolver ---------------------------------------------------------------

#' Geometry resolver for BLUE_DB units
#'
#' Methods, called with `$` as in the Python package:
#'
#' * `geo$geometries(gisco_ids, year, election_type, country_code)` -- an `sf`
#'   data frame (EPSG:3035) with one row per resolved unit.
#' * `geo$resolve_sources(gisco_ids, year, election_type, country_code)` -- the
#'   polygon each unit is drawn with, as a data frame (`gisco_id`, `kind`,
#'   `layer`, `source_id`, `ref`; `NA` where unresolved).
#' * `geo$geometry_of(ref)` -- the polygon a [source_ref()] points at.
#' * `geo$all_geometries(year, election_type, countries)` -- every unit of a
#'   boundary vintage, e.g. as a background layer.
#' * `geo$attach(results, year, election_type, continental)` -- a
#'   `db$results()` frame as a plottable `sf` object, each election at its
#'   own boundary vintage.
#'
#' GISCO layers are downloaded on first use and cached (see [cache_dir()]).
#'
#' @param maps_dir Path to the `maps/` folder (located automatically).
#' @param cache_dir Download cache (located automatically).
#' @return An object of class `BlueGeo`.
#' @examples
#' \dontrun{
#' db <- BlueDB(); geo <- BlueGeo()
#' res <- db$results(countries = "PT", election_type = "national", years = 2022)
#' m <- geo$attach(db$vote_shares(res))
#' plot(m["PS_pct"], border = NA)
#' }
#' @export
BlueGeo <- function(maps_dir = NULL, cache_dir = NULL) {
  .need_sf()
  self <- new.env(parent = emptyenv())
  self$maps_dir <- bluedb::maps_dir(maps_dir)
  self$cache_dir <- bluedb::cache_dir(cache_dir)
  maps <- self$maps_dir
  cache <- self$cache_dir

  self$resolve_sources <- function(gisco_ids, year, election_type = "national",
                                   country_code = NULL) {
    ids <- unique(as.character(gisco_ids))
    year <- as.integer(year)
    if (!length(ids)) return(.refs_frame(character(), list()))
    if (is.null(country_code)) {
      cands <- unique(substr(ids[nchar(ids) >= 2], 1, 2))
      country_code <- if (length(cands) == 1) cands else NULL
    }
    resolved <- list()
    remaining <- ids

    for (gid in remaining) {                          # 0. overrides
      ref <- geo_override(gid, year, election_type)
      if (!is.null(ref)) resolved[[gid]] <- ref
    }
    remaining <- setdiff(remaining, names(resolved))

    if (length(remaining))                            # 1. maps/
      remaining <- from_maps(remaining, resolved, election_type, country_code, year)
    if (length(remaining))                            # 2. GISCO municipalities
      remaining <- from_lau(remaining, resolved, effective_vintage(country_code, year))
    if (length(remaining) && !is.null(country_code)) { # 2b. another territory's units
      other <- remaining[nchar(remaining) > 3 & substr(remaining, 3, 3) == "_" &
                         substr(remaining, 1, 2) != country_code]
      for (cc in unique(substr(other, 1, 2))) {
        sub <- self$resolve_sources(other[substr(other, 1, 2) == cc], year, election_type, cc)
        for (i in which(!is.na(sub$kind)))
          resolved[[sub$gisco_id[i]]] <- source_ref(sub$kind[i], sub$layer[i], sub$source_id[i])
        remaining <- setdiff(remaining, sub$gisco_id[!is.na(sub$kind)])
      }
    }
    if (length(remaining))                            # 3. GISCO NUTS
      remaining <- from_nuts(remaining, resolved, year)
    if (length(remaining)) {                          # 4. unions of countries
      vintage <- .best_year(year, .CNTR_YEARS)
      for (gid in remaining) {
        members <- foreign_members(gid, ids)
        if (length(members)) {
          resolved[[gid]] <- source_ref("cntrset", as.character(vintage), paste(members, collapse = "+"))
          remaining <- setdiff(remaining, gid)
        }
      }
    }
    .refs_frame(ids, resolved)
  }

  # `resolved` is updated in the caller's frame (the Python dict is shared).
  from_maps <- function(remaining, resolved, election_type, country_code, year) {
    env <- parent.frame()
    ccs <- if (!is.null(country_code)) country_code else {
      pre <- sub("_.*$", "", remaining[grepl("_", remaining, fixed = TRUE)])
      sort(unique(pre[nchar(pre) == 2]), method = "radix")
    }
    for (cc in ccs) {
      if (!length(remaining)) break
      path <- if (election_type == "european")
        .maps_eu(maps, cc, year, cache) %||% .maps_national(maps, cc, year, cache)
      else .maps_national(maps, cc, year, cache)
      if (is.null(path)) next
      hit <- intersect(.read_maps_shp(path)$gisco_id, remaining)
      for (gid in hit) env$resolved[[gid]] <- source_ref("maps", basename(path), gid)
      remaining <- setdiff(remaining, hit)
    }
    remaining
  }

  from_lau <- function(remaining, resolved, year) {
    env <- parent.frame()
    vintages <- year
    if (.best_year(year, .MUNI_YEARS) %in% .COMM_YEARS) vintages <- c(vintages, .LAU_YEARS[1])
    rest <- setdiff(.MUNI_YEARS, vintages)
    rest <- rest[order(abs(rest - year), -rest)]
    vintages <- c(vintages, utils::head(rest, .MAX_FALLBACK_VINTAGES))
    alt <- vapply(remaining, .normalize_lau_id, character(1), USE.NAMES = FALSE)
    names(alt) <- remaining
    still <- remaining
    for (n in seq_along(vintages)) {
      y <- vintages[n]
      if (!length(still)) break
      if (n == 2) {
        own <- length(remaining) - length(still)
        if (own >= .FALLBACK_THRESHOLD * length(remaining)) break
      }
      if (abs(y - year) > .MAX_VINTAGE_DISTANCE && !(y %in% c(year, .LAU_YEARS[1]))) next
      index <- tryCatch(.muni_dataset(y, cache)$gisco_id, error = function(e) {
        warning("Could not fetch LAU dataset ", y, ": ", conditionMessage(e), call. = FALSE)
        NULL
      })
      if (is.null(index)) next
      still <- lau_hits(env, still, alt, index, y)
    }
    # The long tail: every vintage's ID list, nearest first.
    if (length(still)) {
      # nearest first: each unit takes the first vintage that has it
      for (y in .MUNI_YEARS[order(abs(.MUNI_YEARS - year), -.MUNI_YEARS)]) {
        if (!length(still)) break
        ids <- tryCatch(.muni_id_set(y, cache), error = function(e) NULL)
        if (!is.null(ids)) still <- lau_hits(env, still, alt, ids, y)
      }
    }
    still
  }

  # Resolve the IDs (or their normalised form) found in a vintage's `index`.
  lau_hits <- function(env, still, alt, index, y) {
    direct <- still %in% index
    a <- alt[still]
    via <- !direct & !is.na(a) & a %in% index
    for (i in which(direct)) env$resolved[[still[i]]] <- source_ref("lau", as.character(y), still[i])
    for (i in which(via)) env$resolved[[still[i]]] <- source_ref("lau", as.character(y), a[[i]])
    still[!(direct | via)]
  }

  from_nuts <- function(remaining, resolved, year) {
    env <- parent.frame()
    for (level in 3:0) {
      if (!length(remaining)) break
      ids <- tryCatch(.nuts_dataset(year, level, cache)$gisco_id, error = function(e) {
        warning("Could not fetch NUTS", level, " dataset: ", conditionMessage(e), call. = FALSE)
        NULL
      })
      hit <- intersect(ids, remaining)
      for (gid in hit) env$resolved[[gid]] <- source_ref("nuts", paste0(year, ":", level), gid)
      remaining <- setdiff(remaining, hit)
    }
    # Voters abroad: the host country's boundary, under either spelling, from
    # older vintages if need be, then from the world country layer.
    if (length(remaining)) {
      hosts <- vapply(remaining, abroad_host, character(1))
      hosts <- hosts[!is.na(hosts)]
      aliases <- lapply(stats::setNames(nm = unique(hosts)), function(h)
        unique(c(h, .HISTORIC_COUNTRIES[[h]], names(.ISO_TO_NUTS)[.ISO_TO_NUTS == h])))
      try_layer <- function(vintages, load, make_ref) {
        for (v in vintages) {
          if (!length(hosts)) break
          index <- tryCatch(load(v), error = function(e) NULL)
          if (is.null(index)) next
          for (gid in names(hosts)) {
            code <- intersect(aliases[[hosts[[gid]]]], index)[1]
            if (!is.na(code)) {
              env$resolved[[gid]] <- make_ref(v, code)
              remaining <<- setdiff(remaining, gid)
              hosts <<- hosts[names(hosts) != gid]
            }
          }
        }
      }
      try_layer(c(year, sort(.NUTS_YEARS, decreasing = TRUE)),
                function(v) .nuts_dataset(v, 0, cache)$gisco_id,
                function(v, code) source_ref("nuts", paste0(v, ":0"), code))
      try_layer(c(.best_year(year, .CNTR_YEARS), sort(.CNTR_YEARS, decreasing = TRUE)),
                function(v) .countries_dataset(v, cache)$gisco_id,
                function(v, code) source_ref("cntr", as.character(v), code))
    }
    # Last resort: a special unit alone in its NUTS region is drawn as it.
    if (length(remaining)) {
      sp <- .special_parents()
      wanted <- sp[remaining]
      wanted <- wanted[!is.na(wanted)]
      all_parents <- sp[union(names(env$resolved), remaining)]
      counts <- table(all_parents[!is.na(all_parents)])
      singles <- wanted[counts[wanted] == 1]
      if (length(singles)) {
        levels <- if (length(env$resolved)) 3:2 else 3:0
        vintages <- c(year, sort(.NUTS_YEARS, decreasing = TRUE))
        for (level in levels) for (v in vintages) {
          if (!length(singles)) break
          index <- tryCatch(.nuts_dataset(v, level, cache)$gisco_id, error = function(e) NULL)
          if (is.null(index)) next
          for (gid in names(singles)) {
            p <- singles[[gid]]
            codes <- unique(c(p, substr(p, 1, 4), substr(p, 1, 3), substr(p, 1, 2)))
            code <- intersect(codes[nzchar(codes)], index)[1]
            if (!is.na(code)) {
              env$resolved[[gid]] <- source_ref("nuts", paste0(v, ":", level), code)
              remaining <- setdiff(remaining, gid)
              singles <- singles[names(singles) != gid]
            }
          }
        }
      }
    }
    remaining
  }

  # The layer a reference's kind and layer name point at.
  layer_of <- function(kind, layer) {
    switch(sub("set$", "", kind),
      maps = { p <- .maps_path(maps, cache, layer); if (file.exists(p)) .read_maps_shp(p) else NULL },
      lau  = .muni_dataset(as.integer(layer), cache),
      nuts = { vl <- as.integer(strsplit(layer, ":", fixed = TRUE)[[1]]); .nuts_dataset(vl[1], vl[2], cache) },
      cntr = .countries_dataset(as.integer(layer), cache),
      NULL)
  }

  self$geometry_of <- function(ref) {
    if (is.character(ref)) ref <- parse_source_ref(ref)
    ids <- strsplit(ref$source_id, "+", fixed = TRUE)[[1]]
    tryCatch({
      switch(ref$kind,
        maps = , mapsset = {
          path <- .maps_path(maps, cache, ref$layer)
          if (!file.exists(path)) return(NULL)
          x <- .read_maps_shp(path)
          if (ref$kind == "maps") .geom_of(x, ref$source_id)
          else .union(as.list(sf::st_geometry(x)[x$gisco_id %in% ids]))
        },
        lau = .geom_of(.muni_dataset(as.integer(ref$layer), cache), ref$source_id),
        lauset = {
          x <- .muni_dataset(as.integer(ref$layer), cache)
          .union(lapply(unique(ids), function(i) .geom_of(x, i)))
        },
        nuts = , nutsset = {
          vl <- as.integer(strsplit(ref$layer, ":", fixed = TRUE)[[1]])
          x <- .nuts_dataset(vl[1], vl[2], cache)
          if (ref$kind == "nuts") .geom_of(x, ref$source_id)
          else .union(as.list(sf::st_geometry(x)[x$gisco_id %in% ids]))
        },
        cntr = , cntrset = {
          x <- .countries_dataset(as.integer(ref$layer), cache)
          if (ref$kind == "cntr") .geom_of(x, ref$source_id)
          else .union(as.list(sf::st_geometry(x)[x$gisco_id %in% ids]))
        },
        NULL)
    }, error = function(e) {
      warning("Could not load geometry for ", format(ref), ": ", conditionMessage(e), call. = FALSE)
      NULL
    })
  }

  self$geometries <- function(gisco_ids, year, election_type = "national", country_code = NULL) {
    refs <- self$resolve_sources(gisco_ids, year, election_type, country_code)
    missing <- refs$gisco_id[is.na(refs$kind)]
    if (length(missing))
      warning("Could not find geometry for ", length(missing), " gisco_id(s): ",
              paste(utils::head(sort(missing, method = "radix"), 10), collapse = ", "),
              call. = FALSE)
    found <- refs[!is.na(refs$kind), , drop = FALSE]
    geoms <- vector("list", nrow(found))
    # single features of one layer are looked up together, sets one by one
    batch <- found$kind %in% c("maps", "lau", "nuts", "cntr")
    for (lk in unique(paste(found$kind, found$layer)[batch])) {
      rows <- which(batch & paste(found$kind, found$layer) == lk)
      layer <- tryCatch(layer_of(found$kind[rows[1]], found$layer[rows[1]]), error = function(e) {
        warning("Could not load ", lk, ": ", conditionMessage(e), call. = FALSE)
        NULL
      })
      if (is.null(layer)) next
      idx <- match(found$source_id[rows], layer$gisco_id)
      g <- sf::st_geometry(layer)
      geoms[rows] <- lapply(idx, function(i) if (is.na(i)) NULL else g[[i]])
    }
    for (i in which(!batch))
      geoms[i] <- list(self$geometry_of(source_ref(found$kind[i], found$layer[i], found$source_id[i])))
    ok <- !vapply(geoms, is.null, logical(1))
    sf::st_sf(gisco_id = found$gisco_id[ok],
              geometry = sf::st_sfc(geoms[ok], crs = 3035))
  }

  self$all_geometries <- function(year, election_type = "national", countries = NULL) {
    keep_cc <- function(cc) is.null(countries) || cc %in% countries
    best <- character()                               # cc -> shapefile
    if (election_type == "european") {
      for (shp in .derived_glob(maps, cache, "EU_??_*.shp")) {
        m <- regmatches(basename(shp), regexec("^EU_([A-Z]{2})_([0-9]{4})\\.shp$", basename(shp)))[[1]]
        if (!length(m) || !keep_cc(m[2])) next
        if (as.integer(m[3]) <= year) best[m[2]] <- shp
      }
    } else {
      for (shp in .derived_glob(maps, cache, "[A-Z][A-Z]_[0-9][0-9][0-9][0-9].shp")) {
        m <- regmatches(basename(shp), regexec("^([A-Z]{2})_([0-9]{4})\\.shp$", basename(shp)))[[1]]
        if (!length(m) || !keep_cc(m[2])) next
        if (as.integer(m[3]) <= year) best[m[2]] <- shp
      }
      for (shp in .derived_glob(maps, cache, "[A-Z][A-Z].shp")) {
        cc <- sub("\\.shp$", "", basename(shp))
        if (keep_cc(cc) && is.na(best[cc])) best[cc] <- shp
      }
    }
    parts <- list()
    for (cc in sort(names(best), method = "radix")) {
      x <- .read_maps_shp(best[[cc]])
      x$country_code <- cc
      parts[[length(parts) + 1]] <- x[, c("gisco_id", "country_code")]
    }
    lau <- tryCatch(.muni_dataset(year, cache), error = function(e) {
      warning("Could not fetch LAU dataset: ", conditionMessage(e), call. = FALSE)
      NULL
    })
    if (!is.null(lau) && nrow(lau)) {
      lau <- lau[!lau$is_alias, ]
      lau$country_code <- sub("_.*$", "", lau$gisco_id)
      lau <- lau[!(lau$country_code %in% names(best)) & keep_cc(lau$country_code), ]
      parts[[length(parts) + 1]] <- lau[, c("gisco_id", "country_code")]
    }
    if (!length(parts))
      return(sf::st_sf(gisco_id = character(), country_code = character(),
                       geometry = sf::st_sfc(crs = 3035)))
    parts <- lapply(parts, function(p) { sf::st_geometry(p) <- "geometry"; p })
    do.call(rbind, parts)
  }

  self$attach <- function(results, year = NULL, election_type = NULL, continental = TRUE) {
    df <- as.data.frame(results)
    rownames(df) <- NULL
    id_col <- if ("geo_id" %in% names(df)) "geo_id" else "gisco_id"
    if (!(id_col %in% names(df))) stop("results must have a 'geo_id' or 'gisco_id' column")
    keys <- c(if (is.null(year) && "year" %in% names(df)) "year",
              if (is.null(election_type) && "election_type" %in% names(df)) "election_type",
              if ("country_code" %in% names(df)) "country_code")
    grp <- if (length(keys)) .key(df, keys) else rep("", nrow(df))
    geometry <- vector("list", nrow(df))
    for (g in unique(grp)) {                    # one vintage per election group
      rows <- which(grp == g)
      first <- df[rows[1], , drop = FALSE]
      yr <- if ("year" %in% keys) first$year else year
      et <- if ("election_type" %in% keys) first$election_type else (election_type %||% "national")
      cc <- if ("country_code" %in% keys) first$country_code else NULL
      ids <- as.character(df[[id_col]][rows])
      gg <- self$geometries(ids, year = as.integer(yr), election_type = et, country_code = cc)
      geometry[rows] <- as.list(sf::st_geometry(gg))[match(ids, gg$gisco_id)]
    }
    ok <- !vapply(geometry, is.null, logical(1))
    if (any(!ok))
      warning("attach: could not resolve geometry for ", sum(!ok), " of ", nrow(df), " rows",
              call. = FALSE)
    out <- sf::st_sf(df[ok, , drop = FALSE], geometry = sf::st_sfc(geometry[ok], crs = 3035))
    if (continental && nrow(out)) {
      b <- .CONTINENTAL_BBOX
      xy <- sf::st_coordinates(suppressWarnings(sf::st_centroid(sf::st_geometry(out))))
      out <- out[xy[, 1] >= b[1] & xy[, 1] <= b[3] & xy[, 2] >= b[2] & xy[, 2] <= b[4], ]
    }
    rownames(out) <- NULL
    out
  }

  class(self) <- "BlueGeo"
  self
}

.refs_frame <- function(ids, resolved) {
  get <- function(field) vapply(ids, function(g) {
    r <- resolved[[g]]
    if (is.null(r)) NA_character_ else as.character(r[[field]])
  }, character(1), USE.NAMES = FALSE)
  out <- data.frame(gisco_id = ids, kind = get("kind"), layer = get("layer"),
                    source_id = get("source_id"), stringsAsFactors = FALSE)
  out$ref <- ifelse(is.na(out$kind), NA_character_,
                    paste0(out$kind, ":", out$layer, "#", out$source_id))
  out
}

#' @export
print.BlueGeo <- function(x, ...) {
  cat("<BlueGeo> maps:", x$maps_dir, " cache:", x$cache_dir, "\n")
  invisible(x)
}
