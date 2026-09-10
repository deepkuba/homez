from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])([A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,}))(?![\w.-])",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TOKEN_PATTERN = re.compile(
    r"(?:bearer\s+|(?:access[_-]?token|refresh[_-]?token|token|tracking[_-]?id|"
    r"recipient[_-]?id|user[_-]?id|signature|authorization|api[_-]?key|"
    r"unsubscribe)\s*[:=]\s*)[^\s&<>\"']+",
    re.IGNORECASE,
)
SENSITIVE_HEADERS = frozenset(
    {
        "arc-authentication-results",
        "arc-message-signature",
        "arc-seal",
        "authentication-results",
        "delivered-to",
        "dkim-signature",
        "feedback-id",
        "list-unsubscribe",
        "list-unsubscribe-post",
        "received",
        "received-spf",
        "return-path",
        "x-forwarded-to",
        "x-google-smtp-source",
        "x-original-to",
        "x-received",
    }
)
RESERVED_DOMAINS = frozenset({"example.com", "example.net", "example.org"})


@dataclass(frozen=True, order=True)
class FixtureViolation:
    path: Path
    line: int
    rule: str
    detail: str


def _is_reserved_host(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    return normalized in RESERVED_DOMAINS or normalized.endswith(".invalid")


def _decoded_message_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    decoded_parts: list[str] = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_maintype() != "text":
            continue
        try:
            content = part.get_content()
        except (KeyError, LookupError, UnicodeError):
            continue
        if isinstance(content, str) and content not in text:
            decoded_parts.append(content)
    return "\n".join((text, *decoded_parts))


MAX_FIXTURE_BYTES = 2 * 1024 * 1024
FIXTURE_SUFFIXES = frozenset({".eml", ".html", ".json"})
PARSER_URL_PATTERN = re.compile(r"(?:[a-z][a-z0-9+.-]*:)?//[^\s<>\"']+", re.I)
SECRET_KEY_PATTERN = re.compile(
    r"^(?:password|secret|client_secret|access_token|refresh_token|token|"
    r"api[_-]?key|authorization|cookie|signature|tracking[_-]?id|recipient[_-]?id)$",
    re.I,
)
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+\d[\d ()-]{7,}\d)(?!\w)")


def _json_text(text: str) -> str:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result or SECRET_KEY_PATTERN.fullmatch(key):
                raise ValueError("Duplicate or sensitive JSON key")
            result[key] = value
        return result

    def invalid_constant(value: str) -> object:
        raise ValueError("Nonstandard JSON constant")

    value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    return json.dumps(value, ensure_ascii=False)


