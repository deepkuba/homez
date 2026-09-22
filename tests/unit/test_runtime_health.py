from datetime import datetime, timezone
from pathlib import Path

from homefinder.runtime_health import main


def test_dedicated_runtime_health_probe_checks_heartbeat_without_full_cli(
    tmp_path: Path,
) -> None:
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")

    assert (
        main(
            [
                "--heartbeat-file",
                str(heartbeat),
                "--max-age-seconds",
                "90",
            ]
        )
        == 0
    )
