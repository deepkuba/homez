from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from homefinder.parsers.contracts import DECLARED_FIELDS, PageInput
from homefinder.parsers.morizon.parser import MorizonPageParser

FIXTURE = Path(__file__).parents[1] / "fixtures/parsers/morizon/baseline.html"
NUXT_FIXTURE = Path(__file__).parents[1] / "fixtures/parsers/morizon/nuxt_offer.html"


def parse(body: bytes):
    page = PageInput(uuid4(), datetime.now(timezone.utc), body)
    result = MorizonPageParser("a" * 64).parse(page)
    assert result.capture_id == page.capture_id
    return result


def test_baseline_extracts_facts_with_provenance():
    result = parse(FIXTURE.read_bytes())
    assert result.variant == "jsonld-residence-v1"
    assert result.facts.title == "Synthetic apartment"
    assert result.facts.price_minor == 75000050
    assert result.facts.currency == "PLN"
    assert result.facts.area_sqm == "51.5"
    assert result.facts.rooms == 2
    assert result.facts.location == "Fixture Town"
    assert result.facts.description == "Synthetic description"
    assert result.facts.availability == "active"
    assert result.facts.monthly_admin_fee_minor == 50000
    assert result.facts.heating_type == "district"
    assert result.facts.admin_fee_includes_heating is True
    assert result.missing_fields == ()
    assert all(
        c.release_hash == "a" * 64
        and c.origin == "page"
        and c.locator.startswith("script[type=application/ld+json]")
        for c in result.candidates
        if c.name != "price_per_sqm_minor"
    )
    assert (
        next(c for c in result.candidates if c.name == "price_per_sqm_minor").origin
        == "derived"
    )


def test_nuxt_offer_extracts_primary_property_without_similar_listings():
    result = parse(NUXT_FIXTURE.read_bytes())

    assert result.variant == "jsonld-offer-nuxt-property-v3"
    assert result.facts.title == "Synthetic Nuxt apartment"
    assert result.facts.price_minor == 81000000
    assert result.facts.currency == "PLN"
    assert result.facts.area_sqm == "51.25"
    assert result.facts.rooms == 3
    assert result.facts.location == "Warszawa"
    assert result.facts.description == "Synthetic Nuxt description"
    assert result.facts.heating_type == "district"
    assert result.facts.price_per_sqm_minor == 1580488
    assert result.facts.monthly_admin_fee_minor is None
    assert result.facts.availability == "unknown"
    assert {candidate.name for candidate in result.candidates} == {
        "title",
        "price",
        "currency",
        "locality",
        "area",
        "rooms",
        "description",
        "heating_type",
        "price_per_sqm_minor",
    }


def test_nuxt_offer_rejects_ambiguous_primary_property():
    body = NUXT_FIXTURE.read_bytes().replace(
        b'[{"propertyData":1}', b'[{"propertyData":1},{"propertyData":2}'
    )
    assert parse(body).variant == "unknown-variant"


@pytest.mark.parametrize(
    "body",
    [
        b"<h1>Unrecognized</h1>",
        b'<script type="application/ld+json">{bad}</script>',
        FIXTURE.read_bytes() * 2,
    ],
)
def test_unknown_or_ambiguous_variant_never_invents_facts(body):
    result = parse(body)
    assert result.variant == "unknown-variant"
    assert result.candidates == ()
    assert result.missing_fields == DECLARED_FIELDS[:11]
    assert result.facts.title == ""
    assert result.facts.price_minor is None


def test_invalid_values_are_missing_without_discarding_known_title():
    result = parse(
        FIXTURE.read_bytes()
        .replace(b"750000.50", b"-1")
        .replace(b"51.5", b"NaN")
        .replace(b'"numberOfRooms":2', b'"numberOfRooms":true')
    )
    assert result.facts.title == "Synthetic apartment"
    assert result.facts.price_minor is None
    assert result.facts.area_sqm is None
    assert result.facts.rooms is None
    assert {"price", "area", "rooms"} <= set(result.missing_fields)
