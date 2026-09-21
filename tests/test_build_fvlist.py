import csv
import json
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).parents[1] / "scripts" / "build_fvlist.py"
SPEC = importlib.util.spec_from_file_location("build_fvlist", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class BuildFvListTests(unittest.TestCase):
    def test_previous_catalog_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "FVList.json"
            for data, expected in [
                ({"contentVersion": 1, "atlasBank": 0}, ("A", 1)),
                ({"contentVersion": 5, "atlasBank": 1}, ("B", 5)),
                ({"releaseId": 6, "atlasBank": "A"}, ("A", 6)),
            ]:
                with self.subTest(data=data):
                    path.write_text(json.dumps(data), encoding="utf-8-sig")
                    self.assertEqual(MODULE.read_current_catalog(path), expected)
            for data in [
                {"contentVersion": 1, "atlasBank": 2},
                {"contentVersion": 1, "atlasBank": True},
                {"releaseId": "1", "atlasBank": "A"},
            ]:
                with self.subTest(data=data):
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        MODULE.read_current_catalog(path)

    def write_csv(self, path, rows, header=False):
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            if header:
                writer.writerow(["worldId", "tag", "description"])
            writer.writerows(rows)

    def test_headerless_bom_and_tags(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "FVBase.csv"
            world_id = "wrld_a8cb7ac9-716a-4c74-8b17-aca507b4d8ab"
            self.write_csv(source, [[world_id, "Rest|Night", "Custom text"]])
            worlds = MODULE.read_base_csv(source)
            self.assertEqual(worlds[0].custom_tags, ["Rest", "Night"])

    def test_invalid_id_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "FVBase.csv"
            self.write_csv(source, [["bad", "x", "y"]])
            with self.assertRaisesRegex(ValueError, "invalid worldId"):
                MODULE.read_base_csv(source)

    def test_build_pages_initial_bank_and_square_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "FVBase.csv"
            rows = [[f"wrld_{i:08x}-1111-2222-3333-444444444444", "Rest", f"World {i}"] for i in range(17)]
            self.write_csv(source, rows, header=True)
            output = root / "out"

            def world_fetcher(world_id, user_agent):
                return {"name": "World", "authorName": "Tester", "capacity": 32, "recommendedCapacity": 16, "description": "API", "imageUrl": "https://example.invalid/x"}

            def image_fetcher(url, user_agent):
                return Image.new("RGB", (400, 200), "red")

            release = MODULE.build_release(source, None, output, "test-agent", world_fetcher=world_fetcher, image_fetcher=image_fetcher)
            self.assertEqual(release["atlasBank"], "A")
            self.assertEqual(release["detailPageCount"], 2)
            with Image.open(output / "FVPreviewA_00.jpg") as atlas:
                self.assertEqual(atlas.size, (2048, 2048))

    def test_malformed_current_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "FVBase.csv"
            self.write_csv(source, [["wrld_a8cb7ac9-716a-4c74-8b17-aca507b4d8ab", "Rest", "Text"]])
            current = root / "current.json"
            current.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "malformed"):
                MODULE.build_release(source, current, root / "out", "agent", world_fetcher=lambda *_: {}, image_fetcher=lambda *_: Image.new("RGB", (1, 1)))


if __name__ == "__main__":
    unittest.main()
