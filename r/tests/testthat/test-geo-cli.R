test_that("maps/ territories resolve without any download", {
  skip_if_not_installed("sf")
  db <- test_db()
  if (!dir.exists(maps_dir())) skip("maps/ not found")
  geo <- BlueGeo()
  res <- db$results(countries = "JE", years = 2022)
  refs <- geo$resolve_sources(res$geo_id, 2022, "national", "JE")
  expect_false(anyNA(refs$kind))
  expect_true(all(refs$kind %in% c("maps", "mapsset")))
  m <- suppressWarnings(geo$attach(res))
  expect_s3_class(m, "sf")
  expect_equal(nrow(m), nrow(res))
  expect_equal(sf::st_crs(m)$epsg, 3035L)
})

test_that("the command line parses like the Python one", {
  a <- bluedb:::.parse_args(c("results", "-c", "FR", "--country", "DE", "-y", "2022",
                              "--level=NUTS2", "--by", "ideology", "--shares"))
  expect_equal(a$command, "results")
  expect_equal(a$country, c("FR", "DE"))
  expect_equal(a$year, 2022L)
  expect_equal(a$level, "NUTS2")
  expect_true(a$shares)
  expect_error(bluedb:::.parse_args(c("results", "--level", "NUTS9")))
  expect_equal(cli_main(c("--version")), 0L)
})

test_that("cli_main exports CSV", {
  skip_without_data()
  out <- tempfile(fileext = ".csv")
  expect_equal(suppressMessages(cli_main(c("results", "-c", "LI", "-y", "2025",
                                           "--level", "NUTS0", "-o", out))), 0L)
  expect_equal(nrow(utils::read.csv(out)), 1L)
})
