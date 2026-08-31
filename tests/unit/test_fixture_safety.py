from pathlib import Path

from homefinder.fixture_safety import scan_fixture_file, scan_fixture_paths


def test_committed_email_fixtures_are_safe() -> None:
    violations = scan_fixture_paths(
        (Path("data/email_examples"), Path("tests/fixtures"))
    )

    assert violations == []


def test_scanner_rejects_representative_unsafe_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "unsafe.eml"
    fixture.write_text(
        "From: Real Person <person@private-mail.pl>\n"
        "To: email@example.com\n"
        "List-Unsubscribe: <https://tracker.example.pl/unsubscribe?token=secret>\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Open https://tracker.example.pl/click?id=personalized-id\n",
        encoding="utf-8",
    )

    violations = scan_fixture_file(fixture)

    assert {violation.rule for violation in violations} == {
        "active-url",
        "personal-email",
        "sensitive-header",
        "token-like-data",
    }
