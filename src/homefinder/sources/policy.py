from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    key: str
    allowed_senders: frozenset[str]
    allowed_hosts: frozenset[str]
    max_message_bytes: int = 512_000
    page_fetch_enabled: bool = False
    max_messages_per_poll: int = 50
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.max_message_bytes <= 0:
            raise ValueError("max_message_bytes must be positive")

    def allows_sender(self, sender: str) -> bool:
        return sender.casefold() in self.allowed_senders

    def allows_url(self, url: str) -> bool:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.casefold() in self.allowed_hosts
            and parsed.username is None
            and parsed.password is None
        )


class SourcePolicyRegistry:
    def __init__(self, policies: tuple[SourcePolicy, ...] = ()) -> None:
        self._policies = {policy.key: policy for policy in policies}

    def register(self, policy: SourcePolicy) -> None:
        self._policies[policy.key] = policy

    def get(self, key: str) -> SourcePolicy | None:
        return self._policies.get(key)

    def require(self, key: str) -> SourcePolicy:
        policy = self.get(key)
        if policy is None:
            raise KeyError(f"source policy is not registered: {key}")
        return policy

    def can_fetch_pages(self, key: str, url: str) -> bool:
        policy = self.require(key)
        return policy.page_fetch_enabled and policy.allows_url(url)