class _FixtureHTMLParser(HTMLParser):
    """Accept a small, explicitly closed synthetic HTML subset."""

    void_tags = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )
    forbidden_tags = frozenset(
        {"iframe", "object", "embed", "style", "base", "link", "svg", "math"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.script_data: list[str] = []
        self.decoded: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw_tag = self.get_starttag_text() or ""
        if not re.fullmatch(
            r"<[A-Za-z][A-Za-z0-9:-]*(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*"
            r"(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))?)*\s*/?>",
            raw_tag,
        ):
            raise ValueError("Malformed HTML attributes")
        if tag in self.forbidden_tags:
            raise ValueError("Executable markup")
        if len({key for key, _ in attrs}) != len(attrs):
            raise ValueError("Duplicate attributes")
        for key, value in attrs:
            if key.startswith("on") or key in {"style", "srcdoc"}:
                raise ValueError("Executable attribute")
            self.decoded.append(value or "")
        if tag == "meta" and "http-equiv" in dict(attrs):
            raise ValueError("Active metadata")
        if tag == "script":
            if dict(attrs).get("type") not in {
                "application/ld+json",
                "application/json",
            }:
                raise ValueError("Executable script")
            if "src" in dict(attrs):
                raise ValueError("External script")
            self.script_data = []
        if tag not in self.void_tags:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.void_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            raise ValueError("Unbalanced HTML")
        if tag == "script":
            self.decoded.append(_json_text("".join(self.script_data)))

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] == "script":
            self.script_data.append(data)
        elif "<" in data or ">" in data:
            raise ValueError("Unparsed markup")
        self.decoded.append(data)

    def handle_comment(self, data: str) -> None:
        raise ValueError("Comments are outside the synthetic fixture subset")

    def handle_decl(self, decl: str) -> None:
        if decl.lower() != "doctype html":
            raise ValueError("Unsupported declaration")

    def unknown_decl(self, data: str) -> None:
        raise ValueError("Unsupported declaration")

    def handle_pi(self, data: str) -> None:
        raise ValueError("Processing instruction")


def _scan_parser_fixture(path: Path, raw: bytes) -> list[FixtureViolation]:
    try:
        text = raw.decode("utf-8", errors="strict")
        if path.suffix.lower() == ".json":
            text += "\n" + _json_text(text)
        else:
            # HTMLParser repairs or silently discards malformed input; first require
            # every byte to belong to a complete text or markup token.
            tokens = re.findall(r"[^<>]+|<(?:[^<>\"']|\"[^\"]*\"|'[^']*')*>", text)
            if "".join(tokens) != text:
                raise ValueError("Unparsed HTML content")
            parser = _FixtureHTMLParser()
            parser.feed(text)
            parser.close()
            if parser.stack:
                raise ValueError("Unclosed HTML")
            text += "\n" + "\n".join(parser.decoded)
        text = unescape(text)
    except (ValueError, RecursionError, AssertionError):
        return [
            FixtureViolation(path, 1, "unparsed-fixture", "Invalid or unsafe structure")
        ]
    violations: set[FixtureViolation] = set()
    for number, line in enumerate(text.splitlines(), 1):
        for match in EMAIL_PATTERN.finditer(line):
            if not _is_reserved_host(match.group(2)):
                violations.add(
                    FixtureViolation(path, number, "personal-email", "redacted")
                )
        for match in PARSER_URL_PATTERN.finditer(line):
            try:
                host = urlsplit(match.group()).hostname
            except ValueError:
                host = None
            if host is None or not _is_reserved_host(host):
                violations.add(FixtureViolation(path, number, "active-url", "redacted"))
        if re.search(
            r"(?:javascript|vbscript|data|tel|sms):", re.sub(r"\s+", "", line), re.I
        ):
            violations.add(FixtureViolation(path, number, "active-content", "redacted"))
        if PHONE_PATTERN.search(line):
            violations.add(FixtureViolation(path, number, "personal-phone", "redacted"))
        if TOKEN_PATTERN.search(line) or re.search(
            r"(?:password|secret|api[_-]?key|token)[\"']?\s*[:=]", line, re.I
        ):
            violations.add(
                FixtureViolation(path, number, "token-like-data", "redacted")
            )
    return sorted(violations)


def scan_fixture_file(path: Path) -> list[FixtureViolation]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_FIXTURE_BYTES + 1)
    except OSError:
        return [FixtureViolation(path, 1, "unreadable-fixture", "Cannot read fixture")]
    if len(raw) > MAX_FIXTURE_BYTES:
        return [FixtureViolation(path, 1, "oversized-fixture", "Fixture exceeds 2 MiB")]
    if path.suffix.lower() in {".html", ".json"}:
        return _scan_parser_fixture(path, raw)
    text = _decoded_message_text(raw)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    violations: set[FixtureViolation] = set()

    for header in message:
        normalized_header = header.lower()
        if normalized_header in SENSITIVE_HEADERS or (
            normalized_header.startswith("x-") and normalized_header != "x-homez-source"
        ):
            violations.add(FixtureViolation(path, 1, "sensitive-header", header))

    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in EMAIL_PATTERN.finditer(line):
            address, domain = match.groups()
            if not _is_reserved_host(domain):
                violations.add(
                    FixtureViolation(
                        path, line_number, "personal-email", address.lower()
                    )
                )

        for match in URL_PATTERN.finditer(line):
            raw_url = match.group(0).rstrip(".,);]")
            host = urlsplit(raw_url.replace("&amp;", "&")).hostname
            if host is None or not _is_reserved_host(host):
                violations.add(
                    FixtureViolation(path, line_number, "active-url", raw_url)
                )

        if TOKEN_PATTERN.search(line):
            violations.add(
                FixtureViolation(path, line_number, "token-like-data", "redacted")
            )

    return sorted(violations)


def scan_fixture_paths(paths: tuple[Path, ...]) -> list[FixtureViolation]:
    fixture_files: set[Path] = set()
    violations: list[FixtureViolation] = []
    for path in paths:
        if path.is_file() and path.suffix.lower() in FIXTURE_SUFFIXES:
            fixture_files.add(path)
        elif path.is_dir():
            fixture_files.update(
                file
                for file in path.rglob("*")
                if file.is_file() and file.suffix.lower() in FIXTURE_SUFFIXES
            )
        else:
            violations.append(
                FixtureViolation(path, 1, "unscanned-path", "Invalid fixture path")
            )
    return sorted(
        violations
        + [
            violation
            for fixture_file in sorted(fixture_files)
            for violation in scan_fixture_file(fixture_file)
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject unsafe data in committed email, HTML, and JSON fixtures."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    violations = scan_fixture_paths(tuple(args.paths))
    for violation in violations:
        print(
            f"{violation.path}:{violation.line}: {violation.rule}: {violation.detail}"
        )
    return bool(violations)


if __name__ == "__main__":
    raise SystemExit(main())
