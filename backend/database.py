import os

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row


def database():
    """Open a connection that commits on success and rolls back on error."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(503, "Set DATABASE_URL to connect to your local database.")
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=3)
