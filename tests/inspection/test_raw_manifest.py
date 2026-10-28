import unittest

from src.inspection.raw_manifest import validate_manifest, validate_source
from src.schema import Manifest, SourceConfig


class RawManifestInspectionTests(unittest.TestCase):
    def test_accepts_current_source_shape(self):
        source = SourceConfig(
            id="pncp_pca_rj",
            base="https://pncp.example",
            endpoint="/api/consulta/v1/pca/atualizacao",
            natureza="agregada",
            resource_type="pca",
            key_fields=("idPcaPncp",),
        )

        self.assertEqual(validate_source(source), [])

    def test_rejects_unknown_natureza_and_duplicate_source_id(self):
        source = SourceConfig(
            id="duplicada",
            base="https://pncp.example",
            endpoint="/api",
            natureza="agregadaen",
            key_fields=("id",),
        )

        errors = validate_manifest(Manifest((source, source)))

        self.assertTrue(any("natureza" in error for error in errors))
        self.assertTrue(any("duplicados" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
