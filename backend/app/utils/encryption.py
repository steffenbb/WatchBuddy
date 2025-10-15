"""
encryption.py

Simple AES-GCM encryption utilities for storing sensitive keys in DB.
Uses app key stored on disk at /app/data/.app_key or Docker secret /run/secrets/app_key.
If not present, generates and stores file (first-run only).
"""

import os
import base64
from cryptography.fernet import Fernet
from pathlib import Path


APP_KEY_PATHS = [
    "/run/secrets-gen/app_key.txt",  # Docker secret (auto-generated)
    "/app/data/.app_key",            # Local file
    "data/.app_key"                  # Development fallback
]

def _get_or_create_app_key() -> bytes:
    """Load or create app encryption key."""
    for path in APP_KEY_PATHS:
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return f.read()
    
    # Generate new key
    key = Fernet.generate_key()
    
    # Try to save to first writable location
    for path in APP_KEY_PATHS[1:]:  # Skip Docker secret path for writing
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(key)
            os.chmod(path, 0o600)  # Restrict permissions
            break
        except (OSError, PermissionError):
            continue
    
    return key

def encrypt(value: str) -> str:
    """Encrypt a string value."""
    key = _get_or_create_app_key()
    fernet = Fernet(key)
    encrypted = fernet.encrypt(value.encode())
    return base64.b64encode(encrypted).decode()

def decrypt(value_encrypted: str) -> str:
    """Decrypt an encrypted string value."""
    key = _get_or_create_app_key()
    fernet = Fernet(key)
    encrypted_bytes = base64.b64decode(value_encrypted.encode())
    decrypted = fernet.decrypt(encrypted_bytes)
    return decrypted.decode()