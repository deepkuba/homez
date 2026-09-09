"""Streaming NAS artifact boundary; raw bytes stay out of metadata."""

from typing import Protocol

from homefinder.parsers.contracts import PageInput, Portal


class ArtifactWriter(Protocol):
    def store(self, source: Portal, page: PageInput) -> str: ...
