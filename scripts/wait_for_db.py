"""Utility script: block until Postgres accepts connections. Useful for local dev/CI."""

import asyncio
import sys

from app.database.session import check_database_connection


async def main(retries: int = 30, delay: float = 1.0) -> None:
    for attempt in range(1, retries + 1):
        try:
            await check_database_connection()
            print("Database is ready.")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[{attempt}/{retries}] Database not ready yet: {exc}")
            await asyncio.sleep(delay)
    print("Database did not become ready in time.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
