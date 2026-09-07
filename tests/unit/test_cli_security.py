import pytest

from homefinder.cli import _parser


@pytest.mark.parametrize("command", ("backup", "restore"))
def test_backup_commands_do_not_accept_secrets_on_command_line(command: str) -> None:
    parser = _parser()
    positional = "backup.dump.enc"

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                command,
                positional,
                "--database-url",
                "postgresql://user:secret@db/homefinder",
            ]
        )

    with pytest.raises(SystemExit):
        parser.parse_args([command, positional, "--encryption-key", "secret"])


def test_scraper_server_accepts_only_a_secret_file_path() -> None:
    parser = _parser()

    parsed = parser.parse_args(
        [
            "scraper-server",
            "--source",
            "olx",
            "--token-file",
            "/run/secrets/scraper_token",
            "--state-file",
            "/var/lib/homefinder-scraper/rate-limit.json",
        ]
    )

    assert parsed.source == "olx"
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["scraper-server", "--source", "olx", "--token", "secret-value"]
        )
