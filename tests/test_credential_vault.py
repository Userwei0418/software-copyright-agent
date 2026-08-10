import sqlite3
import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.credential_vault import CredentialVault
from software_copyright_agent.storage import Database


class CredentialVaultTests(unittest.TestCase):
    def test_round_trip_stores_only_ciphertext_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "app.db")
            vault = CredentialVault(database, root)
            provider_id = "11111111-1111-4111-8111-111111111111"
            secret = "sk-test-secret-that-must-not-appear-in-database"
            vault.store(provider_id, secret)
            self.assertEqual(vault.read(provider_id), secret)
            self.assertTrue(vault.has(provider_id))
            raw_database = (root / "app.db").read_bytes()
            self.assertNotIn(secret.encode("utf-8"), raw_database)
            with sqlite3.connect(root / "app.db") as connection:
                nonce, ciphertext = connection.execute(
                    "SELECT nonce, ciphertext FROM model_credentials WHERE provider_id = ?",
                    (provider_id,),
                ).fetchone()
            self.assertEqual(len(nonce), 12)
            self.assertNotEqual(ciphertext, secret.encode("utf-8"))
            vault.delete(provider_id)
            self.assertFalse(vault.has(provider_id))

    def test_ciphertext_is_bound_to_provider_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "app.db")
            vault = CredentialVault(database, root)
            first = "11111111-1111-4111-8111-111111111111"
            second = "22222222-2222-4222-8222-222222222222"
            vault.store(first, "sk-provider-bound-secret")
            database.initialize()
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT nonce, ciphertext FROM model_credentials WHERE provider_id = ?", (first,)
                ).fetchone()
                connection.execute(
                    "INSERT INTO model_credentials VALUES (?, ?, ?, 'now', 'now')",
                    (second, row["nonce"], row["ciphertext"]),
                )
            with self.assertRaisesRegex(ValueError, "decryption failed"):
                vault.read(second)


if __name__ == "__main__":
    unittest.main()
