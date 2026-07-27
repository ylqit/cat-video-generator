from __future__ import annotations

import os
from collections.abc import Iterator

import docker
import pytest
from docker.errors import DockerException
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from cat_video_generator.config import DatabaseOperation, DatabaseSettings
from cat_video_generator.db import create_database_engine
from cat_video_generator.migration import alembic_config


@pytest.fixture(scope="session")
def postgres_settings() -> Iterator[DatabaseSettings]:
    try:
        docker.from_env(timeout=3).ping()
    except DockerException as exc:
        pytest.skip(f"Docker daemon is unavailable: {exc}")

    with PostgresContainer(
        image="postgres:16-alpine",
        username="test",
        password="test",
        dbname="test_cat_video",
        driver="psycopg",
    ) as container:
        settings = DatabaseSettings(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            database="test_cat_video",
            user="test",
            password="test",
            sslmode="disable",
            allow_insecure_local_tests=True,
        )
        yield settings


@pytest.fixture()
def migrated_database(
    postgres_settings: DatabaseSettings,
) -> Iterator[DatabaseSettings]:
    settings = postgres_settings
    environment = {
        "CAT_VIDEO_DB_HOST": settings.host,
        "CAT_VIDEO_DB_PORT": str(settings.port),
        "CAT_VIDEO_DB_NAME": settings.database,
        "CAT_VIDEO_DB_USER": settings.user,
        "CAT_VIDEO_DB_PASSWORD": settings.password,
        "CAT_VIDEO_DB_SSLMODE": settings.sslmode,
        "CAT_VIDEO_ALLOW_INSECURE_LOCAL_TESTS": "true",
    }
    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)
    engine = create_database_engine(settings, DatabaseOperation.TEST)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS cat_video CASCADE"))
        command.upgrade(alembic_config(), "head")
        yield settings
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS cat_video CASCADE"))
        engine.dispose()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
