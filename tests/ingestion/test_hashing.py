import unittest

from src.ingestion.hashing import calculate_payload_hash, payload_hash


class HashingTests(unittest.TestCase):
    def test_hash_is_stable_for_mapping_order(self):
        first = {"valorUnitario": 6.25, "descricaoItem": "Arroz tipo 1"}
        second = {"descricaoItem": "Arroz tipo 1", "valorUnitario": 6.25}

        self.assertEqual(payload_hash(first), payload_hash(second))

    def test_hash_changes_when_raw_value_changes(self):
        first = {"valorUnitario": 6.25, "descricaoItem": "Arroz tipo 1"}
        second = {"valorUnitario": 7.0, "descricaoItem": "Arroz tipo 1"}

        self.assertNotEqual(payload_hash(first), payload_hash(second))

    def test_hash_preserves_unicode_and_nested_values(self):
        first = {"descrição": "Óleo", "itens": [{"valor": 6.25}]}
        second = {"itens": [{"valor": 6.25}], "descrição": "Óleo"}

        self.assertEqual(
            calculate_payload_hash(first), calculate_payload_hash(second)
        )


if __name__ == "__main__":
    unittest.main()
