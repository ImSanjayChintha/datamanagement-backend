"""
Module: administration.utils.password
Purpose: Secure temporary password generation for admin invites and resets.
"""
import secrets
from app.core.constants import TEMP_PASSWORD_LENGTH, TEMP_PASSWORD_CHARSET


def generate_temp_password() -> str:
    """Generate a temporary password using the configured length and charset.

    Returns:
        A random password guaranteed to contain at least one uppercase letter,
        one lowercase letter, and one digit.
    """
    while True:
        pw = "".join(secrets.choice(TEMP_PASSWORD_CHARSET) for _ in range(TEMP_PASSWORD_LENGTH))
        if (any(c.isupper() for c in pw)
                and any(c.islower() for c in pw)
                and any(c.isdigit() for c in pw)):
            return pw
