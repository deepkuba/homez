"""Pure content-addressed parser release identity."""

import hashlib
import json
import re

from homefinder.parsers.contracts import Portal

_HASH = re.compile(r"[0-9a-f]{64}")


def content_addressed_release_hash(
    source: Portal,
    parser_content_hash: str,
    configuration_hash: str,
    dependency_lock_hash: str,
) -> str:
    if source not in {"gratka", "morizon", "otodom", "olx"} or any(
        _HASH.fullmatch(value) is None
        for value in (parser_content_hash, configuration_hash, dependency_lock_hash)
    ):
        raise ValueError("invalid content-addressed release input")
    payload = json.dumps(
        {
            "source": source,
            "parser_content_hash": parser_content_hash,
            "configuration_hash": configuration_hash,
            "dependency_lock_hash": dependency_lock_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = ["content_addressed_release_hash"]
