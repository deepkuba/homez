from email import policy
from email.parser import BytesParser
from pathlib import Path

from homefinder.fixture_safety import scan_fixture_file, scan_fixture_paths


def test_committed_email_fixtures_are_safe() -> None:
    violations = scan_fixture_paths(
        (Path("data/email_examples"), Path("tests/fixtures"))
    )

    assert violations == []


def test_sanitized_olx_example_preserves_only_the_reviewable_contract() -> None:
    fixture = Path("data/email_examples/olx_alert.eml")
    message = BytesParser(policy=policy.default).parsebytes(fixture.read_bytes())

    assert message["From"] == "OLX Example Alerts <alerts@example.com>"
    assert message["X-Homez-Source"] is None
    assert message.get_body(preferencelist=("plain",)) is not None
    assert message.get_body(preferencelist=("html",)) is not None
    assert scan_fixture_file(fixture) == []


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


def test_scanner_discovers_synthetic_html_and_json(tmp_path: Path) -> None:
    (tmp_path / "baseline.HTML").write_text("<p>Synthetic listing</p>")
    (tmp_path / "baseline.json").write_text('{"url":"https://example.com/item"}')
    (tmp_path / "unsafe.html").write_text('<a href="//real-portal.pl/item">Listing</a>')
    assert {item.rule for item in scan_fixture_paths((tmp_path,))} == {"active-url"}


def test_scanner_rejects_unsafe_parser_fixtures(tmp_path: Path) -> None:
    import pytest

    cases = [
        ("bad.json", '{"api_key":"secret"}'),
        ("bad.json", '{"url":"https:\\u002f\\u002freal-portal.pl"}'),
        ("bad.json", "{} trailing"),
        ("bad.json", '{"safe":1,"safe":"person@private.pl"}'),
        ("bad.html", "<script>alert(1)</script>"),
        ("bad.html", '<p onclick="alert(1)">hello</p>'),
        ("bad.html", '<a href="java&#115;cript:alert(1)">hello</a>'),
        ("bad.html", '<iframe src="https://example.com"></iframe>'),
        ("bad.html", '<script type="application/ld+json">{} trailing</script>'),
        (
            "bad.html",
            '<script type="application/ld+json">{"email":"person@private.pl"}</script>',
        ),
        ("bad.html", "<p>Call +48 600 123 456</p>"),
        ("bad.html", "<p>unfinished"),
        ("bad.html", "<!-- hidden https://real-portal.pl -->"),
        ("bad.html", "<p>broken</div>"),
    ]
    for name, content in cases:
        fixture = tmp_path / name
        fixture.write_text(content)
        if not scan_fixture_file(fixture):
            pytest.fail(f"Unsafe fixture accepted: {content}")


def test_scanner_accepts_inert_structured_data(tmp_path: Path) -> None:
    fixture = tmp_path / "safe.html"
    fixture.write_text(
        '<!doctype html><html><body><script type="application/ld+json">'
        '{"name":"Synthetic listing","url":"https://example.com/item"}'
        '</script><img src="https://example.com/image"></body></html>'
    )
    assert scan_fixture_file(fixture) == []


def test_scanner_fails_closed_on_missing_invalid_and_large_files(
    tmp_path: Path,
) -> None:
    assert scan_fixture_paths((tmp_path / "missing",))
    fixture = tmp_path / "bad.json"
    fixture.write_bytes(b"\xff")
    assert scan_fixture_file(fixture)
    fixture.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    assert scan_fixture_file(fixture)


def test_scanner_rejects_unparsed_html_attributes(tmp_path: Path) -> None:
    fixture = tmp_path / "broken.html"
    fixture.write_text('<p title="hello" stray="unfinished>hidden</p>')
    assert scan_fixture_file(fixture)
    fixture.write_text("<p =oops>hidden</p>")
    assert scan_fixture_file(fixture)


def test_scanner_rejects_json_hidden_secrets_and_nonstandard_values(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "broken.json"
    for content in ('{"a":NaN}', '{"api\\u005fkey":"secret"}', '{"a":1,"a":2}'):
        fixture.write_text(content)
        assert scan_fixture_file(fixture)


def test_fixture_safety_cli_reports_failure(tmp_path: Path) -> None:
    from unittest.mock import patch

    from homefinder.fixture_safety import main

    fixture = tmp_path / "unsafe.json"
    fixture.write_text('{"url":"https://active-portal.pl"}')
    with patch("sys.argv", ["fixture_safety", str(tmp_path)]):
        assert main() == 1
    fixture.write_text('{"url":"https://example.com"}')
    with patch("sys.argv", ["fixture_safety", str(tmp_path)]):
        assert main() == 0
