from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def make_pool(url: str, name: str, max_size: int = 5) -> ConnectionPool:
    """Pool autocommit con dict_row (también válido para PostgresSaver)."""
    pool = ConnectionPool(
        url, min_size=1, max_size=max_size, name=name, open=False,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    pool.open(wait=True, timeout=15)
    return pool
