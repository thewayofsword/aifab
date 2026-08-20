"""Encryption utilities for AIFab - AES-256-GCM encryption."""
import os
import base64
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_key() -> bytes:
    """Generate a random 256-bit (32-byte) AES key."""
    return AESGCM.generate_key(bit_length=256)


def generate_msgkey() -> tuple:
    """Generate a 64-char msgkey and its SHA-256 hash with salt.
    Returns: (msgkey_plaintext, msgkey_hash, salt)
    """
    msgkey = base64.urlsafe_b64encode(os.urandom(48)).decode()[:64]
    salt = os.urandom(16).hex()
    hash_val = hashlib.sha256((msgkey + salt).encode()).hexdigest()
    return msgkey, hash_val, salt


def verify_msgkey(msgkey: str, stored_hash: str, salt: str) -> bool:
    """Verify a msgkey against stored hash and salt."""
    calc = hashlib.sha256((msgkey + salt).encode()).hexdigest()
    return calc == stored_hash


def encrypt_content(plaintext: str, key: bytes) -> bytes:
    """Encrypt plaintext string with AES-256-GCM.
    Returns: nonce + ciphertext (concatenated)
    """
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
    return nonce + ciphertext


def decrypt_content(cipher_blob: bytes, key: bytes) -> str:
    """Decrypt AES-256-GCM encrypted data.
    Input: nonce + ciphertext (concatenated)
    Returns: plaintext string
    """
    aesgcm = AESGCM(key)
    nonce = cipher_blob[:12]
    ciphertext = cipher_blob[12:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode('utf-8')


def encrypt_key_to_hex(key: bytes) -> str:
    """Convert binary key to hex string for storage."""
    return key.hex()


def decrypt_key_from_hex(hex_str: str) -> bytes:
    """Convert hex string back to binary key."""
    return bytes.fromhex(hex_str)
