"""One-off script to create (or reset) the initial ADMIN account.

Not run automatically at container startup and never invoked with a
hardcoded password — this is the "secure deployment/setup process" the
Phase 6 spec requires for provisioning the first admin. Run it manually,
once, after deploying:

    docker compose exec app python scripts/create_admin.py --email you@example.com --name "Your Name"

With no --password given, a random one-time password is generated and
printed ONCE — nothing in this script persists it in plaintext anywhere,
and the account is created with must_change_password=True so that
one-time password can't linger as a long-term credential.
"""

import argparse
import asyncio
import sys

from app.auth.models import UserRole, UserStatus
from app.auth.passwords import generate_temporary_password, hash_password
from app.database.session import AsyncSessionLocal
from app.repositories.user_repository import UserRepository


async def main(email: str, name: str, password: str | None, force: bool) -> None:
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        existing = await repo.get_by_email(email)

        temp_password = password or generate_temporary_password()

        if existing is not None:
            if not force:
                print(
                    f"A user with email '{email}' already exists (id={existing.id}, "
                    f"role={existing.role}). Re-run with --force to reset their password "
                    "and promote them to ADMIN instead.",
                    file=sys.stderr,
                )
                sys.exit(1)
            await repo.set_password(
                existing, hash_password(temp_password), must_change_password=True
            )
            await repo.set_role(existing, UserRole.ADMIN.value)
            if existing.status != UserStatus.ACTIVE.value:
                await repo.set_status(existing, UserStatus.ACTIVE.value)
            await session.commit()
            print(f"Reset existing user '{email}' to an active ADMIN account.")
        else:
            await repo.create(
                email=email,
                name=name,
                password_hash=hash_password(temp_password),
                role=UserRole.ADMIN.value,
                must_change_password=True,
            )
            await session.commit()
            print(f"Created ADMIN account '{email}'.")

        if password is None:
            print("\nOne-time password (save this now — it will not be shown again):")
            print(f"  {temp_password}\n")
            print("This account must change its password on first login.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--password",
        default=None,
        help="Set an explicit initial password instead of generating a random one. "
        "Read from the command line or pipe it in — never commit it anywhere.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="If a user with this email already exists, reset their password and role to ADMIN.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.email, args.name, args.password, args.force))
