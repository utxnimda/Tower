"""Character animation mapping with tiny disposable local sprite fixtures."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    from .extract_assets import extract
    from .game_data import character_asset_key
except ImportError:
    from extract_assets import extract
    from game_data import character_asset_key

from PIL import Image


class CharacterAssetTests(unittest.TestCase):
    def test_saved_animation_names_map_to_first_frames_and_preserve_buildings(self):
        with tempfile.TemporaryDirectory(prefix="lingqi-portraits-") as folder:
            root = Path(folder)
            out = root / "assets"
            out.mkdir()
            old_file = out / "jz1.webp"
            old_file.write_bytes(b"existing-building-must-stay-identical")
            old_entry = {"file": "jz1.webp", "name": "原建筑", "animation": "建筑"}
            (out / "manifest.json").write_text(json.dumps({"jz1": old_entry}), encoding="utf-8")
            sheet = Image.new("RGBA", (6, 2), (255, 0, 0, 255))
            for x in range(2, 4):
                for y in range(2):
                    sheet.putpixel((x, y), (0, 255, 0, 255))
            for x in range(4, 6):
                for y in range(2):
                    sheet.putpixel((x, y), (0, 0, 255, 255))
            buf = io.BytesIO()
            sheet.save(buf, "PNG")
            frame = lambda x: ["images/test.png", 0, x, 0, 2, 2, False]
            anim = lambda name, frames: [name, 5, False, 1, 0, False, 1, frames]
            sprite = lambda name, animations: [name, 12, False, [], 0, 0, None, animations]
            project = [None, None, None, [
                sprite("地图建筑", [anim("建筑", [frame(4)])]),
                sprite("角色形象", [anim("潘金莲", [frame(0), frame(4)]), anim("B6", [frame(2)])]),
            ]]
            with zipfile.ZipFile(root / "package.nw", "w") as archive:
                archive.writestr("data.json", json.dumps({"project": project}))
                archive.writestr("images/test.png", buf.getvalue())
                archive.writestr("jz.json", json.dumps({"data": [[[0], [""], [""]], [[1], ["新名"], ["建筑"]]]}))
                archive.writestr("ss.json", json.dumps({"data": []}))
            manifest = extract(root / "package.nw", out)
            self.assertEqual(manifest["jz1"], old_entry)
            self.assertEqual(old_file.read_bytes(), b"existing-building-must-stay-identical")
            self.assertEqual(set(manifest), {"jz1", "character:潘金莲", "character:B6"})
            portrait = manifest[character_asset_key("潘金莲")]
            self.assertEqual(portrait["animation"], "潘金莲")
            self.assertEqual(portrait["sourceObject"], "角色形象")
            self.assertTrue(portrait["file"].isascii())
            with Image.open(out / portrait["file"]) as image:
                red, green, blue = image.convert("RGB").getpixel((0, 0))
                self.assertGreater(red, 230)
                self.assertLess(green + blue, 25)  # first frame, not the blue second frame
            with Image.open(out / manifest["character:B6"]["file"]) as image:
                red, green, blue = image.convert("RGB").getpixel((0, 0))
                self.assertGreater(green, 230)
                self.assertLess(red + blue, 25)

    def test_missing_appearance_does_not_guess_a_character(self):
        for value in (None, "", 0):
            self.assertIsNone(character_asset_key(value))
        self.assertEqual(character_asset_key("14"), "character:14")


if __name__ == "__main__":
    unittest.main()
