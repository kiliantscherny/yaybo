# Reading the yaybo data model

For the complete column list, types and keys, read [schema.dbml](schema.dbml) beside this file — it is generated from the code, so it cannot be out of date. This file is the part that cannot be generated: what the tables mean and where they mislead.

## Contents

- Grain and keys
- Two registers, not one
- Joins
- Which tables need a login
- Columns that are computed, not recorded
- Columns that are easy to confuse
- How updates work

## Grain and keys

| table | one row per | primary key |
| --- | --- | --- |
| `ejendomme` | property | `uuid` |
| `ejere` | current owner | `ejendom_uuid, nummer` |
| `haeftelser` | charge, at a document version | `ejendom_uuid, dokument_uuid, dokument_version` |
| `servitutter` | easement, at a document version | `ejendom_uuid, dokument_uuid, dokument_version` |
| `dokument_parter` | person named on a document, in a role | `ejendom_uuid, dokument_uuid, dokumentart, rolle, nummer` |
| `underpant` | sub-pledge of a mortgage deed | `ejendom_uuid, haeftelse_uuid, rettighed_uuid` |
| `handelshistorik` | recorded sale | `ejendom_uuid, registrering_id` |
| `bygninger` | building in the BBR record | `ejendom_uuid, bygning_nr` |
| `adkomsthistorik` | past transfer | `ejendom_uuid, post_nummer` |
| `adkomsthistorik_ejere` | person in a past transfer | `ejendom_uuid, post_nummer, nummer` |
| `attester` | the property's register document | `ejendom_uuid` |
| `rentestatistik` | month × loan type of DST rates | `maaned, rentfix_kode` |
| `andele` | co-op share | `uuid` |
| `andel_haeftelser` | charge against a share, at a document version | `andel_uuid, dokument_uuid, dokument_version` |
| `andel_meddelelser` | notice noted on a share | `andel_uuid, dato_loebenummer` |

`haeftelser` is keyed on the document **version** because one document can secure more than one charge — an ejerpantebrev raised twice appears as two rows with the same `dokument_uuid`, different amounts and different priorities. Counting distinct `dokument_uuid` undercounts charges; counting rows does not.

## Two registers, not one

Tinglysning is four books. Two are here, and they disagree about what a co-op building is — correctly, in both cases.

- The **tingbog** records real property. A co-op block is **one** property, owned by the association, however many doors it has. That is `ejendomme`.
- The **andelsboligbog** records shares. The same block is **one entry per flat**. That is `andele`, joined back by `andele.ejendom_uuid`.

A share is not land, so `andele` has **no valuation, no matrikel, no registered area and no easements** — only an address, its charges and any notices. `boligareal_m2`, the coordinates and `til_salg` come from Boligsiden, not from the register.

### Who lives there

There is **no owner of record**. The andelsboligbog registers rights *over* a share, not title *to* one — who holds an andel is the association's record, and is in no register this reads. If asked who owns a co-op flat, say that, then offer the two places names do appear:

- `andel_haeftelser.kreditorer`. Most charges on a share are an **ejerpantebrev**, a deed the owner issues to *themselves* and pledges to a bank, so its creditor is in practice the andelshaver. Present this as an inference from the instrument type, never as the register naming an owner, and check `dokumenttype` before drawing it.
- `andel_meddelelser.debitorer` and `.disponenter`. A notice is noted when something has happened to the andelshaver rather than to the flat — a death, a bankruptcy, a court removing their power to dispose of it. Most shares have none, and their absence means nothing has been noted, not that nobody lives there.

Neither carries a date of birth. `ejere.foedselsdato` and `dokument_parter.foedselsdato` have no counterpart here.

Three traps, all of which produce plausible-looking wrong answers:

- **Absence proves nothing.** A share only enters the book once something is registered against it. A flat with no row in `andele` may simply have no loan against it.
- **`andele.samlet_gaeld_dkk` is not what living there owes.** It totals what is charged against that share alone. An andelshaver also owes their portion of the association's own mortgage, which sits against the *building* in `ejendomme`/`haeftelser`. Adding the two needs the association's accounts, which are not in any register.
- **There is no sale price for a share, and none is stored.** Do not reach for `ejendomme.seneste_salg_*` through the join and call it the flat's price: it is the building's own sale, the same figure for every flat in the block. What an andel may be sold for is set by the association's accounts under andelsboligloven.

Never `UNION` `ejendomme` and `andele` into one list of homes without saying which is which — the columns line up and the meanings do not.

## Joins

`ejendom_uuid` matches `ejendomme.uuid` in every table. Beyond that:

