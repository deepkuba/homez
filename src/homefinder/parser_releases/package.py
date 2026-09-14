"""Derive executable parser identity from immutable installed build inputs."""

import hashlib
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from homefinder.parser_release_identity import content_addressed_release_hash
from homefinder.parsers import contracts as parser_contracts
from homefinder.parsers.contracts import DECLARED_FIELDS, MAX_PAGE_BYTES, Parser, Portal

_PARSER_CLASSES = {
    "gratka": ("homefinder.parsers.gratka", "GratkaPageParser"),
    "morizon": ("homefinder.parsers.morizon", "MorizonPageParser"),
    "otodom": ("homefinder.parsers.otodom", "OtodomPageParser"),
    "olx": ("homefinder.parsers.olx", "OlxPageParser"),
}
_MAX_LOCK_BYTES = 2_000_000


@dataclass(frozen=True)
class PackagedParser:
    source: Portal
    release_hash: str
    parser_content_hash: str
    configuration_hash: str
    dependency_lock_hash: str
    parser: Parser


def load_packaged_parser(
    source: Portal, *, dependency_lock_file: Path
) -> PackagedParser:
    module_name, class_name = _PARSER_CLASSES[source]
    module = importlib.import_module(module_name)
    package_dir = Path(cast(str, module.__file__)).parent
    parser_content_hash = _hash_source_files(package_dir)
    configuration = hashlib.sha256()
    configuration.update(
        json.dumps(
            {
                "format": "parser-package-v1",
                "source": source,
                "max_page_bytes": MAX_PAGE_BYTES,
                "declared_fields": DECLARED_FIELDS,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    contract_file = Path(parser_contracts.__file__)
    contract_content = contract_file.read_bytes()
    configuration.update(len(contract_content).to_bytes(8, "big"))
    configuration.update(contract_content)
    configuration_hash = configuration.hexdigest()
    lock = dependency_lock_file.read_bytes()
    if not lock or len(lock) > _MAX_LOCK_BYTES:
        raise ValueError("dependency lock has invalid size")
    dependency_lock_hash = hashlib.sha256(lock).hexdigest()
    release_hash = content_addressed_release_hash(
        source,
        parser_content_hash,
        configuration_hash,
        dependency_lock_hash,
    )
    parser_class = getattr(module, class_name)
    parser = cast(Parser, parser_class(release_hash))
    return PackagedParser(
        source,
        release_hash,
        parser_content_hash,
        configuration_hash,
        dependency_lock_hash,
        parser,
    )


def _hash_source_files(package_dir: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(package_dir.glob("*.py"), key=lambda path: path.name)
    if not paths:
        raise ValueError("parser package contains no source")
    for path in paths:
        content = path.read_bytes()
        digest.update(len(path.name).to_bytes(4, "big"))
        digest.update(path.name.encode())
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
