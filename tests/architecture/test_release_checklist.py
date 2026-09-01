from pathlib import Path


def test_release_checklist_names_required_evidence_and_stays_unapproved() -> None:
    checklist = Path("docs/release-checklist.md").read_text(encoding="utf-8")

    for required in (
        "Immutable image reference and digest",
        "Alembic revision",
        "PostgreSQL/PostGIS",
        "Vertical workflow",
        "Backup and clean restore",
        "Shadow report 1",
        "Shadow report 2",
        "#69",
    ):
        assert required in checklist
    assert "Release decision: **NOT APPROVED**" in checklist
