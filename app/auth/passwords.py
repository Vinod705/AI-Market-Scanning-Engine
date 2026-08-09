"""Password hashing — bcrypt only. Plaintext passwords are never persisted
anywhere (database, logs, session store); only `hash_password`'s output
ever reaches the database.
"""

import secrets
import string

import bcrypt

_BCRYPT_ROUNDS = 12
_TEMP_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def hash_password(plain_password: str) -> str:
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash (e.g. hashing scheme changed) — never treat as a match.
        return False


def generate_temporary_password(length: int = 16) -> str:
    """A cryptographically random one-time password for new/reset accounts.
    Returned to the caller exactly once — nothing here persists it in
    plaintext anywhere."""
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length))
