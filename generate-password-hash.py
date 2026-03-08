"""
Generate a bcrypt password hash for ADMIN_PASSWORD_HASH in backend/.env.

Usage:
    python generate-password-hash.py

Copy the printed ADMIN_PASSWORD_HASH= line into backend/.env.
"""
import getpass

try:
    import bcrypt
except ImportError:
    print("ERROR: bcrypt not installed.")
    print("Run: pip install bcrypt")
    raise SystemExit(1)

password = getpass.getpass("Enter new admin password: ")
confirm = getpass.getpass("Confirm password: ")

if password != confirm:
    print("ERROR: Passwords do not match.")
    raise SystemExit(1)

if len(password) < 8:
    print("ERROR: Password must be at least 8 characters.")
    raise SystemExit(1)

password_bytes = password.encode("utf-8")
if len(password_bytes) > 72:
    print("ERROR: Password is too long for bcrypt (max 72 UTF-8 bytes).")
    print("Use a shorter password or a passphrase under 72 bytes.")
    raise SystemExit(1)

hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")
print()
print("Copy this line into backend/.env:")
print(f"ADMIN_PASSWORD_HASH={hashed}")
