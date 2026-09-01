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
