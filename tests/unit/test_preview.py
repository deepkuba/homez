from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from homefinder.domain.models import Listing, ListingSnapshot
from homefinder.preview import render_preview_card


def test_preview_escapes_untrusted_listing_content() -> None:
    listing = Listing(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        source_id=UUID("00000000-0000-0000-0000-000000000002"),
        source_listing_id="safe-id",
        canonical_url="https://listings.homez.invalid/safe-id",
        title="<script>alert(1)</script>",
    )
    snapshot = ListingSnapshot(
        id=UUID("00000000-0000-0000-0000-000000000003"),
        listing_id=listing.id,
        observed_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        price_minor=50_000_000,
        currency="PLN",
        area_sqm=Decimal("40.0"),
        rooms=2,
        availability="available",
        location="Kraków",
        description="safe",
        content_hash="hash",
    )

    html = render_preview_card(listing, snapshot)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'rel="noreferrer noopener"' in html
