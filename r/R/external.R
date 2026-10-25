# Election results that are not part of the Zenodo record (port of
# blue_db/_external.py).
#
# The Jersey and Guernsey results are reproduced under their publishers' terms,
# which restrict reuse to research and non-commercial purposes and so cannot be
# released under the dataset's CC BY 4.0 licence.  They are kept in the GitHub
# repository instead, and BlueDB() downloads them from there the first time
# they are requested, pinned to the release tag, like the GISCO boundaries are
# downloaded on demand by BlueGeo().  The notices are kept in sync with
# `external` in blue_db_meta.json.

.EXTERNAL_REF <- "v2026.2"
.EXTERNAL_BASE_URL <- "https://raw.githubusercontent.com/blue-electoral-bulletin/BLUE_DB/{ref}/dist/"

.EXTERNAL <- list(
  JE = list(
    name = "Jersey",
    licence = "\u00a9 States of Jersey; research and non-commercial use",
    notice = paste(
      "Jersey election results \u00a9 States of Jersey, compiled from secondary",
      "sources and checked against the official results published by the States",
      "Greffe (https://www.vote.je).",
      "Reuse is permitted for research, reporting, and personal or",
      "non-commercial purposes only; any other reuse requires the permission",
      "of the States Greffe (contact@vote.je). These files are not covered by",
      "BLUE_DB's CC BY 4.0 licence or by the MIT License."),
    terms_url = "https://www.vote.je/terms-and-conditions/"),
  GG = list(
    name = "Guernsey",
    licence = "\u00a9 States of Guernsey; research and private study",
    notice = paste(
      "Guernsey election results \u00a9 States of Guernsey, compiled from",
      "secondary sources and checked against the official results published on",
      "https://www.gov.gg; reproduced for research and private study,",
      "accurately and with the source and copyright",
      "acknowledged; any other reuse requires the permission of the States of",
      "Guernsey. These files are not covered by BLUE_DB's CC BY 4.0 licence or",
      "by the MIT License."),
    terms_url = "https://www.gov.gg/TsCs"))

#' @rdname dist_dir
#' @export
external_dir <- function(explicit = NULL) {
  if (!is.null(explicit)) return(path.expand(explicit))
  file.path(dirname(cache_dir()), "external")
}

.external_noticed <- new.env(parent = emptyenv())

# The publisher's terms for `cc`, once per session.
.external_notice <- function(cc) {
  info <- .EXTERNAL[[cc]]
  if (is.null(info) || isTRUE(.external_noticed[[cc]])) return(invisible())
  assign(cc, TRUE, envir = .external_noticed)
  warning(info$notice, " Terms: ", info$terms_url, call. = FALSE)
}

# A local copy of the external election file `file_path` (relative to dist/),
# downloaded from the GitHub repository at the release tag on first use.
.fetch_external <- function(file_path, cache = NULL) {
  dest <- file.path(external_dir(cache), .EXTERNAL_REF, file_path)
  if (file.exists(dest)) return(dest)
  url <- paste0(sub("{ref}", .EXTERNAL_REF, .EXTERNAL_BASE_URL, fixed = TRUE), file_path)
  .download(url, dest, 60)
  dest
}
