import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "homez-sql"


def _fake_command(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env bash\nprintf 'args=%s\\n' \"$*\"\nprintf 'stdin='\ncat\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def test_homez_sql_uses_read_only_role_and_forwards_stdin_as_csv(
    tmp_path: Path,
) -> None:
    fake_docker = _fake_command(tmp_path / "docker")
    environment = {
        **os.environ,
        "HOMEZ_SQL_DOCKER_BIN": str(fake_docker),
    }

    result = subprocess.run(  # noqa: S603 - the executable is repository-owned
        [str(SCRIPT), "--csv"],
        input="SELECT count(*) FROM analytics.latest_offers;\n",
        text=True,
        capture_output=True,
        check=True,
        env=environment,
    )

    assert "compose --project-name homez" in result.stdout
    assert "exec -T db psql -X" in result.stdout
    assert "-U homez_analytics_reader" in result.stdout
    assert "-d homefinder --csv" in result.stdout
    assert "stdin=SELECT count(*) FROM analytics.latest_offers;" in result.stdout
    assert "-U homefinder" not in result.stdout


def test_homez_sql_rejects_unsafe_ssh_host(tmp_path: Path) -> None:
    environment = {
        **os.environ,
        "HOMEZ_SQL_SSH_BIN": str(_fake_command(tmp_path / "ssh")),
    }

    result = subprocess.run(  # noqa: S603 - the executable is repository-owned
        [str(SCRIPT), "--host", "server;touch /tmp/nope"],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 2
    assert "invalid SSH host" in result.stderr
