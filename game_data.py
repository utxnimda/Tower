"""Read-only adapters for the game's Construct array saves and static data.

Only package.nw and the established save locations are opened, always for
reading.  No game process interaction, save write, or package extraction occurs.
All public coordinates are the game's coordinates minus 10000.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from .worker_stats import calculate_worker_stats
except ImportError:
    from worker_stats import calculate_worker_stats


SAVE_DIRECTORIES = ("savedir", "backup", "save")
SAVE_KEY_RE = re.compile(r"^(savedir|backup|save)/ea([1-3])save\.dat$")
ELEMENT_NAMES = ("金", "木", "水", "火", "土")
JOB_TYPES = ("伐木", "采矿", "种植", "锻造", "工艺", "炼丹", "研究", "操练", "建造")
TEMPLATE_TABLES = ("lw", "jz", "ss", "zy", "sx", "wp", "tf", "buff", "jn", "tz")
LIMIT_TABLES = ("jc", "kj")
MAP_SIZE = 50
COORDINATE_ORIGIN = 10000
READ_ATTEMPTS = 4
LOCAL_TIMEZONE = timezone(timedelta(hours=8))


class GameDataError(ValueError):
    """The selected data cannot be represented safely and completely."""


def _stat_signature(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def _stable_read(path: Path) -> tuple[bytes, tuple[int, int, int]]:
    """Read one immutable-in-memory version while the game keeps running."""
    last_error: Exception | None = None
    for attempt in range(READ_ATTEMPTS):
        try:
            before = _stat_signature(path)
            with path.open("rb") as stream:
                payload = stream.read()
            after = _stat_signature(path)
            if before == after and len(payload) == before[0]:
                return payload, after
            last_error = GameDataError(f"游戏正在更新文件：{path.name}")
        except OSError as exc:
            last_error = exc
        if attempt + 1 < READ_ATTEMPTS:
            time.sleep(0.06 * (attempt + 1))
    raise GameDataError(f"无法稳定读取 {path.name}，请稍后重试。") from last_error


def _read_json(path: Path) -> tuple[dict[str, Any], bytes, tuple[int, int, int]]:
    last_error: Exception | None = None
    for attempt in range(READ_ATTEMPTS):
        payload, signature = _stable_read(path)
        try:
            value = json.loads(payload.decode("utf-8-sig"))
            if not isinstance(value, dict):
                raise GameDataError(f"{path.name} 的根节点不是 JSON 对象。")
            return value, payload, signature
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < READ_ATTEMPTS:
                time.sleep(0.06 * (attempt + 1))
    raise GameDataError(f"{path.name} 不是完整的可读 JSON 存档。") from last_error


def _array(document: dict[str, Any], label: str) -> list:
    dimensions = document.get("size")
    data = document.get("data")
    if (
        document.get("c2array") is not True
        or not isinstance(dimensions, list)
        or len(dimensions) != 3
        or not all(isinstance(n, int) and n >= 0 for n in dimensions)
        or not isinstance(data, list)
    ):
        raise GameDataError(f"{label} 不是支持的 Construct 数组格式。")
    return data


def _cell(data: list, x: int, y: int, z: int = 0, default: Any = 0) -> Any:
    if min(x, y, z) < 0:
        return default
    try:
        value = data[x][y][z]
    except (IndexError, TypeError):
        return default
    return default if value is None else value


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        raise GameDataError("存档包含非有限数值，无法可靠读取。")
    return result


def _integer(value: Any, default: int = 0) -> int:
    return int(_number(value, default))


def _safe_path(game_dir: str | Path, relative: str) -> Path:
    root = Path(game_dir).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise GameDataError("数据路径超出所选游戏目录。")
    return path


def _clean_text(value: Any) -> str:
    return re.sub(r"\[/?[^\]]+\]", "", str(value or ""))


def _metadata(game_dir: str | Path, directory: str) -> tuple[list, str | None]:
    path = _safe_path(game_dir, f"{directory}/saveinfo.dat")
    if not path.is_file():
        return [], "没有对应的 saveinfo.dat，无法确认存档内嵌保存时间。"
    try:
        document, _, _ = _read_json(path)
        return _array(document, "saveinfo.dat"), None
    except (OSError, GameDataError) as exc:
        return [], str(exc)


def _save_descriptor(key: str, signature: tuple, metadata: list) -> dict[str, Any]:
    match = SAVE_KEY_RE.fullmatch(key)
    if match is None:
        raise GameDataError("仅支持 savedir、backup、save 中的 ea1/2/3save.dat。")
    directory, slot_text = match.groups()
    slot = int(slot_text)
    has_metadata = _number(_cell(metadata, slot, 0)) > 0
    embedded = _integer(_cell(metadata, slot, 7)) if has_metadata else 0
    modified = signature[1] // 1_000_000
    # Unknown embedded dates must not outrank known saves merely because Steam
    # refreshed a file's modification time. The fallback remains visible.
    game_date = _clean_text(_cell(metadata, slot, 5, default="")) if has_metadata else ""
    play_time = _clean_text(_cell(metadata, slot, 6, default="")) if has_metadata else ""
    kind = {"backup": "自动备份", "savedir": "手动存档", "save": "旧存档副本"}[directory]
    detail = " · ".join(part for part in (game_date, play_time) if part)
    label = f"{kind} {slot}" + (f" · {detail}" if detail else " · 保存信息缺失")
    return {
        "key": key,
        "label": label,
        "directory": directory,
        "slot": slot,
        "timestamp": embedded,
        "timestampSource": "saveinfo" if embedded else "unknown",
        "modifiedTimestamp": modified,
        "gameDate": game_date,
        "playTime": play_time,
        "savedAt": datetime.fromtimestamp(embedded / 1000, LOCAL_TIMEZONE).isoformat() if embedded else None,
        "size": signature[0],
    }


def list_saves(game_dir: str | Path) -> list[dict[str, Any]]:
    """List known slots, preferring their embedded save times over file mtime."""
    results = []
    for directory in SAVE_DIRECTORIES:
        metadata, warning = _metadata(game_dir, directory)
        for slot in range(1, 4):
            key = f"{directory}/ea{slot}save.dat"
            path = _safe_path(game_dir, key)
            if not path.is_file():
                continue
            try:
                signature = _stat_signature(path)
            except OSError:
                continue
            entry = _save_descriptor(key, signature, metadata)
            if warning:
                entry["warning"] = warning
            elif entry["timestamp"] == 0:
                entry["warning"] = "文件存在，但对应保存信息槽为空；日期不能由文件修改时间代替。"
            results.append(entry)
    results.sort(key=lambda item: (item["timestamp"], item["modifiedTimestamp"], item["key"]), reverse=True)
    return results


@lru_cache(maxsize=3)
def _load_templates_cached(package_path: str, signature: tuple) -> dict[str, list]:
    payload, actual_signature = _stable_read(Path(package_path))
    if actual_signature != signature:
        # The contents are still one stable version; they must not be cached
        # under an obsolete package signature.
        return _load_templates_cached(package_path, actual_signature)
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            tables = {
                name: _array(json.loads(archive.read(f"{name}.json").decode("utf-8-sig")), f"{name}.json")
                for name in TEMPLATE_TABLES
            }
            # Older/partial packages can still supply the existing game
            # data. A missing limit table must yield an unknown limit, never
            # confuse the number already built with the construction cap.
            for name in LIMIT_TABLES:
                if f"{name}.json" in archive.namelist():
                    tables[name] = _array(json.loads(archive.read(f"{name}.json").decode("utf-8-sig")), f"{name}.json")
            return tables
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GameDataError("package.nw 中缺少可读的建筑、灵物或人员属性配置。") from exc


def _template(templates: list, template_id: int, label: str) -> None:
    if template_id <= 0 or template_id >= len(templates):
        raise GameDataError(f"存档引用了未知{label}模板 {template_id}。")


def _pedestal_limit(data: list, templates: dict[str, list], placed: int) -> dict[str, Any]:
    """Reproduce building 67's construction cap, independently of aura range.

    package.nw/data.json: 建筑数量上限 event 2006 / expression 1590:
    10 + 加成属性效果(21). 进阶科技属性 event 4010 / expression 2576
    adds one BEFORE integer conversion and division for bonus 21. Its jc
    fields 4/5/6 are the step, percent-scaled amount and technology ID; field
    7 is the optional office bonus (zero in the audited game configuration).
    The technology's raw level is save[technology_id][4][0].
    """
    result = {"status": "unknown", "maximum": None, "placed": placed,
              "remaining": None, "formula": "", "base": 10}
    jc, kj = templates.get("jc", []), templates.get("kj", [])
    if len(jc) <= 21 or not kj:
        return {**result, "reason": "游戏配置缺少置物台上限或科技表，暂不能自动确定可建总数。"}

    def strict_number(value: Any, label: str, *, integer: bool = False) -> float:
        # These audited save/config cells are JSON numbers. Do not coerce
        # missing data, booleans or strings into a plausible construction cap.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GameDataError(f"{label}缺失或不是有效数值。")
        number = float(value)
        if not math.isfinite(number) or number < 0 or (integer and not number.is_integer()):
            raise GameDataError(f"{label}不是有效的非负{'整数' if integer else '有限数值'}。")
        return number

    try:
        if str(_cell(jc, 21, 3)) != "数值":
            raise GameDataError("置物台上限的加成类型不是已核实的数值类型。")
        office_id = strict_number(_cell(jc, 21, 7, default=None), "置物台上限的司职编号", integer=True)
        if office_id != 0:
            raise GameDataError("当前游戏为置物台上限配置了尚未支持的司职加成。")
        technology_id = int(strict_number(_cell(jc, 21, 6, default=None), "置物台上限的科技编号", integer=True))
        levels_per_bonus = strict_number(_cell(jc, 21, 4, default=None), "置物台上限的科技等级步长")
        bonus_per_step = strict_number(_cell(jc, 21, 5, default=None), "置物台上限的加成量") / 100
        if levels_per_bonus <= 0 or technology_id <= 0:
            raise GameDataError("置物台上限的科技加成配置无效。")
        _template(kj, technology_id, "科技")
        raw_level = _cell(data, technology_id, 4, 0, default=None)
        technology_level = int(strict_number(raw_level, "置物台上限对应的科技等级", integer=True))
        bonus = math.floor(int(technology_level + 1) / levels_per_bonus) * bonus_per_step
        maximum = 10 + bonus
        if not maximum.is_integer():
            raise GameDataError("置物台上限不是整数，当前配置超出已核实的规则。")
        maximum = int(maximum)
        return {
            **result, "status": "verified", "maximum": maximum,
            "remaining": max(0, maximum - placed),
            "technologyId": technology_id,
            "technologyName": str(_cell(kj, technology_id, 1)),
            "technologyLevel": technology_level, "technologyLevelOffset": 1,
            "levelsPerBonus": levels_per_bonus, "bonusPerStep": bonus_per_step,
            "technologyBonus": bonus,
            "formula": f"10 + floor(({technology_level} + 1) / {levels_per_bonus:g}) × {bonus_per_step:g} = {maximum}",
            "source": "建筑数量上限(67) → 10 + 加成属性效果(21) → jc[21] / save[科技编号][4][0]",
        }
    except (GameDataError, ValueError, OverflowError) as exc:
        return {**result, "reason": str(exc)}


def building_cells(x: int, y: int, width: int, height: int) -> list[dict[str, int]]:
    """1/3 tiles use a centre anchor; a 2-tile dimension starts at its anchor."""
    left = x - (width - 1) // 2
    top = y - (height - 1) // 2
    return [{"x": xx, "y": yy} for yy in range(top, top + height) for xx in range(left, left + width)]


def _game_round_tenth(value: float, tier: int = 0) -> float:
    # c3runtime expressions 3793..3798: Math.round((10 * raw) *
    # (1 + tier * 0.15)) / 10. Binary-float multiplication order is observable;
    # neither decimal arithmetic, reassociation nor an epsilon matches the game.
    scaled = (10 * float(value)) * (1 + tier * 0.15)
    integer = math.floor(scaled)
    return (integer + int(scaled - integer >= 0.5)) / 10


def character_asset_key(appearance: Any) -> str | None:
    """Use the saved Sprite animation name, never a profession-derived image."""
    return f"character:{appearance}" if appearance not in (None, "", 0) else None


def _worker_range_support(templates: dict[str, list]) -> list[str]:
    """Reject future configurations whose extra sources need a new evaluator.

    These three tables contain no range-related effects in the audited game.
    Runtime buff totals are not persisted, so a new such buff must make the
    range unknown instead of quietly assuming zero.
    """
    reasons = []
    for name, columns in (("buff", (3,)), ("jn", (11, 12, 13)), ("tz", (3, 5, 7))):
        for row in range(1, len(templates[name])):
            if any(_integer(_cell(templates[name], row, col)) in (50, 68) for col in columns):
                reasons.append(f"{name} 模板 {row} 含尚未支持的工作距离效果")
    if _number(_cell(templates["sx"], 68, 12)) > 0:
        reasons.append("工距加成含尚未支持的全局属性效果")
    if str(_cell(templates["sx"], 68, 4)) == "倍率":
        reasons.append("工距加成的配置单位已改变")
    return reasons


def _worker_range(data: list, slot: int, templates: dict[str, list],
                  unsupported: list[str]) -> tuple[float | None, dict[str, Any]]:
    """Reproduce attributes 50/68, including per-person equipment and talents.

    Generated expressions 5170, 5210, 5238, 5258/5260, 5272/5275 determine
    the order of floating-point operations. See worker_rules.md for evidence.
    """
    reasons = list(unsupported)
    wp, tf = templates["wp"], templates["tf"]
    components = {"talent68": 0.0, "equipment68": 0.0, "internalPill68": 0.0,
                  "buff68": 0.0, "passive68": 0.0,
                  "equipment50": 0.0, "internalPill50": 0.0, "buff50": 0.0}

    def main_value(item: int, storage: int, *, secondary: int | None = None) -> float:
        tier = _number(_cell(data, item, 3, storage))
        # The secondary-affix branch explicitly resets equipment level to 0.
        level = 0.0 if secondary is not None else _number(_cell(data, item, 6, storage))
        quality = _number(_cell(data, item, 14 + secondary if secondary else 8, storage))
        # Both 50 and 68 use event 10168 / expression 5210. Its formula does
        # not use the main-attribute template's setting coefficient.
        return math.floor((((4 + (1.5 * tier)) + (level * 0.2)) * (quality / 100)) * 100) / 100

    def add_main(item: int, storage: int, prefix: str) -> None:
        template_id = _integer(_cell(data, item, 1, storage))
        if template_id <= 0 or template_id >= len(wp):
            reasons.append(f"{prefix} 引用未知物品模板 {template_id}")
            return
        for column in (8, 9, 10):
            attribute = _integer(_cell(wp, template_id, column))
            if attribute in (50, 68):
                components[f"{prefix}{attribute}"] += main_value(item, storage)

    for equipment_slot in range(1, 7):
        item = _integer(_cell(data, slot, 15 + equipment_slot, 2))
        if item <= 0:
            continue
        add_main(item, 9, "equipment")
        for affix in range(1, 7):
            attribute = _integer(_cell(data, item, 8 + affix, 9))
            if attribute in (50, 68):
                components[f"equipment{attribute}"] += main_value(item, 9, secondary=affix)
    for pill_slot in range(1, 9):
        item = _integer(_cell(data, slot, 2 * pill_slot, 16))
        # event 10269 / expression 1923: state precedes the item reference.
        active = _number(_cell(data, slot, 2 * pill_slot - 1, 16)) > 0
        if item > 0 and active:
            add_main(item, 17, "internalPill")
    for talent_slot in range(1, 4):
        template_id = _integer(_cell(data, slot, talent_slot, 12))
        level = _number(_cell(data, slot, talent_slot + 20, 12))
        if template_id <= 0 or level <= 0:
            continue
        if template_id >= len(tf):
            reasons.append(f"未知天赋模板 {template_id}")
            continue
        for attribute_column in (7, 8, 9):
            if _integer(_cell(tf, template_id, attribute_column)) == 68:
                coefficient = _number(_cell(tf, template_id, attribute_column + 3))
                components["talent68"] += math.floor(coefficient * math.pow(1.8, level - 1))
    percent = (components["buff68"] + components["equipment68"] + components["talent68"]
               + components["internalPill68"] + components["passive68"])
    radius = (math.floor(150 * ((100 + percent) / 100)) + components["buff50"]
              + components["internalPill50"] + components["equipment50"])
    if not math.isfinite(radius) or radius < 0:
        reasons.append("工作距离不是有效的非负数值")
    evidence = {
        "status": "unknown" if reasons else "exact",
        "formula": "floor(150*((100+attr68)/100))+buff50+internalPill50+equipment50",
        "components": components, "attribute68": percent, "tilePixels": 100,
        "reasons": reasons,
    }
    return (None if reasons else radius), evidence


def _read_workers(data: list, templates: dict[str, list]) -> list[dict]:
    workers = []
    unsupported = _worker_range_support(templates)
    for slot in range(1, len(data)):
        # Map generation 7738: field0 > 0 and NOT(field53 > 0).
        # Top-list event 629 additionally excludes unrecruited field55 == 1;
        # conservatively exclude any positive unrecruited flag.
        if (_number(_cell(data, slot, 0, 2)) <= 0
                or _number(_cell(data, slot, 53, 2)) > 0
                or _number(_cell(data, slot, 55, 2)) > 0):
            continue
        raw_x, raw_y = (_integer(_cell(data, slot, field, 2)) for field in (7, 8))
        in_city = all(COORDINATE_ORIGIN < value <= COORDINATE_ORIGIN + MAP_SIZE
                      for value in (raw_x, raw_y))
        x, y = (raw_x - COORDINATE_ORIGIN, raw_y - COORDINATE_ORIGIN) if in_city else (None, None)
        state = str(_cell(data, slot, 9, 2, default="自动"))
        current_task = state if state in JOB_TYPES else None
        radius, evidence = _worker_range(data, slot, templates, unsupported)
        profession_id = _integer(_cell(data, slot, 6, 2))
        # Sprite 角色形象.SetAnim reads this exact value (events 631/8374,
        # generated expressions 677/4399), independently of profession/name.
        appearance = _cell(data, slot, 5, 2, default="")
        worker = {
            "id": f"w{slot}", "slot": slot, "name": str(_cell(data, slot, 1, 2)),
            "appearance": str(appearance) if appearance not in (None, "", 0) else None,
            "asset": character_asset_key(appearance),
            "professionId": profession_id,
            "profession": str(_cell(templates["zy"], profession_id, 1, default="未知职业")),
            "x": x, "y": y, "rawX": raw_x, "rawY": raw_y,
            "inMainCity": in_city, "deployed": raw_x > 0 and raw_y > 0,
            "settingState": state, "currentTaskType": current_task,
            "rangePixels": radius, "rangeTiles": radius / 100 if radius is not None else None,
            "rangeEvidence": evidence,
            "priorityTarget": {
                "rawX": _integer(_cell(data, slot, 57, 2)),
                "rawY": _integer(_cell(data, slot, 58, 2)),
            },
        }
        worker["stats"] = calculate_worker_stats(data, templates, slot, expected_range=radius)
        workers.append(worker)
    return workers


def _read_current_aura(snapshot: dict[str, Any]) -> None:
    """Attach current in-game aura to saved entities, with no layout scoring.

    The values reproduce the game's current centre-cell calculation and each
    placed item's tier-adjusted emission. No positions or items are changed.
    """
    items = {item["id"]: item for item in snapshot["items"]}
    emitters = []
    for building in snapshot["buildings"]:
        item = items.get(building["itemId"])
        if building["template"] != 67 or item is None:
            continue
        emission = {
            "qi": _game_round_tenth(item["qi"], building["tier"]),
            "elements": [_game_round_tenth(value, building["tier"]) for value in item["elements"]],
            "radius": 3,
        }
        building["emittedAura"] = emission
        emitters.append({"x": building["x"], "y": building["y"], "id": item["id"], **emission})

    def covering(x: int, y: int) -> list:
        return [e for e in emitters if abs(e["x"] - x) + abs(e["y"] - y) <= 3]

    for building in snapshot["buildings"]:
        if building["template"] == 66:
            matches = covering(building["x"], building["y"])
            building["currentAura"] = {
                "qi": round(sum(e["qi"] for e in matches), 10),
                "contributingItemIds": [e["id"] for e in matches],
            }
    for facility in snapshot["facilities"]:
        matches = covering(facility["x"], facility["y"])
        element = facility["element"]
        value = round(sum(e["elements"][element] for e in matches), 10)
        facility["currentAura"] = {
            "element": element, "value": value,
            "contributingItemIds": [e["id"] for e in matches],
        }


def load_snapshot(game_dir: str | Path, key: str) -> dict[str, Any]:
    """Load one stable save into an independent, JSON-serializable snapshot."""
    if not isinstance(key, str) or SAVE_KEY_RE.fullmatch(key) is None:
        raise GameDataError("仅支持 savedir、backup、save 中的 ea1/2/3save.dat。")
    path = _safe_path(game_dir, key)
    document, payload, signature = _read_json(path)
    data = _array(document, key)
    if document["size"][2] < 22:
        raise GameDataError("存档缺少本工具需要的设施/灵物数据层。")
    directory = key.split("/", 1)[0]
    metadata, metadata_warning = _metadata(game_dir, directory)
    descriptor = _save_descriptor(key, signature, metadata)
    warnings = [metadata_warning] if metadata_warning else []
    if descriptor["timestamp"] == 0:
        warnings.append("该存档没有可靠的内嵌保存时间，请核对所选槽位。")
    # Recheck the selected save after reading metadata, so a rotating automatic
    # backup cannot silently mix a new date with an older payload.
    if _stat_signature(path) != signature:
        raise GameDataError("读取期间该自动备份已轮换，请重新选择或重试。")
    package = _safe_path(game_dir, "package.nw")
    templates = _load_templates_cached(str(package), _stat_signature(package))
    lw, jz, ss = templates["lw"], templates["jz"], templates["ss"]
    digest = hashlib.sha256(payload).hexdigest()
    level = _integer(_cell(data, 2, 1, 0))
    bounds = {
        "xmin": max(1, 21 - 2 * math.floor((level + 1) / 2)),
        "xmax": min(MAP_SIZE, 30 + 2 * math.ceil((level + 1) / 2)),
        "ymin": 1,
        "ymax": min(MAP_SIZE, 10 + 2 * (level + 1)),
    }
    items = []
    for storage in (18, 19):
        for slot in range(1, len(data)):
            template_id = _integer(_cell(data, slot, 1, storage))
            if template_id <= 0:
                continue
            _template(lw, template_id, "灵物")
            owner = _integer(_cell(data, slot, 12, storage)) if storage == 19 else 0
            items.append({
                "id": f"{storage}:{slot}", "slot": slot, "storage": storage,
                "name": str(_cell(lw, template_id, 1)), "template": template_id,
                "level": _integer(_cell(data, slot, 2, storage)),
                "tier": _integer(_cell(data, slot, 3, storage)),
                "quality": _integer(_cell(data, slot, 4, storage)),
                "elements": [_number(_cell(data, slot, field, storage)) for field in range(5, 10)],
                "qi": _number(_cell(data, slot, 10, storage)),
                "placedBuildingId": owner or None,
            })
    buildings = []
    for slot in range(1, len(data)):
        template_id = _integer(_cell(data, slot, 1, 1))
        if template_id <= 0:
            continue
        _template(jz, template_id, "建筑")
        x, y = (_integer(_cell(data, slot, field, 1)) - COORDINATE_ORIGIN for field in (2, 3))
        width, height = (_integer(_cell(jz, template_id, field)) for field in (8, 9))
        if width < 1 or height < 1:
            raise GameDataError(f"建筑 {template_id} 的占地尺寸无效。")
        item_slot = _integer(_cell(data, slot, 9, 1)) if template_id == 67 else 0
        category = str(_cell(jz, template_id, 3))
        tier = _integer(_cell(data, slot, 5, 1))
        # Building relocation event 2151 requires save[building,7,1] <= 0.
        move_state = _number(_cell(data, slot, 7, 1), math.nan)
        move_known = math.isfinite(move_state)
        can_move = move_known and move_state <= 0
        buildings.append({
            "id": slot, "template": template_id, "name": str(_cell(jz, template_id, 1)),
            "x": x, "y": y, "width": width, "height": height,
            "tier": tier, "category": category,
            "canMove": can_move, "moveStateValue": move_state if move_known else None,
            "moveRestriction": "" if can_move else ("当前游戏状态暂不允许搬迁" if move_known else "建筑搬迁状态无法确认"),
            # Normal mode: event 2464, expressions 1726/1727. This is the
            # aggregate capacity across ALL facility templates of one owner.
            "facilityCapacity": tier * 4 + 4 if category == "资源" else tier + 2,
            "level": _integer(_cell(data, slot, 8, 1)),
            "templateTier": _integer(_cell(jz, template_id, 5)),
            "itemId": f"19:{item_slot}" if item_slot > 0 else None,
            "cells": building_cells(x, y, width, height),
        })
    building_by_id = {building["id"]: building for building in buildings}
    item_by_id = {item["id"]: item for item in items}
    for building in buildings:
        if building["itemId"]:
            item = item_by_id.get(building["itemId"])
            if item is None or item["placedBuildingId"] != building["id"]:
                raise GameDataError(f"置物台 {building['id']} 与灵物的双向绑定不一致，请重读稳定存档。")
    facility_positions: dict[int, list] = defaultdict(list)
    resources = []
    for x in range(1, MAP_SIZE + 1):
        for y in range(1, MAP_SIZE + 1):
            facility_slot = _integer(_cell(data, x + 150, y, 6))
            if facility_slot > 0:
                facility_positions[facility_slot].append({"x": x, "y": y})
            resource_id = _integer(_cell(data, x + 50, y, 6))
            if resource_id > 0:
                _template(jz, resource_id, "自然物")
                resources.append({"x": x, "y": y, "template": resource_id, "name": str(_cell(jz, resource_id, 1))})
    facilities = []
    for slot in range(1, len(data)):
        template_id = _integer(_cell(data, slot, 1, 21))
        if template_id <= 0:
            continue
        _template(ss, template_id, "设施")
        cells = facility_positions.get(slot, [])
        if len(cells) != 1:
            raise GameDataError(f"设施 {slot} 的坐标映射不是唯一单格，无法确定实际位置。")
        element = _integer(_cell(ss, template_id, 37)) - 1
        if not 0 <= element < 5:
            raise GameDataError(f"设施 {slot} 的五行类型不在本工具支持范围。")
        owner_id = _integer(_cell(data, slot, 2, 21))
        if owner_id not in building_by_id:
            raise GameDataError(f"设施 {slot} 的归属建筑 {owner_id} 不存在。")
        facilities.append({
            "id": f"f{slot}", "slot": slot, "template": template_id,
            "name": str(_cell(ss, template_id, 1)), **cells[0],
            "element": element, "ownerId": owner_id, "cells": cells,
            "jobType": str(_cell(ss, template_id, 4)),
        })
    groups: dict[tuple[int, int], dict] = {}
    for facility in facilities:
        group_id = facility["template"], facility["ownerId"]
        if group_id not in groups:
            groups[group_id] = {
                "key": f"{group_id[0]}:{group_id[1]}", "template": group_id[0],
                "ownerId": group_id[1], "name": facility["name"],
                "ownerName": building_by_id[group_id[1]]["name"],
                "ownerCapacity": building_by_id[group_id[1]]["facilityCapacity"],
                "element": facility["element"], "jobType": facility["jobType"], "count": 0,
            }
        groups[group_id]["count"] += 1
    workers = _read_workers(data, templates)
    for worker in workers:
        if worker["inMainCity"] and worker["rangePixels"] is None:
            warnings.append(f"{worker['name']} 的个人工作范围尚不能精确还原。")
    pedestal_limit = _pedestal_limit(data, templates, sum(b["template"] == 67 for b in buildings))
    if pedestal_limit["status"] != "verified":
        warnings.append(pedestal_limit["reason"])
    snapshot = {
        "id": digest[:20], "path": str(path), "key": key,
        "label": descriptor["label"], "timestamp": descriptor["timestamp"],
        "gameDate": descriptor["gameDate"], "playTime": descriptor["playTime"],
        "savedAt": descriptor["savedAt"], "sha256": digest,
        "bounds": bounds, "mapBounds": {"xmin": 1, "xmax": 50, "ymin": 1, "ymax": 50},
        "cityLevel": level, "coordinateOrigin": COORDINATE_ORIGIN,
        "pedestalLimit": pedestal_limit,
        "items": items, "buildings": buildings, "facilities": facilities,
        "facilityGroups": list(groups.values()), "resources": resources,
        "workers": workers, "jobTypes": list(JOB_TYPES),
        "warnings": warnings, "schemaVersion": 2,
        "source": {"key": key, "size": signature[0], "modifiedTimestamp": signature[1] // 1_000_000,
                   "saveDimensions": document["size"], "packagePath": str(package), "readOnly": True},
        "counts": {"items": len(items), "inventoryItems": sum(i["storage"] == 18 for i in items),
                   "placedItems": sum(i["storage"] == 19 for i in items), "buildings": len(buildings),
                   "facilities": len(facilities), "pedestals": sum(b["template"] == 67 for b in buildings),
                   "workers": len(workers), "mainCityWorkers": sum(w["inMainCity"] for w in workers)},
    }
    _read_current_aura(snapshot)
    return snapshot
