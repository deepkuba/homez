from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from homefinder.catalog.orm import (
    Base,
    CandidateListingRecord,
    DuplicateEvidenceRecord,
    PropertyCandidateRecord,
)
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.domain.models import (
    EmailMessage,
    Listing,
    ListingSnapshot,
    ParsedAlert,
    Source,
)


def _alert(
    source_name: str,
    listing_name: str,
    *,
    price: int = 74900000,
    title: str = "Jasne 2 pokoje z balkonem",
) -> ParsedAlert:
    source = Source(uuid4(), source_name, source_name.title())
    listing = Listing(
        uuid4(),
        source.id,
        listing_name,
        f"https://{source_name}.example/{listing_name}",
        title,
    )
    snapshot = ListingSnapshot(
        uuid4(),
        listing.id,
        datetime.now(timezone.utc),
        price,
        "PLN",
        Decimal("51.4"),
        2,
        "available",
        "Kraków, Dębniki",
        "Spokojne mieszkanie blisko zieleni.",
        uuid4().hex,
    )
    message = EmailMessage(
        uuid4(),
        source.id,
        f"message-{source_name}-{listing_name}",
        snapshot.observed_at,
        f"alerts@{source_name}.example",
        "Alert",
        uuid4().hex,
    )
    return ParsedAlert(source, message, listing, snapshot)


def test_same_property_from_two_sources_shares_candidate_and_tracks_fuzzy_review() -> (
    None
):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = SqlAlchemyCatalogRepository(session)
        first = repository.add_alert(_alert("portal_a", "a-1"))
        second = repository.add_alert(_alert("portal_b", "b-1", price=76000000))

        candidates = session.scalars(select(PropertyCandidateRecord)).all()
        assert first.created and second.created
        assert len(candidates) == 1
        evidence = session.scalars(select(DuplicateEvidenceRecord)).all()
        assert len(evidence) == 1
        assert evidence[0].status == "pending"
        assert evidence[0].confidence >= Decimal("0.60")


def test_merge_split_and_material_change_resurfacing_are_explicit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = SqlAlchemyCatalogRepository(session)
        first = repository.add_alert(_alert("portal_a", "a-1"))
        second = repository.add_alert(
            _alert("portal_b", "b-1", title="Jasne mieszkanie z balkonem")
        )
        candidates = session.scalars(select(PropertyCandidateRecord)).all()
        repository.merge_candidates(candidates[0].id, candidates[1].id)
        assert len(session.scalars(select(PropertyCandidateRecord)).all()) == 1
        candidate = session.scalar(select(PropertyCandidateRecord))
        assert candidate is not None
        repository.record_presentation(
            candidate.id, first.snapshot.id, datetime.now(timezone.utc)
        )

        assert (
            repository.should_resurface(
                candidate.id,
                first.snapshot.id,
                datetime.now(timezone.utc),
                timedelta(days=7),
            )
            is False
        )
        assert (
            repository.should_resurface(
                candidate.id,
                second.snapshot.id,
                datetime.now(timezone.utc),
                timedelta(days=7),
            )
            is True
        )

        link = session.scalar(
            select(CandidateListingRecord).where(
                CandidateListingRecord.candidate_id == candidate.id
            )
        )
        assert link is not None
        repository.split_listing(candidate.id, link.listing_id)
        assert len(session.scalars(select(PropertyCandidateRecord)).all()) == 2
