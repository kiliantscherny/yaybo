# Worked queries

Open read-only. Every query here runs against a database with data in it.

```bash
duckdb -readonly out/tinglysning.duckdb -c "SELECT ..."
```

## Contents

- Orientation: what is in there
- One property in full
- Comparing properties
- Mortgages and loan types
- People (needs a login)
- Buildings
- Inside the signed document
- Exporting a result

## Orientation: what is in there

```sql
SELECT 'ejendomme' AS tabel, count(*) FROM ejendomme
UNION ALL SELECT 'ejere', count(*) FROM ejere
UNION ALL SELECT 'haeftelser', count(*) FROM haeftelser
UNION ALL SELECT 'servitutter', count(*) FROM servitutter
UNION ALL SELECT 'dokument_parter', count(*) FROM dokument_parter
UNION ALL SELECT 'handelshistorik', count(*) FROM handelshistorik
UNION ALL SELECT 'bygninger', count(*) FROM bygninger
UNION ALL SELECT 'adkomsthistorik', count(*) FROM adkomsthistorik;
```

How much was fetched with a login, and how stale it is:

```sql
SELECT beriget, count(*) AS ejendomme,
       min(hentet) AS aeldste, max(hentet) AS nyeste
FROM ejendomme GROUP BY 1;
```

Which addresses are held:

```sql
SELECT adresse, lejlighed, boligareal_m2, ejendomsvurdering_dkk, hentet
FROM ejendomme ORDER BY adresse LIMIT 50;
```

## One property in full

Find the uuid first, then use it everywhere:

```sql
SELECT uuid, adresse, lejlighed, boligtype, boligareal_m2,
       ejendomsvurdering_dkk, samlet_gaeld_dkk, belaaningsgrad_pct, beriget
FROM ejendomme
WHERE adresse ILIKE '%Prøvegade 1%';
```

Its owners, charges and easements:

```sql
SELECT nummer, navn, foedselsdato, cvr, andel
FROM ejere WHERE ejendom_uuid = ? ORDER BY nummer;

SELECT prioritet, dokumenttype, hovedstol_dkk, rentetype, rentesats_pct,
       laantype_estimat, laantype_afstand, kreditorer, tinglysningsdato
FROM haeftelser WHERE ejendom_uuid = ? ORDER BY prioritet;

SELECT dato_loebenummer, dokumenttype, tekst, paataleberettigede
FROM servitutter WHERE ejendom_uuid = ? ORDER BY tinglysningsdato;
```

Everything that ever happened to it, on one timeline. The register keeps these
as separate lists, and merging them is what makes the story readable:

```sql
SELECT dato, art, beloeb, detalje FROM (
    SELECT dato, 'Salg' AS art, beloeb_dkk AS beloeb,
           handelstype AS detalje
    FROM handelshistorik WHERE ejendom_uuid = ?
    UNION ALL
    SELECT dato, 'Adkomst', koebesum_dkk, dokumenttype
    FROM adkomsthistorik WHERE ejendom_uuid = ?
    UNION ALL
    SELECT tinglysningsdato, 'Hæftelse', hovedstol_dkk, dokumenttype
    FROM haeftelser WHERE ejendom_uuid = ?
    UNION ALL
    SELECT tinglysningsdato, 'Servitut', NULL, dokumenttype
    FROM servitutter WHERE ejendom_uuid = ?
) WHERE dato IS NOT NULL
ORDER BY dato DESC;
```

## Comparing properties

Price per square metre by postcode:

```sql
SELECT regexp_extract(adresse, '(\d{4}) [^,]*$', 1) AS postnr,
       count(*) AS boliger,
       round(median(seneste_salg_pris_m2)) AS median_pris_m2,
       round(median(ejendomsvurdering_dkk)) AS median_vurdering
FROM ejendomme
WHERE seneste_salg_pris_m2 IS NOT NULL
GROUP BY 1 HAVING count(*) >= 3
ORDER BY median_pris_m2 DESC;
```

By building — the address with the flat taken off it, which is how the figures
screen groups them too:

```sql
SELECT regexp_replace(adresse, ',\s*[^,]*\.\s*[^,]*,', ',') AS bygning,
       count(*) AS boliger,
       round(avg(boligareal_m2)) AS gns_m2,
       round(median(seneste_salg_pris_m2)) AS median_pris_m2
FROM ejendomme
GROUP BY 1 HAVING count(*) > 1
ORDER BY boliger DESC;
```

Most heavily mortgaged. Quote the caveats with the numbers:

```sql
SELECT adresse,
       ejendomsvurdering_dkk,          -- public valuation, below market
       samlet_gaeld_dkk,
       belaaningsgrad_pct,             -- a CEILING, for that reason
       frivaerdi_dkk                   -- a FLOOR, for that reason
FROM ejendomme
WHERE samlet_gaeld_dkk > 0
ORDER BY belaaningsgrad_pct DESC NULLS LAST
LIMIT 20;
```

## Mortgages and loan types

Estimated loan types, with the confidence kept alongside:

