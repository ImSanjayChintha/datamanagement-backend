"""
Admin setup + reset script.
Run from the backend root:  python scripts/reset_admin.py

What it does:
  1. Executes app/db/seeds/admin_functions.sql
       — drops v_admin_users from admin schema if misplaced
       — recreates it in toolkit schema (correct location)
       — creates all read + write functions
  2. Upserts admin@corex.com with a fresh bcrypt hash for 'admin123'
  3. Simulates the login query and reports pass / fail
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
import asyncpg

ADMIN_EMAIL    = "admin@corex.com"
ADMIN_PASSWORD = "admin123"

SEEDS_SQL = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "app", "db", "seeds", "admin_functions.sql",
)


async def main():
    print(f"Connecting to: {settings.DATABASE_URL[:40]}...")
    conn = await asyncpg.connect(settings.DATABASE_URL)

    try:
        await conn.execute(
            "SET search_path TO toolkit, public, admin, pim, company, ecomm, forecast"
        )

        # ── 1. Run the full seeds SQL ────────────────────────────────────────
        print("\n[1] Running app/db/seeds/admin_functions.sql ...")
        with open(SEEDS_SQL, encoding="utf-8") as f:
            sql = f.read()
        await conn.execute(sql)
        print("    Done.")

        # ── 2. Upsert admin user with fresh bcrypt hash ──────────────────────
        print(f"\n[2] Upserting {ADMIN_EMAIL} with password={ADMIN_PASSWORD!r} ...")
        new_hash = get_password_hash(ADMIN_PASSWORD)
        await conn.execute(
            """INSERT INTO admin.admin_users
                   (email, username, full_name, role, hashed_password, is_active, must_change_password)
               VALUES ($1, $2, 'Admin', 'admin', $3, TRUE, FALSE)
               ON CONFLICT (email) DO UPDATE
               SET hashed_password         = EXCLUDED.hashed_password,
                   is_active               = TRUE,
                   must_change_password    = FALSE,
                   invite_token            = NULL,
                   invite_token_expires_at = NULL,
                   updated_at              = NOW()""",
            ADMIN_EMAIL, ADMIN_EMAIL.split("@")[0], new_hash,
        )
        print("    Done.")

        # ── 3. Verify view is now in toolkit ─────────────────────────────────
        print("\n[3] Checking view location ...")
        schema = await conn.fetchval(
            "SELECT table_schema FROM information_schema.views WHERE table_name = 'v_admin_users'"
        )
        print(f"    v_admin_users is in schema: {schema!r}  (should be 'toolkit')")

        # ── 4. Simulate login ─────────────────────────────────────────────────
        print(f"\n[4] Simulating login for {ADMIN_EMAIL} ...")
        admin = await conn.fetchrow(
            "SELECT * FROM v_admin_users WHERE email=$1 AND is_active=TRUE",
            ADMIN_EMAIL,
        )
        if not admin:
            print("    FAIL: v_admin_users returned no row")
        elif not verify_password(ADMIN_PASSWORD, admin["hashed_password"]):
            print("    FAIL: password hash does not match")
        else:
            print(f"    OK — login will succeed  id={admin['id']} role={admin['role']}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
