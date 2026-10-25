# Boundary layers built on the user's machine rather than shipped (port of
# blue_db/_derived.py).
#
# Malta's electoral districts are unions of localities.  The locality
# boundaries come from the Malta Spatial Data Infrastructure (MSDI), whose
# licence (CC BY-NC-ND) lets them be shared unmodified but not as a derivative,
# so the repository ships the original file (maps/sources/MT/) and the
# districts are dissolved from it here, the first time they are needed, into
# the cache.  The maps/ layer name stays MT.shp, so the crosswalk in
# dist/geo/geo_lookup.csv does not change.

.MT_SOURCE <- file.path("sources", "MT", "AdministrativeUnitsAndBoundaries.gml")

# The 13 electoral districts as unions of MSDI localities (the `text` field).
# Localities that straddle a district boundary are assigned to their primary
# district.
.MT_LOCALITIES_DISTRICTS <- list(
  MT_01 = c("VALLETTA", "FLORIANA", "HAMRUN", "MARSA", "SANTA VENERA"),
  MT_02 = c("BIRGU", "ISLA", "BORMLA", "ZABBAR", "KALKARA", "XGHAJRA"),
  MT_03 = c("ZEJTUN", "GHAXAQ", "MARSASCALA", "MARSAXLOKK"),
  MT_04 = c("FGURA", "GUDJA", "PAOLA", "SANTA LUCIJA", "TARXIEN"),
  MT_05 = c("BIRZEBBUGIA", "KIRKOP", "MQABBA", "QRENDI", "SAFI", "ZURRIEQ"),
  MT_06 = c("LUQA", "QORMI", "SIGGIEWI"),
  MT_07 = c("DINGLI", "MDINA", "MTARFA", "RABAT (Malta)", "ZEBBUG (Malta)"),
  MT_08 = c("BIRKIRKARA", "IKLIN", "LIJA", "BALZAN"),
  MT_09 = c("GHARGHUR", "MSIDA", "PIETA", "SAN GWANN", "SWIEQI", "TA' XBIEX"),
  MT_10 = c("GZIRA", "PEMBROKE", "SAN GILJAN", "SLIEMA", "NAXXAR"),
  MT_11 = c("ATTARD", "MOSTA"),
  MT_12 = c("MGARR", "MELLIEHA", "SAN PAWL IL-BAHAR"),
  MT_13 = c("RABAT (Victoria)", "FONTANA", "GHAJNSIELEM", "GHARB", "GHASRI",
            "KERCEM", "MUNXAR", "NADUR", "QALA", "SAN LAWRENZ",
            "SANNAT", "XAGHRA", "XEWKIJA", "ZEBBUG (Ghawdex)"))

# Dissolve the MSDI localities in `source` into Malta's districts at `out`.
.build_mt <- function(source, out) {
  loc <- sf::st_transform(sf::st_read(source, layer = "AdministrativeUnit", quiet = TRUE), 3035)
  ids <- character(); geoms <- list()
  for (id in names(.MT_LOCALITIES_DISTRICTS)) {
    sub <- loc[loc$text %in% .MT_LOCALITIES_DISTRICTS[[id]], ]
    if (!nrow(sub)) next
    ids <- c(ids, id)
    geoms <- c(geoms, list(sf::st_union(sf::st_geometry(sub))[[1]]))
  }
  dir.create(dirname(out), recursive = TRUE, showWarnings = FALSE)
  x <- sf::st_sf(gisco_id = ids, geometry = sf::st_sfc(geoms, crs = 3035))
  sf::st_write(x, out, quiet = TRUE, delete_dsn = TRUE)
  out
}

# maps/ layer name -> list(source under maps/, builder)
.DERIVED <- list(MT.shp = list(source = .MT_SOURCE, build = .build_mt))

.derived_dir <- function(cache) file.path(dirname(cache), "derived")

# Path of the maps/ layer `name`, building a derived one if needed.  A file
# shipped in maps/ wins; a derived layer is built once from its source and
# cached.  For anything else the (possibly missing) maps/ path is returned.
.maps_path <- function(maps, cache, name) {
  path <- file.path(maps, name)
  d <- .DERIVED[[name]]
  if (file.exists(path) || is.null(d)) return(path)
  source <- file.path(maps, d$source)
  if (!file.exists(source)) return(path)
  out <- file.path(.derived_dir(cache), name)
  if (file.exists(out)) out else d$build(source, out)
}

# Sys.glob(file.path(maps, pattern)) plus the derived layers whose name matches.
.derived_glob <- function(maps, cache, pattern) {
  found <- Sys.glob(file.path(maps, pattern))
  names(found) <- basename(found)
  for (name in names(.DERIVED)) {
    if (!(name %in% names(found)) && grepl(utils::glob2rx(pattern), name) &&
        file.exists(file.path(maps, .DERIVED[[name]]$source)))
      found[name] <- .maps_path(maps, cache, name)
  }
  unname(found[sort(names(found), method = "radix")])
}