```sql
SELECT laantype_estimat,
       count(*) AS antal,
       round(avg(rentesats_pct), 2) AS gns_rente,
       round(min(laantype_afstand), 3) AS taettest_paa_naeste,
       round(sum(hovedstol_dkk)) AS samlet_hovedstol
FROM haeftelser
WHERE laantype_estimat IS NOT NULL
GROUP BY 1 ORDER BY antal DESC;
```

Variable-rate charges and their terms:

```sql
SELECT e.adresse, h.hovedstol_dkk, h.reference_rente,
       h.rente_margin_pct, h.rentesats_pct, h.tinglysningsdato
FROM haeftelser h JOIN ejendomme e ON e.uuid = h.ejendom_uuid
WHERE h.rentetype ILIKE '%variabel%'
ORDER BY h.tinglysningsdato DESC;
```

Checking an estimate against the series it came from:

```sql
SELECT maaned, laantype, effektiv_rente_pct, bidrag_pct
FROM rentestatistik
WHERE maaned BETWEEN '2019M01' AND '2019M12'
ORDER BY maaned, laantype;
```

## People (needs a login)

Everyone named on one property's documents. Filter on `dokumentart` when
joining, or rows multiply:

```sql
SELECT p.dokumentart, p.rolle, p.nummer, p.navn, p.foedselsdato, p.cvr
FROM dokument_parter p
WHERE p.ejendom_uuid = ?
ORDER BY p.dokumentart, p.rolle, p.nummer;
```

Creditors by how much they are owed:

```sql
SELECT p.navn AS kreditor, p.cvr,
       count(DISTINCT p.dokument_uuid) AS dokumenter,
       round(sum(h.hovedstol_dkk)) AS samlet_hovedstol
FROM dokument_parter p
JOIN haeftelser h
  ON h.dokument_uuid = p.dokument_uuid
 AND h.ejendom_uuid = p.ejendom_uuid
WHERE p.dokumentart = 'haeftelse' AND p.rolle = 'kreditor'
GROUP BY 1, 2
ORDER BY samlet_hovedstol DESC NULLS LAST
LIMIT 20;
```

Owners holding more than one property:

```sql
SELECT navn, count(DISTINCT ejendom_uuid) AS ejendomme,
       string_agg(DISTINCT cvr, ', ') AS cvr
FROM ejere
GROUP BY 1 HAVING count(DISTINCT ejendom_uuid) > 1
ORDER BY ejendomme DESC;
```

Previous owners of one property:

```sql
SELECT a.post_nummer, a.dato, a.dokumenttype, a.koebesum_dkk, ae.navn
FROM adkomsthistorik a
LEFT JOIN adkomsthistorik_ejere ae
  ON ae.ejendom_uuid = a.ejendom_uuid AND ae.post_nummer = a.post_nummer
WHERE a.ejendom_uuid = ?
ORDER BY a.dato DESC, ae.nummer;
```

## Buildings

```sql
SELECT (opfoerelsesaar / 10)::INT * 10 AS aarti,
       varmeinstallation,
       count(*) AS bygninger,
       round(avg(boligareal_m2)) AS gns_areal
FROM bygninger
WHERE opfoerelsesaar IS NOT NULL
GROUP BY 1, 2 ORDER BY aarti DESC, bygninger DESC;
```

Property against the building it sits in:

```sql
SELECT e.adresse, e.boligareal_m2 AS bbr_m2, e.areal_m2 AS tinglyst_m2,
       b.opfoerelsesaar, b.ydervaeg, b.tagdaekning, b.varmeinstallation
FROM ejendomme e
LEFT JOIN bygninger b ON b.ejendom_uuid = e.uuid
ORDER BY b.opfoerelsesaar NULLS LAST;
```

## Inside the signed document

The JSON is the register's own OIO structure: PascalCase, and deeper than you
would guess. A wrong path returns `NULL` rather than an error, so check that a
path resolves before drawing a conclusion from it.

```sql
SELECT adresse,
       json_extract_string(dokument_json,
         '$.EjendomSummarisk.EjendomStamoplysninger.EjendomIdentifikator'
         || '.BestemtFastEjendomNummer') AS bfe,
       json_array_length(dokument_json,
         '$.EjendomSummarisk.HaeftelseSummariskSamling.HaeftelseSummarisk')
         AS haeftelser_i_dokumentet
FROM attester
LIMIT 5;
```

To explore the structure rather than guess at it:

```sql
SELECT json_keys(dokument_json, '$.EjendomSummarisk') FROM attester LIMIT 1;
```

## Exporting a result

Any of the above can go straight to Excel:

```bash
yaybo export --name laantyper --query "
  SELECT laantype_estimat, count(*) AS antal,
         round(avg(rentesats_pct), 2) AS gns_rente
  FROM haeftelser WHERE laantype_estimat IS NOT NULL
  GROUP BY 1 ORDER BY antal DESC"
```

For anything long, put the SQL in a file:

```bash
yaybo export --query-file report.sql --name rapport --format xlsx,csv
```
