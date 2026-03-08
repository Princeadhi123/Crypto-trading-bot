"""
Generate a self-signed TLS certificate for local HTTPS.

Usage:
    python generate-certs.py

Output:
    backend/certs/server.crt  — certificate (share with browser / nginx)
    backend/certs/server.key  — private key  (keep secret, never commit)

After running, set in backend/.env:
    SSL_CERTFILE=certs/server.crt
    SSL_KEYFILE=certs/server.key
"""
import ipaddress
import os
from datetime import datetime, timedelta, timezone

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    print("ERROR: cryptography package not installed.")
    print("Run: pip install cryptography>=41.0.0")
    raise SystemExit(1)

OUT_DIR = os.path.join(os.path.dirname(__file__), "backend", "certs")
os.makedirs(OUT_DIR, exist_ok=True)

KEY_PATH = os.path.join(OUT_DIR, "server.key")
CERT_PATH = os.path.join(OUT_DIR, "server.crt")

# ── Generate RSA private key ──────────────────────────────────────────────────
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# ── Build self-signed certificate ─────────────────────────────────────────────
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CryptoBot Pro"),
])

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.now(timezone.utc))
    .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]),
        critical=False,
    )
    .sign(key, hashes.SHA256())
)

# ── Write files ───────────────────────────────────────────────────────────────
with open(KEY_PATH, "wb") as f:
    f.write(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))

with open(CERT_PATH, "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print(f"Generated: {CERT_PATH}")
print(f"Generated: {KEY_PATH}")
print()
print("Add to backend/.env:")
print("  SSL_CERTFILE=certs/server.crt")
print("  SSL_KEYFILE=certs/server.key")
print()
print("NOTE: Your browser will warn about a self-signed cert.")
print("      Click 'Advanced > Proceed' once to accept it.")
print("NOTE: certs/ is gitignored — never commit the key file.")
