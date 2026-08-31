from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
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


def scan_fixture_file(path: Path) -> list[FixtureViolation]:
    raw = path.read_bytes()
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
    for path in paths:
        if path.is_file() and path.suffix.lower() == ".eml":
            fixture_files.add(path)
        elif path.is_dir():
            fixture_files.update(path.rglob("*.eml"))
    return sorted(
        violation
        for fixture_file in sorted(fixture_files)
        for violation in scan_fixture_file(fixture_file)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject personal or active data in committed email fixtures."
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
