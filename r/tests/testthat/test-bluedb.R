test_that("the election index is typed and filterable", {
  db <- test_db()
  idx <- db$index
  expect_true(all(c("country_code", "election_date", "year", "election_type",
                    "file_path", "vote_type", "round") %in% names(idx)))
  expect_s3_class(idx$election_date, "Date")
  expect_setequal(unique(idx$election_type), c("national", "european"))
  fr <- db$elections(countries = "FR", election_type = "national", round = 1)
  expect_true(nrow(fr) > 0 && all(fr$country_code == "FR" & fr$round == 1))
  expect_true(all(db$elections(vote_type = "Erststimme")$country_code == "DE"))
})

test_that("national and EP contests on the same day are kept apart", {
  db <- test_db()
  be <- db$results(countries = "BE", years = 2014, geo_level = "NUTS0")
  expect_setequal(be$election_type, c("national", "european"))
})

test_that("results carry metadata first and add up across levels", {
  db <- test_db()
  lau <- db$results(countries = "LI", years = 2025)
  expect_equal(names(lau)[1:7], c("country_code", "election_date", "year",
                                  "election_type", "geo_id", "geo_name", "geo_level"))
  expect_true(all(lau$geo_level == "LAU"))
  n0 <- db$results(countries = "LI", years = 2025, geo_level = "NUTS0")
  expect_equal(nrow(n0), 1L)
  expect_equal(n0$geo_id, "LI")
  parties <- setdiff(names(n0), c(names(n0)[1:7], "registered", "turnout", "invalid", "blank"))
  expect_equal(sum(n0[parties]), sum(lau[parties]))
})

test_that("party columns collapse into groups", {
  db <- test_db()
  raw <- db$results(countries = "AT", years = 2024, geo_level = "NUTS0")
  ideo <- db$results(countries = "AT", years = 2024, geo_level = "NUTS0", aggregate_by = "ideology")
  meta <- c(names(raw)[1:7], "registered", "turnout", "invalid", "blank")
  expect_equal(sum(raw[setdiff(names(raw), meta)]), sum(ideo[setdiff(names(ideo), meta)]))
  expect_lt(ncol(ideo), ncol(raw))
})

test_that("latest_per_country keeps one contest per country", {
  db <- test_db()
  df <- db$results(election_type = "national", latest_per_country = TRUE, geo_level = "NUTS0",
                   aggregate_by = "ideology", countries = c("FR", "DE", "IT"))
  expect_equal(sort(unique(df$country_code)), c("DE", "FR", "IT"))
  expect_equal(length(unique(paste(df$country_code, df$election_date))), 3L)
})

test_that("vote shares sum to 100 and fill_forward covers every year", {
  db <- test_db()
  df <- db$results(countries = c("AT", "SE"), election_type = "national",
                   geo_level = "NUTS0", aggregate_by = "ideology")
  sh <- db$vote_shares(df)
  pct <- grep("_pct$", names(sh), value = TRUE)
  expect_equal(unname(rowSums(sh[pct], na.rm = TRUE)), rep(100, nrow(sh)))
  # values are carried forward within the requested years only
  ff <- db$fill_forward(df, 2006:2020)
  expect_equal(nrow(ff), 2L * 15L)
  expect_equal(names(ff)[1], "year")
  expect_false(anyNA(ff$turnout))
  expect_equal(ff$turnout[ff$country_code == "SE" & ff$year == 2009],
               df$turnout[df$country_code == "SE" & df$year == 2006])
})

test_that("the geographic chain reaches NUTS 0", {
  db <- test_db()
  ch <- db$geo_chain
  at <- ch[ch$gisco_id == "AT_10101", ]
  expect_equal(at$LAU, "AT_10101")
  expect_equal(at$NUTS0, "AT")
  expect_equal(at$NUTS3, "AT112")
})

# A two-election dist/: one CC BY file on disk, one external file absent.
fake_dist <- function(root) {
  dir.create(root, showWarnings = FALSE)
  dist <- file.path(root, "dist")
  dir.create(file.path(dist, "elections", "LU"), recursive = TRUE)
  writeLines(c(
    "country_code,parliament_name,election_date,round,vote_type,file_path,distribution,licence",
    "LU,Chambre,2023-10-08,,,elections/LU/2023_10.csv,included,CC-BY-4.0",
    "JE,States Assembly,2022-06-22,,,elections/JE/2022_06.csv,external,x"),
    file.path(dist, "elections", "index.csv"))
  writeLines(c("gisco_id,name,registered,turnout,invalid,blank,CSV",
               "LU_1,Unit,10,8,0,0,8"),
             file.path(dist, "elections", "LU", "2023_10.csv"))
  dist
}

test_that("external results can be left out", {
  db <- BlueDB(fake_dist(tempfile("bluedb")), external = FALSE)
  expect_equal(db$index$country_code, "LU")
})

test_that("external results are fetched with their publisher's notice", {
  root <- tempfile("bluedb")
  dir.create(root)
  fetched <- file.path(root, "2022_06.csv")
  writeLines(c("gisco_id,name,registered,turnout,invalid,blank,RJ",
               "JE_0018,District 1,10,6,0,0,6"), fetched)
  calls <- character()
  local_mocked_bindings(.fetch_external = function(file_path, cache = NULL) {
    calls <<- c(calls, file_path)
    fetched
  })
  rm(list = ls(.external_noticed), envir = .external_noticed)
  db <- BlueDB(fake_dist(root))
  expect_warning(df <- db$results(countries = "JE"), "States of Jersey")
  expect_equal(calls, "elections/JE/2022_06.csv")
  expect_equal(sum(df$RJ), 6)
})
