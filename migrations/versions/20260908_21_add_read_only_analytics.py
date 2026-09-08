"""Add a curated read-only SQL interface for offer analytics."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_21"
down_revision: str | None = "20260908_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'homez_analytics_reader'
            ) THEN
                CREATE ROLE homez_analytics_reader;
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        ALTER ROLE homez_analytics_reader
            LOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS
            CONNECTION LIMIT 2
            PASSWORD NULL
        """
    )
    op.execute(
        "ALTER ROLE homez_analytics_reader SET default_transaction_read_only = on"
    )
    op.execute("ALTER ROLE homez_analytics_reader SET statement_timeout = '30s'")
    op.execute(
        "ALTER ROLE homez_analytics_reader "
        "SET idle_in_transaction_session_timeout = '30s'"
    )
    op.execute("ALTER ROLE homez_analytics_reader SET temp_file_limit = '64MB'")
    op.execute("ALTER ROLE homez_analytics_reader SET work_mem = '4MB'")
    op.execute(
        "ALTER ROLE homez_analytics_reader SET search_path = analytics, pg_catalog"
    )

    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    op.execute("REVOKE ALL ON SCHEMA analytics FROM PUBLIC")
    op.execute(
        """
        CREATE OR REPLACE VIEW analytics.latest_offers
        WITH (security_barrier = true)
        AS
        WITH latest_evaluation AS (
            SELECT DISTINCT ON (evaluation.listing_id)
                evaluation.*
            FROM candidate_match_evaluations AS evaluation
            ORDER BY
                evaluation.listing_id,
                evaluation.evaluated_at DESC,
                evaluation.id DESC
        ),
        latest_fact_set AS (
            SELECT DISTINCT ON (fact_set.listing_id)
                fact_set.*
            FROM candidate_fact_sets AS fact_set
            ORDER BY
                fact_set.listing_id,
                fact_set.created_at DESC,
                fact_set.id DESC
        ),
        offer_data AS (
            SELECT
                fact_set.listing_id,
                fact_set.candidate_id,
                evaluation.evaluated_at,
                evaluation.eligible,
                evaluation.score,
                evaluation.confidence,
                evaluation.buyer_profile_version,
                evaluation.matcher_version,
                fact_set.facts_json::jsonb AS normalized_facts,
                evaluation.facts_json::jsonb AS evaluated_facts_json,
                evaluation.explanation_json::jsonb AS explanation_jsonb,
                listing.source_listing_id,
                listing.canonical_url,
                listing.title AS listing_title,
                source.key AS source_key,
                snapshot.observed_at,
                snapshot.price_minor,
                snapshot.currency,
                snapshot.area_sqm AS snapshot_area_sqm,
                snapshot.rooms AS snapshot_rooms,
                snapshot.availability AS snapshot_availability,
                snapshot.location AS snapshot_location,
                snapshot.description AS snapshot_description
            FROM latest_fact_set AS fact_set
            JOIN listings AS listing
                ON listing.id = fact_set.listing_id
            JOIN sources AS source
                ON source.id = listing.source_id
            JOIN listing_snapshots AS snapshot
                ON snapshot.id = fact_set.snapshot_id
            LEFT JOIN latest_evaluation AS evaluation
                ON evaluation.listing_id = fact_set.listing_id
        )
        SELECT
            offer.listing_id,
            offer.candidate_id,
            offer.source_key AS source,
            offer.source_listing_id,
            offer.canonical_url,
            COALESCE(
                NULLIF(offer.normalized_facts ->> 'title', ''),
                offer.listing_title
            ) AS title,
            offer.observed_at,
            offer.evaluated_at,
            CASE
                WHEN offer.normalized_facts ->> 'purchase_price_minor'
                    ~ '^[0-9]+$'
                THEN (offer.normalized_facts ->> 'purchase_price_minor')::numeric
                    / 100
                ELSE offer.price_minor::numeric / 100
            END AS purchase_price_pln,
            COALESCE(
                NULLIF(offer.normalized_facts ->> 'currency', ''),
                offer.currency
            ) AS currency,
            CASE
                WHEN offer.normalized_facts ->> 'area_sqm'
                    ~ '^[0-9]+([.][0-9]+)?$'
                THEN (offer.normalized_facts ->> 'area_sqm')::numeric
                ELSE offer.snapshot_area_sqm
            END AS area_sqm,
            CASE
                WHEN offer.normalized_facts ->> 'rooms' ~ '^[0-9]+$'
                THEN (offer.normalized_facts ->> 'rooms')::integer
                ELSE offer.snapshot_rooms
            END AS rooms,
            COALESCE(
                NULLIF(offer.normalized_facts ->> 'availability', ''),
                offer.snapshot_availability
            ) AS availability,
            COALESCE(
                NULLIF(offer.normalized_facts ->> 'locality', ''),
                offer.snapshot_location
            ) AS location,
            COALESCE(
                NULLIF(offer.normalized_facts ->> 'description', ''),
                offer.snapshot_description
            ) AS description,
            CASE
                WHEN COALESCE(
                    offer.evaluated_facts_json ->> 'monthly_admin_fee_minor',
                    offer.normalized_facts ->> 'monthly_admin_fee_minor'
                ) ~ '^[0-9]+$'
                THEN COALESCE(
                    offer.evaluated_facts_json ->> 'monthly_admin_fee_minor',
                    offer.normalized_facts ->> 'monthly_admin_fee_minor'
                )::numeric / 100
                ELSE NULL
            END AS monthly_admin_fee_pln,
            COALESCE(
                NULLIF(offer.evaluated_facts_json ->> 'heating_type', ''),
                NULLIF(offer.normalized_facts ->> 'heating_type', '')
            ) AS heating_type,
            CASE COALESCE(
                offer.evaluated_facts_json ->> 'admin_fee_includes_heating',
                offer.normalized_facts ->> 'admin_fee_includes_heating'
            )
                WHEN 'true' THEN true
                WHEN 'false' THEN false
                ELSE NULL
            END AS admin_fee_includes_heating,
            offer.eligible,
            offer.score,
            offer.confidence,
            offer.buyer_profile_version,
            offer.matcher_version,
            feedback.value AS feedback_value,
            feedback.reason_code AS feedback_reason,
            feedback.comment AS feedback_comment,
            feedback.recorded_at AS feedback_recorded_at,
            offer.normalized_facts AS facts,
            offer.evaluated_facts_json AS evaluated_facts,
            offer.explanation_jsonb AS explanation
        FROM offer_data AS offer
        LEFT JOIN LATERAL (
            SELECT
                event.value,
                event.reason_code,
                event.comment,
                event.recorded_at
            FROM feedback_events AS event
            WHERE event.listing_id = offer.listing_id::text
            ORDER BY event.recorded_at DESC, event.id DESC
            LIMIT 1
        ) AS feedback ON true
        """
    )
    op.execute(
        "COMMENT ON VIEW analytics.latest_offers IS "
        "'Latest evaluated offer facts and latest user feedback; no secrets or tokens.'"
    )
    op.execute("GRANT USAGE ON SCHEMA analytics TO homez_analytics_reader")
    op.execute("GRANT SELECT ON analytics.latest_offers TO homez_analytics_reader")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("REVOKE SELECT ON analytics.latest_offers FROM homez_analytics_reader")
    op.execute("REVOKE USAGE ON SCHEMA analytics FROM homez_analytics_reader")
    op.execute("DROP VIEW IF EXISTS analytics.latest_offers")
    op.execute("DROP SCHEMA IF EXISTS analytics")
    op.execute("DROP ROLE IF EXISTS homez_analytics_reader")
