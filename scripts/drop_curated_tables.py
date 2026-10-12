"""Drop curated tables that are no longer needed."""
from src.storage.supabase_client import pg_connection

TABLES = [
    "corporate_actions",
]


def main() -> None:
    with pg_connection() as conn:
        cur = conn.cursor()
        for t in TABLES:
            try:
                cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
                print(f"  DROPPED {t}")
            except Exception as e:
                print(f"  FAILED {t}: {e}")
    print("Done")


if __name__ == "__main__":
    main()