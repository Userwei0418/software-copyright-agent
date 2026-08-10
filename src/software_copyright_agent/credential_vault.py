import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .service import utc_now
from .storage import Database


class CredentialVault:
    def __init__(self, database: Database, data_dir: Path) -> None:
        self._database = database
        self._key_path = data_dir / ".credential-master-key"

    def _master_key(self) -> bytes:
        if self._key_path.exists():
            key = self._key_path.read_bytes()
            if len(key) != 32:
                raise ValueError("Credential master key is invalid")
            return key
        key = AESGCM.generate_key(bit_length=256)
        descriptor = os.open(str(self._key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, key)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return key

    def store(self, provider_id: str, api_key: str) -> None:
        if not api_key or len(api_key) < 8:
            raise ValueError("API Key is too short")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._master_key()).encrypt(
            nonce, api_key.encode("utf-8"), provider_id.encode("utf-8")
        )
        now = utc_now()
        self._database.initialize()
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO model_credentials(provider_id, nonce, ciphertext, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET nonce=excluded.nonce,
                ciphertext=excluded.ciphertext, updated_at=excluded.updated_at""",
                (provider_id, nonce, ciphertext, now, now),
            )
        if self.read(provider_id) != api_key:
            raise ValueError("Credential verification failed after storage")

    def read(self, provider_id: str) -> str:
        self._database.initialize()
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT nonce, ciphertext FROM model_credentials WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Credential not found")
        try:
            plaintext = AESGCM(self._master_key()).decrypt(
                bytes(row["nonce"]), bytes(row["ciphertext"]), provider_id.encode("utf-8")
            )
            return plaintext.decode("utf-8")
        except Exception as error:
            raise ValueError("Credential decryption failed") from error

    def has(self, provider_id: str) -> bool:
        try:
            return bool(self.read(provider_id))
        except ValueError as error:
            if str(error) == "Credential not found":
                return False
            raise

    def delete(self, provider_id: str) -> None:
        self._database.initialize()
        with self._database.connect() as connection:
            connection.execute("DELETE FROM model_credentials WHERE provider_id = ?", (provider_id,))