- `underpant.haeftelse_uuid` → `haeftelser.dokument_uuid`
- `dokument_parter.dokument_uuid` → `haeftelser.dokument_uuid` **or** `servitutter.dokument_uuid`. `dokumentart` says which: `haeftelse`, `servitut`, `adkomst`, `underpant`. Always filter on it when joining, or rows will multiply
- `adkomsthistorik_ejere` → `adkomsthistorik` on **both** `ejendom_uuid` and `post_nummer`
- `rentestatistik` joins to nothing. It is the rate series `laantype_estimat` was matched against, kept so an estimate can be checked
- `andel_haeftelser.andel_uuid` → `andele.uuid`. Note it is **not** `ejendom_uuid`: a share's uuid comes from a different register and never matches `ejendomme.uuid`
- `andele.ejendom_uuid` → `ejendomme.uuid`, and is empty when the lookup found no single building to attribute the share to
- `andel_meddelelser.andel_uuid` → `andele.uuid`. Keyed on `dato_loebenummer` rather than a document uuid, because the register's own view of a notice reads only the date and serial

`rolle` in `dokument_parter` is one of `kreditor`, `debitor`, `meddelelseshaver`, `fuldmagtshaver`, `adkomsthaver`, `underpanthaver`, `paataleberettiget`.

## Which tables need a login

Empty or thin without MitID:

- `dokument_parter` — everyone named on each document
- `adkomsthistorik`, `adkomsthistorik_ejere` — previous owners
- `attester` — the signed document
- `ejere.foedselsdato` — owners' dates of birth

`ejendomme.beriget` records, **per property**, whether that fetch was authenticated. A session that lapses partway through a run leaves some rows enriched and some not, so this is a property-level fact and not a run-level one.

Always check it before reporting an absence:

```sql
SELECT beriget, count(*) FROM ejendomme GROUP BY 1;
```

## Columns that are computed, not recorded

| column | what it actually is |
| --- | --- |
| `ejendomme.samlet_gaeld_dkk` | sum of the property's charges |
| `ejendomme.frivaerdi_dkk` | valuation − debt, against the **public valuation** |
| `ejendomme.belaaningsgrad_pct` | debt ÷ public valuation |
| `ejendomme.seneste_salg_pris_m2` | last sale ÷ area |
| `haeftelser.laantype_estimat` | rate matched against DST rates for the period |
| `haeftelser.laantype_afstand` | distance to the runner-up; larger = more confident |

`ejendomsvurdering_dkk` is the **public valuation**, which sits well below market. Everything derived from it inherits that: `frivaerdi_dkk` is a floor, `belaaningsgrad_pct` a ceiling. Never quote either as a market figure.

`laantype_estimat` is an inference, because the register records an interest rate and never the product. `laantype_alternativ` holds the runner-up and `laantype_kilde` the series used.

## Columns that are easy to confuse

| pair | difference |
| --- | --- |
| `areal_m2` vs `boligareal_m2` | register's tinglyste areal vs BBR living area. Price per m² normally wants the second |
| `ejendomsvurdering_dkk` vs `boligsiden_vurdering_dkk` | public valuation vs Boligsiden's estimate |
| `koebesum_dkk` vs `seneste_salg_dkk` | the register's recorded transfer sum vs Boligsiden's last sale |
| `handelshistorik` vs `adkomsthistorik` | Boligsiden's sales vs the register's transfers. They overlap and neither is a superset: the register knows transfers that were never sales, Boligsiden knows the price per m² |
| `hovedstol_dkk` vs `beloeb_dkk` | a charge's principal vs a sub-pledge's amount |
| `andele.uuid` vs `ejendomme.uuid` | different registers. They never match, and joining them returns nothing rather than erroring |
| `andele.samlet_gaeld_dkk` vs `ejendomme.samlet_gaeld_dkk` | one share's charges vs the whole association building's. Not parts of one total |
| `haeftelser` vs `andel_haeftelser` | charges on the building vs charges on one share. Same column names, different subjects |

Dates are `DATE`. Money is `BIGINT` kroner. Percentages are `DOUBLE`. Anything the register wrote in a form that could not be parsed is `NULL` rather than zero — `NULL` means "not readable or not stated", never "none".

## How updates work

Re-fetching a property **deletes and rewrites** its rows. The database holds the latest reading, not a history of readings. `hentet` is when each row was written, so staleness is per row:

```sql
SELECT adresse, hentet FROM ejendomme ORDER BY hentet LIMIT 10;
```

Every table has an enforced primary key. Two consequences:

- A row arriving without a complete key is dropped and a warning printed. It is not silent, but it is also not an error that stops the run.
- Relationships are documented, not enforced. DuckDB cannot add a foreign key to an existing table, so enforcing them would leave every database created before that change permanently unable to catch up.
