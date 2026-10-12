"""List all public tables in Supabase with row counts.

Uses the direct Postgres connection (psycopg2) so it can query
information_schema / pg_catalog for an authoritative list.
"""
from src.storage.supabase_client import pg_connection


def main() -> None:
    sql = """
        SELECT
            c.relname AS table_name,
            c.reltuples::bigint AS estimated_rows
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
        ORDER BY c.relname
    """
    with pg_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()

    print("=" * 60)
    print(f"{'TABLE':32s} {'EST. ROWS':>10s}")
    print("=" * 60)

    for table_name, est_rows in rows:
        # Exact count for the raw tables (small enough to be fast)
        if table_name.startswith("raw_"):
            with pg_connection() as conn:
                cur = conn.cursor()
                cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
                exact = cur.fetchone()[0]
        else:
            exact = est_rows
        print(f"  {table_name:32s} {exact:>10d}")

    print("=" * 60)


if __name__ == "__main__":
    main()
