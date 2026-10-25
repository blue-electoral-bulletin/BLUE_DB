# Attribution

**BLUE_DB** © 2025–2026 François Hublet and Nicola Chiecchio,
<https://doi.org/10.5281/zenodo.21434111>, is licensed under the Creative
Commons Attribution 4.0 International licence (CC BY 4.0,
<https://creativecommons.org/licenses/by/4.0/>).

BLUE_DB contains data from the sources listed below. The data have been
harmonized, recoded and aggregated: parties and geographic units are recoded to
BLUE_DB's typologies, votes are aggregated by unit and party, and blank and
invalid votes are treated as described in the codebook. Inclusion of a source
does not imply that its publisher endorses BLUE_DB.

## How to attribute BLUE_DB

CC BY 4.0 lets you give credit by linking to a page that holds the full
details. This file is that page: the following line, with a link to it, is
enough.

> Data: BLUE_DB (Hublet & Chiecchio 2026), https://doi.org/10.5281/zenodo.21434111,
> CC BY 4.0, modified from official sources listed in ATTRIBUTION.md. Contains
> Parliamentary information licensed under the Open Parliament Licence v3.0 and
> public sector information licensed under the Open Government Licence.

The last sentence is required by the Open Parliament Licence and the Open
Government Licence, which ask for their own wording.

## Election results

The legal basis on which each country's results are reused is documented in
[`SOURCE_LICENCES.md`](SOURCE_LICENCES.md).

### Sources with a required attribution

| Country | Attribution |
|---|---|
| Austria | Datenquelle: Bundesministerium für Inneres, data.gv.at. CC BY 3.0 AT. Data modified. |
| Czech Republic | Zdroj: Český statistický úřad. CC BY 4.0. Data modified. |
| Denmark | Source: Statistics Denmark. CC BY 4.0. |
| Estonia | State Electoral Office of Estonia. CC BY 4.0. |
| Finland | Ministry of Justice; Source: Statistics Finland. CC BY 4.0. |
| France | Ministère de l'Intérieur, data.gouv.fr. Licence Ouverte 2.0. |
| Germany | © Die Bundeswahlleiterin, Wiesbaden 2002–2025. Datenlizenz Deutschland – Namensnennung – Version 2.0 (www.govdata.de/dl-de/by-2-0). Data modified. |
| Iceland | Statistics Iceland. CC BY 4.0. |
| Ireland | Contains Oireachtas information licensed under the Oireachtas (Open Data) PSI Licence. |
| Isle of Man | Contains public sector information licensed under the Isle of Man Open Government Licence. |
| Italy | Ministero dell'Interno, Eligendo. Open data (CC BY 4.0 by default). |
| Luxembourg | Gouvernement du Grand-Duché de Luxembourg, data.public.lu. CC BY 4.0 (2023) and CC0. |
| Romania | Autoritatea Electorală Permanentă (OGL-ROU-1.0); Biroul Electoral Central. |
| Slovenia | Državna volilna komisija. CC BY 4.0 (European Parliament datasets). |
| Spain | Ministerio del Interior, Infoelectoral. |
| Sweden | Källa: Valmyndigheten. |
| Switzerland | Source: Federal Statistical Office (FSO). |
| United Kingdom | Contains Parliamentary information licensed under the Open Parliament Licence v3.0. |
| Cyprus | Ministry of the Interior of the Republic of Cyprus. |
| Gibraltar | Gibraltar Parliament. |
| Hungary | Nemzeti Választási Iroda. |
| Lithuania | Lietuvos Respublikos vyriausioji rinkimų komisija. |
| Malta | Electoral Commission of Malta. |
| Portugal | Secretaria-Geral da Administração Interna. |
| San Marino | Segreteria di Stato per gli Affari Interni. |
| Türkiye | Yüksek Seçim Kurulu. |
| Ukraine | Central Election Commission of Ukraine. |

### Sources with no attribution requirement

Credited as good practice: the official publishers of Albania, Andorra,
Armenia, Belgium, Bosnia and Herzegovina, Bulgaria (CC0), Croatia, the Faroe
Islands, Georgia, Greece, Greenland, Kosovo, Latvia, Liechtenstein, Moldova,
Monaco, Montenegro, the Netherlands (CC0), North Macedonia, Norway, Poland,
Serbia and Slovakia.

### Jersey and Guernsey

The Jersey and Guernsey results are **not** part of the Zenodo record and are
**not** covered by CC BY 4.0. They are kept in the GitHub repository under
`dist/elections/JE/` and `dist/elections/GG/`, each with a `NOTICE.md`, and the
libraries fetch them on demand.

- Jersey: © States of Jersey. Reuse for research, reporting, and personal or
  non-commercial purposes only (<https://www.vote.je/terms-and-conditions/>).
- Guernsey: © States of Guernsey. Reproduction for research and private study
  only, accurately and with the source and copyright acknowledged
  (<https://www.gov.gg/TsCs>).

Any other reuse needs the publisher's permission.

## Other inputs

- **Geographic typology:** LAU and NUTS codes and names, source: Eurostat
  (GISCO), © European Union. Boundaries are not part of the dataset; the
  libraries download them from GISCO on demand, under Eurostat's terms
  (© EuroGeographics for the administrative boundaries).
- **European Parliament data** on members' national parties and political
  groups: © European Union, source: European Parliament.
- **Party identifiers:** Party Facts (Döring, H. & Regel, S. (2019), Party
  Politics 25(2): 97–109).
- **Party identifiers and colours:** Wikidata, CC0 1.0.
- **2019 and 2024 European Parliament results:** adapted from BLUE_EP
  (Hublet 2024, https://doi.org/10.5281/zenodo.14569325).

Boundary files shipped in the GitHub repository (`maps/`) keep their own
licences, listed in `maps/README.md`.
