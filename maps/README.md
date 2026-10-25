# maps/

Boundary files for the units that the Eurostat GISCO layers do not publish:
electoral constituencies, cantons, and the countries and territories outside
the European layers. All files are in EPSG:3035. They are rebuilt by
`make_maps.py`, `extra_geo.py`, `build_extra_crosswalk_osm.py` and
`ua_constituencies.py`. Every other unit, including Greece's *dímoi*,
Albania, North Macedonia and Serbia, is drawn from GISCO layers that the
libraries download on demand and do not redistribute.

The files do not share one licence. Each keeps the licence of its source, as
listed below. The full table, with publishers and versions, is in the [library
README](../README.md#where-the-geometries-come-from).

## OpenStreetMap-derived files: Open Database License

The following files are derived from OpenStreetMap data,
© OpenStreetMap contributors:

| File | Content |
|---|---|
| `AM.shp` | Armenian communities (admin_level 5) |
| `FO.shp` | Faroese municipalities (admin_level 7) |
| `GG.shp` | Guernsey parishes (admin_level 8) |
| `IM.shp` | Isle of Man local authorities (admin_level 8) |
| `JE.shp` | Jersey parishes (admin_level 8) and vingtaines (admin_level 9) |
| `GE.shp` | the city polygons of Batumi, Kutaisi, Poti and Rustavi (admin_level 6); the other units are public-domain geoBoundaries data |
| `UA_2006.shp` | the city districts used to divide cities between constituencies; the rest is OCHA COD-AB data (CC BY-IGO) |
| `ME.shp`, `SM.shp`, `MC.shp`, `XK.shp`, `TR.shp` | geoBoundaries layers whose source is OpenStreetMap |
| `NL.shp` | Bonaire, Saba and Sint Eustatius, via georgique/world-geojson |

These files are made available under the Open Database License,
<https://opendatacommons.org/licenses/odbl/1.0/>. Any rights in individual
contents of the database are licensed under the Database Contents License,
<https://opendatacommons.org/licenses/dbcl/1.0/>. A database derived from them
must be shared under the same licence. Maps produced from them must credit
"© OpenStreetMap contributors".

## Other files

| Licence | Files |
|---|---|
| CC BY 4.0 | `SI.shp`, `IS.shp`, `BE_2003.shp`, `BE_2024.shp`, `IE_1997/2011/2013/2017/2023.shp`, `EU_IE_*.shp`, `GI.shp` |
| CC BY 3.0 IGO / CC BY-IGO | `MD.shp`; `UA.shp`, `UA_2012.shp`, and the hromadas in `UA_2006.shp` |
| CC BY-SA 3.0 | `GL.shp` |
| Public domain | `AD.shp`, `BA.shp`, and `GE.shp` except its four cities |
| Open Government Licence v3.0 | `UK_1997/2005/2010/2024.shp` (Office for National Statistics; contains OS data © Crown copyright and database right) |
| Licence Ouverte 2.0 | `FR.shp` (Etalab) |
| Attribution required (Maa-amet) | `EE.shp` |
| CC BY-NC-ND | `sources/MT/AdministrativeUnitsAndBoundaries.gml`: Malta's localities, © Planning Authority, Malta Spatial Data Infrastructure, shipped unmodified. The licence does not allow a derivative to be shared, so the 13 electoral districts (`MT.shp`) are not shipped: the Python and R libraries dissolve them from this file on the user's machine and cache the result, and the web viewer merges them in the visitor's browser. Non-commercial use only. |
