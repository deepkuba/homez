from typing import Protocol

from homefinder.domain.models import ParsedAlert


class AlertParser(Protocol):
    def parse(self, raw_message: bytes) -> ParsedAlert: ...
