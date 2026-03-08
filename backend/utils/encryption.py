"""
Field-level encryption for sensitive database columns.

Set FIELD_ENCRYPTION_KEY in backend/.env to enable.
Generate a key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

If the key is not set, values are stored and returned as plain text (backward compat).
If a field was stored before encryption was enabled, decryption will fall back to the raw value gracefully.
"""
import os
from dotenv import load_dotenv

load_dotenv()

_RAW_KEY: str = os.getenv("FIELD_ENCRYPTION_KEY", "").strip()

# Lazily initialised so the module can be imported even before cryptography is installed
_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    if not _RAW_KEY:
        return None
    from cryptography.fernet import Fernet
    _fernet = Fernet(_RAW_KEY.encode())
    return _fernet


def encrypt_value(value: str) -> str:
    """Encrypt a string value. Returns raw value if encryption is not configured."""
    f = _get_fernet()
    if f is None:
        return value
    return f.encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    """Decrypt a string value. Returns raw value if decryption fails (backward compat)."""
    f = _get_fernet()
    if f is None:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except Exception:
        # Value was stored before encryption was enabled — return as-is
        return value
