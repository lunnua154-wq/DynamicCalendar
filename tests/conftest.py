"""Shared fixtures.

Temp files live under ``data/_test`` (gitignored) rather than the OS temp dir so
that a sandboxed or restricted temp area cannot break the suite.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from app import db

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "samples"


@pytest.fixture
def data_dir() -> Path:
    base = REPO_ROOT / "data" / "_test" / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def db_path(data_dir: Path) -> Path:
    return data_dir / "test.db"


@pytest.fixture
def conn(db_path: Path):
    connection = db.connect(db_path)
    db.init_db(connection)
    # Deterministic clock offset: engine tests reason in UTC.
    db.update_setting(connection, "utc_offset_minutes", "0")
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()
