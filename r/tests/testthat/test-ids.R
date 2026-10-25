test_that("abroad units name their host country", {
  expect_equal(abroad_host("BG_BGZZDE"), "DE")
  expect_equal(abroad_host("ROZZDE"), "DE")          # Romania's bare form
  expect_equal(abroad_host("PL_PLZZGR"), "EL")       # ISO -> NUTS spelling
  expect_equal(abroad_host("PL_PLZZGB"), "UK")
  expect_true(is.na(abroad_host("BG_BGZZ99")))       # pooled hosts
  expect_true(is.na(abroad_host("PT_110101")))
})

test_that("source references round-trip through their text form", {
  r <- parse_source_ref("lau:2021#BE_11001")
  expect_equal(r$kind, "lau")
  expect_equal(r$layer, "2021")
  expect_equal(r$source_id, "BE_11001")
  expect_equal(format(r), "lau:2021#BE_11001")
  expect_equal(format(parse_source_ref("nuts:2024:3#MT001")), "nuts:2024:3#MT001")
  expect_equal(parse_source_ref("nuts:2024:3#MT001")$layer, "2024:3")
})

test_that("LAU ids are normalised to GISCO's conventions", {
  expect_equal(bluedb:::.normalize_lau_id("NL_0358"), "NL_GM0358")
  expect_equal(bluedb:::.normalize_lau_id("CH_0001"), "CH_CH0001")
  expect_equal(bluedb:::.normalize_lau_id("LI_00001001"), "LI_LI00001001")
  expect_equal(bluedb:::.normalize_lau_id("RO_001017"), "RO_1017")
  expect_true(is.na(bluedb:::.normalize_lau_id("PT_110101")))
  expect_equal(bluedb:::.commune_gisco_id("CH", "CH0001", 2006), "CH_0001")
  expect_equal(bluedb:::.commune_gisco_id("GR", "GR01010101", 2006), "EL_01010101")
  expect_equal(bluedb:::.commune_gisco_id("IT", "IT001001001", 2006), "IT_001001")
  expect_equal(bluedb:::.commune_alias_id("RO", "1017"), "RO_001017")
})

test_that("Bulgarian settlement codes map to their obshtina", {
  expect_equal(geo_join_id("BG_BLG5277058"), "BG_BLG52")
  expect_equal(geo_join_id("PT_110101"), "PT_110101")
})

test_that("vote-type ranking prefers list votes and blank types", {
  expect_equal(bluedb:::.vote_type_rank(c(NA, "", "Zweitstimme", "Erststimme", "parish", "other")),
               c(0L, 0L, 1L, 9L, 9L, 5L))
})
