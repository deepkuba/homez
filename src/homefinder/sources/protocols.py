from typing import Protocol

from homefinder.domain.models import ParsedAlert


class AlertParser(Protocol):
    source_key: str
    version: str
    expected_sender: str
    expected_host: str
    max_message_bytes: int

    def parse(self, raw_message: bytes) -> ParsedAlert: ...
