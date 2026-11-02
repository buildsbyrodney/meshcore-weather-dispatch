"""Small, shared administrator credential validation helpers."""

MIN_PASSWORD_LENGTH = 6


def validate_password(password: str, field: str = "password") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"{field} must be at least {MIN_PASSWORD_LENGTH} characters")
