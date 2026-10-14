"""Static checks for container configuration files (Dockerfile and docker-compose.yml)."""

import pathlib
import re

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE_PATH = ROOT_DIR / "Dockerfile"
COMPOSE_PATH = ROOT_DIR / "docker-compose.yml"


def test_dockerfile_and_compose_exist() -> None:
    assert DOCKERFILE_PATH.is_file(), "Dockerfile must exist"
    assert COMPOSE_PATH.is_file(), "docker-compose.yml must exist"


def test_dockerfile_copies_ui() -> None:
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert re.search(r"COPY\s+ui\s+.*ui", content), "Dockerfile must COPY ui directory"


def test_no_db_path_in_dockerfile_and_compose() -> None:
    dockerfile_content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    compose_content = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "DB_PATH" not in dockerfile_content, "Dockerfile must not mention DB_PATH"
    assert "DB_PATH" not in compose_content, "docker-compose.yml must not mention DB_PATH"


def test_sets_agent_wallet_data_dir() -> None:
    dockerfile_content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    compose_content = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "AGENT_WALLET_DATA_DIR=/app/data" in dockerfile_content, (
        "Dockerfile must set AGENT_WALLET_DATA_DIR=/app/data"
    )
    assert "AGENT_WALLET_DATA_DIR: /app/data" in compose_content or (
        "AGENT_WALLET_DATA_DIR=/app/data" in compose_content
    ), "docker-compose.yml must set AGENT_WALLET_DATA_DIR to /app/data"


def test_runs_as_non_root_user() -> None:
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    user_match = re.search(r"^\s*USER\s+(\S+)", content, re.MULTILINE)
    assert user_match is not None, "Dockerfile must declare a USER"
    user = user_match.group(1)
    assert user != "root" and user != "0", f"Dockerfile USER must not be root (got {user})"
    assert "10001" in content, "Dockerfile must configure non-root uid 10001"


def test_compose_mounts_volume_at_app_data() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    assert re.search(r"- \./data:/app/data\b", content) or re.search(r":/app/data\b", content), (
        "docker-compose.yml must mount a volume at /app/data"
    )


def test_data_dir_created_and_owned_at_build_time() -> None:
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "/app/data" in content, "Dockerfile must create /app/data"
    assert "chown" in content, "Dockerfile must chown directory to non-root app user"
