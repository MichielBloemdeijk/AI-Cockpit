from __future__ import annotations

import pytest
from sqlalchemy.pool import NullPool

from app.db.session import initialize_engine


@pytest.mark.asyncio
async def test_initialize_engine_uses_null_pool_for_file_backed_sqlite():
    engine = initialize_engine()

    assert isinstance(engine.sync_engine.pool, NullPool)
    async with engine.connect() as connection:
        busy_timeout = await connection.exec_driver_sql("PRAGMA busy_timeout")
        assert busy_timeout.scalar_one() == 30_000
