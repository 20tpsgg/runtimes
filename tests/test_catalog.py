import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("java_matrix", ROOT / "scripts/java-matrix.py")
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


class JavaCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = matrix.load_catalog()

    def check_invalid(self, catalog):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "versions.json"
            path.write_text(json.dumps(catalog))
            with self.assertRaises(ValueError):
                matrix.load_catalog(path)

    def test_every_released_major_has_a_build(self):
        builds = matrix.build_matrix(self.catalog)["include"]
        self.assertEqual([build["java"] for build in builds], list(range(8, 28)))
        self.assertEqual([b["java"] for b in builds if b["support"] == "lts"], [8, 11, 17, 21, 25])
        self.assertEqual([b["java"] for b in builds if b["support"] == "current"], [27])
        for build in builds:
            self.assertTrue((ROOT / build["dockerfile"]).is_file())

    def test_architecture_support_matches_archive_records(self):
        builds = matrix.build_matrix(self.catalog)["include"]
        for version, build in zip(self.catalog["versions"], builds):
            self.assertIn("amd64", version["architectures"])
            self.assertEqual(build["arm64"], "arm64" in version["architectures"])
            if "archives" in version:
                for archive in version["archives"].values():
                    self.assertRegex(archive["version"], rf"^{version['major']}\.")
                    self.assertTrue(archive["source"].startswith("https://api."))

    def test_a_gap_or_duplicate_cannot_silently_omit_a_major(self):
        missing = copy.deepcopy(self.catalog)
        missing["versions"].pop(1)
        self.check_invalid(missing)
        duplicate = copy.deepcopy(self.catalog)
        duplicate["versions"][1] = duplicate["versions"][0]
        self.check_invalid(duplicate)

    def test_archived_jdks_require_checksums_and_architecture_records(self):
        for change in ["checksum", "architecture", "support"]:
            with self.subTest(change=change):
                catalog = copy.deepcopy(self.catalog)
                archived = next(v for v in catalog["versions"] if "archives" in v)
                if change == "checksum":
                    archived["archives"]["amd64"]["sha256"] = ""
                elif change == "architecture":
                    archived["architectures"].append("arm64")
                else:
                    archived["support"] = "lts"
                self.check_invalid(catalog)


if __name__ == "__main__":
    unittest.main()
