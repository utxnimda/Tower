"""Copy small, read-only sprite crops from the local game archive into this tool.

Run with a Python environment containing Pillow. No game save or game process is
accessed. The game's copyrighted art remains local and is not bundled for sharing.
"""
from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path
import sys
import zipfile

_VENDOR = Path(__file__).with_name("vendor")
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

try:
    from .game_data import character_asset_key
except ImportError:
    from game_data import character_asset_key


def _frames(project: list, object_name: str) -> dict:
    sprite = next(obj for obj in project[3] if obj[0] == object_name)
    return {str(anim[0]): anim[7][0] for anim in sprite[7] if anim[7]}


def extract(archive_path: str | Path | None = None, output_dir: str | Path | None = None) -> dict:
    from PIL import Image

    root = Path(__file__).resolve().parent
    archive_path = Path(archive_path) if archive_path else root.parent / "package.nw"
    out = Path(output_dir) if output_dir else root / "web" / "assets"
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    with zipfile.ZipFile(archive_path) as archive:
        project = json.loads(archive.read("data.json"))["project"]
        # Object names are more stable than Construct's numeric object indexes.
        animations = _frames(project, "地图建筑")
        sheets = {}

        def save_frame(key, file, frame, metadata):
            # Preserve the existing building sprites and their manifest entries.
            # Re-running character extraction should not churn unrelated assets.
            old = manifest.get(key)
            if old and (out / old["file"]).is_file():
                return
            sheet_path, _, x, y, width, height = frame[:6]
            if sheet_path not in sheets:
                sheets[sheet_path] = Image.open(io.BytesIO(archive.read(sheet_path))).convert("RGBA")
            rotated = bool(frame[6])
            packed_width, packed_height = (height, width) if rotated else (width, height)
            crop = sheets[sheet_path].crop((int(x), int(y), int(x + packed_width), int(y + packed_height)))
            if rotated:
                crop = crop.transpose(Image.Transpose.ROTATE_90)
            crop.thumbnail((360, 360), Image.Resampling.LANCZOS)
            crop.save(out / file, "WEBP", quality=86, method=4)
            manifest[key] = {"file": file, **metadata}

        for prefix, table_file in (("jz", "jz.json"), ("ss", "ss.json")):
            table = json.loads(archive.read(table_file))["data"]
            for template, row in enumerate(table):
                if not template or len(row) < 3:
                    continue
                anim_name = str(row[2][0])
                # Base forms suffice for planning. Several game animations add a
                # numeric upgrade suffix, so look for the first exported frame.
                frame = animations.get(anim_name) or animations.get(anim_name + "1")
                if frame is None:
                    frame = next((v for k, v in animations.items() if k.startswith(anim_name)), None)
                if frame is None:
                    continue
                file = f"{prefix}{template}.webp"
                save_frame(f"{prefix}{template}", file, frame, {"name": row[1][0], "animation": anim_name})
        # save[role,5,2] is the exact animation passed to 角色形象.SetAnim.
        # Export all appearances so a later save/new recruit uses the same map.
        for appearance, frame in _frames(project, "角色形象").items():
            key = character_asset_key(appearance)
            if key is None:
                continue
            digest = hashlib.sha256(appearance.encode("utf-8")).hexdigest()[:16]
            save_frame(key, f"character_{digest}.webp", frame,
                       {"name": appearance, "animation": appearance, "sourceObject": "角色形象", "frame": 0})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    result = extract()
    print(f"Extracted {len(result)} local sprite crops.")
