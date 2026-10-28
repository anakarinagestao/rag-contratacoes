import unittest

from src.ingestion.change_detector import asset_key, detect_change
from src.ingestion.hashing import payload_hash


class ChangeDetectorTests(unittest.TestCase):
    def test_classifies_new_unchanged_and_updated(self):
        digest = payload_hash({"id": "1", "valor": 10})

        self.assertEqual(detect_change(None, digest), "NEW")
        self.assertEqual(detect_change(digest, digest), "UNCHANGED")
        self.assertEqual(detect_change(digest, "different"), "UPDATED")

    def test_uses_key_fields_and_hash_fallback(self):
        payload = {"numeroControlePNCP": "abc", "valor": 10}

        self.assertEqual(asset_key(payload, ("numeroControlePNCP",)), "abc")
        self.assertEqual(
            asset_key(payload, ("missing",)), payload_hash(payload)
        )


if __name__ == "__main__":
    unittest.main()
