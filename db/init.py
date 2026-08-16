"""Apply db/schema.sql to whatever DATABASE_URL points at.

docker-compose mounts the schema into the Postgres image's
docker-entrypoint-initdb.d, which runs it automatically on first boot. No
managed Postgres does that, so a deployed database comes up empty and every
query fails on a missing table. This is the one-shot that fills the gap.

Deliberately does NOT go through corpus.store.get_client. That helper calls
pgvector's register_vector, which needs the `vector` type to already exist -
but creating that type is the first thing schema.sql does. Bootstrapping a
fresh database through get_client is therefore impossible: registration
would run before creation. Locally the ordering never bites, because compose
has already applied the schema before any code connects; it only shows up on
the fresh managed database this script exists to set up. Nothing here passes
or reads a vector, so the plain connection is all that is needed.

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

import psycopg
from dotenv import load_dotenv

from corpus.store import database_url

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def pgvector_available(conn) -> bool:
    """Whether the server can create the extension at all.

    Distinct from whether it is enabled in this database - checking up front
    separates 'this Postgres build has no pgvector' (provision elsewhere)
    from an ordinary failure, instead of surfacing either as a confusing
    'vector type not found'.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        return cur.fetchone() is not None


def apply_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_PATH.read_text())


def main() -> int:
    load_dotenv()
    url = database_url()

    # Never print the URL itself - it carries the password.
    host = url.rsplit("@", 1)[-1] if "@" in url else url
    print(f"applying {SCHEMA_PATH.name} to {host}")

    with psycopg.connect(url, autocommit=True) as conn:
        if not pgvector_available(conn):
            print(
                "error: this Postgres server does not offer the 'vector' "
                "extension.\nMSHN stores embeddings in pgvector, so provision "
                "the database somewhere that ships it (Neon has it as "
                "standard) and re-run against that connection string.",
                file=sys.stderr,
            )
            return 1

        apply_schema(conn)

        with conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cur.fetchone()
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' ORDER BY table_name
                """
            )
            tables = [r[0] for r in cur.fetchall()]

    print(f"pgvector enabled (version {row[0] if row else 'unknown'})")
    print(f"ok - tables present: {', '.join(tables) or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
