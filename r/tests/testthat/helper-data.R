# Tests that read the dataset skip when no dist/ can be located (e.g. on CRAN
# or in CI without the Zenodo archive); set BLUE_DB_HOME to run them.
skip_without_data <- function() {
  ok <- tryCatch({ dist_dir(); TRUE }, error = function(e) FALSE)
  if (!ok) skip("BLUE_DB dist/ not found (set BLUE_DB_HOME)")
}

test_db <- function() {
  skip_without_data()
  BlueDB()
}
