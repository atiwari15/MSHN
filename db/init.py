"""Apply db/schema.sql to whatever DATABASE_URL points at.

docker-compose mounts the schema into the Postgres image's
docker-entrypoint-initdb.d, which runs it automatically on first boot. No
managed Postgres does that, so a deployed database comes up empty and every
query fails on a missing table. This is the one-shot that fills the gap.

schema.sql is entirely CREATE ... IF NOT EXISTS, so re-running is a no-op
and it is safe to invoke against an already-initialised database. Run it as
a one-off command after provisioning, not on service startup - the API and
both loops boot concurrently, and concurrent CREATE TABLE IF NOT EXISTS on
the same tables can deadlock against each other.

Usage:
    DATABASE_URL=postgresql://... python -m db.init
"""

from __future__ import annotations

import pathlib
import sys

from dotenv import load_dotenv

from corpus.store import database_url, get_client

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def apply_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_PATH.read_text())


def main() -> int:
    load_dotenv()
    url = database_url()

    # Never print the URL itself - it carries the password.
    host = url.rsplit("@", 1)[-1] if "@" in url else url
    print(f"applying {SCHEMA_PATH.name} to {host}")

    with get_client(url) as conn:
        apply_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' ORDER BY table_name
                """
            )
            tables = [r[0] for r in cur.fetchall()]

    print(f"ok - tables present: {', '.join(tables) or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
