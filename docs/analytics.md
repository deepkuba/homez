# SQL analytics

Homez exposes parsed offer data through the PostgreSQL view
`analytics.latest_offers`. The dedicated `homez_analytics_reader` role can read
that view, but cannot read application tables or write to the database. In
particular, Gmail metadata, feedback tokens, and other application-only data are
not exposed.

PostgreSQL remains inside the private Docker network. Do not publish port 5432;
use the helper script on the VPS or tunnel the command through SSH.

## Run queries

From the Homez repository on the VPS:

```bash
scripts/homez-sql < queries/average-admin-fee.sql
```

From a laptop with an SSH host alias named `homez-vps`:

```bash
scripts/homez-sql --host homez-vps < queries/average-admin-fee.sql
```

Export the result as CSV:

```bash
scripts/homez-sql --host homez-vps --csv \
  < queries/average-admin-fee.sql > admin-fees.csv
```

## Available data

The view contains the latest normalized facts for every offer that has reached
the normalization step. Its regular columns cover source, URL, title, price,
area, room count, location, description, administrative fee, heating,
evaluation score, and latest feedback. Evaluation columns can be `NULL` while
an offer is still being processed. Monetary columns ending in `_pln` are
expressed in PLN, not grosze.

The `facts`, `evaluated_facts`, and `explanation` columns are JSONB. They make
future ad-hoc analysis possible without adding a dedicated column for every
field. For example:

```sql
SELECT
    source,
    facts ->> 'some_future_field' AS value,
    count(*)
FROM analytics.latest_offers
WHERE facts ? 'some_future_field'
GROUP BY source, value
ORDER BY source, count(*) DESC;
```

Missing values remain `NULL`. Always inspect coverage alongside an average so a
small number of successfully parsed offers is not mistaken for a representative
result:

```sql
SELECT
    count(*) AS all_offers,
    count(monthly_admin_fee_pln) AS offers_with_fee,
    round(avg(monthly_admin_fee_pln), 2) AS average_fee_pln
FROM analytics.latest_offers;
```
