from typing import Protocol

from homefinder.domain.models import ParsedAlert


class AlertParser(Protocol):
    source_key: str
    version: str

    def parse(self, raw_message: bytes) -> ParsedAlert: ...
