SELECT
    source,
    count(*) FILTER (WHERE monthly_admin_fee_pln IS NOT NULL) AS offers_with_fee,
    round(avg(monthly_admin_fee_pln), 2) AS average_admin_fee_pln,
    min(monthly_admin_fee_pln) AS minimum_admin_fee_pln,
    max(monthly_admin_fee_pln) AS maximum_admin_fee_pln
FROM analytics.latest_offers
GROUP BY source
ORDER BY source;
