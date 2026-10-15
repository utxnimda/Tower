"""Current permanent worker attributes from immutable save/config arrays.

Equipment and active inner pills are read exactly as recorded in the save.
Event/expression numbers refer to the installed game's data.json/c3runtime.js.
Runtime buffs are unavailable in the save and are excluded. These attributes
do not include a facility's environment/technology or estimate output per second.
"""
from __future__ import annotations

import math
from functools import wraps


JOBS = ("伐木", "采矿", "种植", "锻造", "工艺", "炼丹", "建造", "研究", "操练")
DISPLAY_IDS = (*range(40, 69), 70, 72, 80, *range(90, 99), 1, 2, 3)
SLOT_NAMES = ("", "装备位 1", "装备位 2", "装备位 3", "装备位 4", "穿戴内丹", "宝物")
PROFESSION_LEVEL_CAP = 30  # constant SID 705033508603368; event sheet global
NOTE = "按当前存档实际穿戴及已激活炼化内丹复算常驻能力；不含无法从存档恢复的临时 BUFF。"


class UnsupportedStat(ValueError):
    """A dependency cannot be reconstructed; never replace it with a guess."""


def memoized(function):
    """Cache on this evaluator, never in a class cache retaining whole saves."""
    @wraps(function)
    def wrapped(self, *args):
        key = (function.__name__, args)
        if key not in self._cache:
            self._cache[key] = function(self, *args)
        return self._cache[key]
    return wrapped


def cell(data, x, y, z=0):
    if min(x, y, z) < 0:
        return 0
    try:
        value = data[x][y][z]
    except (IndexError, TypeError):
        return 0  # Construct Array.At outside its dimensions
    return 0 if value is None else value


def number(value):
    if value in (None, ""):
        return 0.0  # blank optional config cells compare as zero in game code
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise UnsupportedStat(f"无法读取数值 {value!r}") from exc
    if not math.isfinite(result):
        raise UnsupportedStat("属性含非有限数值")
    return result


def js_round(value):
    """Math.round, including negative halves; avoid Python ties-to-even."""
    lower = math.floor(value)
    return lower + int(value - lower >= 0.5)


def skill_base(level):
    # expression 5354, 基础技艺基数
    if level < 0:
        raise UnsupportedStat("技艺等级小于 0")
    return js_round(8 + 2 * math.pow(level, 1.2))


def equipment_attribute(attr, level, tier, quality, coefficient, kind, secondary=False):
    """装备主属性 10148..10168. Operation order follows generated JS."""
    if secondary:
        level, coefficient = 0, 100  # event 10150, not the item's normal level
    if tier < 1:
        raise UnsupportedStat("物品阶级小于 1")
    if 4 <= attr <= 7:
        # Combat attack is outside the worker panel and has another base curve.
        raise UnsupportedStat("战斗攻击属性不在工作能力计算范围")
    if 1 <= attr <= 3:
        return math.floor(((((100 * 0.003) * math.pow(tier, 1.6)) * (1 + 0.04 * level)) * 100 * (quality / 100)) * (coefficient / 100)) / 100
    if attr == 10:
        return math.floor(((tier * 0.2 + level * 0.01) * (quality / 100)) * 100) / 100
    if attr == 11:
        return math.floor((20 * (tier * 0.2 + level * 0.01)) * (quality / 100))
    if attr in (12, 34, 51, 29, 70):
        return math.floor(((2 + tier * 0.75 + level * 0.01) * (quality / 100)) * 100) / 100
    if attr in (13, 52):
        return math.floor(((8 + 3 * tier + level * 0.4) * (quality / 100)) * 100) / 100
    if 40 <= attr <= 48:
        return math.floor((((0.4 * skill_base((tier - 1) * 10 + 1)) * (1 + 0.04 * level)) * (quality / 100)) * (coefficient / 100) + level)
    if attr in (71, 72):
        return math.floor(((2 + tier * 0.75 + level * 0.1) * (quality / 100)) * 100) / 100
    if kind == "倍率":
        return math.floor(((tier + level * 0.1) * (quality / 100)) * 100) / 10000
    if kind not in ("整数", "小数", "百分比"):
        raise UnsupportedStat(f"属性 {attr} 的类型未知：{kind}")
    return math.floor(((4 + 1.5 * tier + level * 0.2) * (quality / 100)) * 100) / 100


def talent_attribute(attr, coefficient, level, kind):
    # events 10095..10106: branches are ordered, not inferred from units.
    if 1 <= attr <= 3:
        return level * (coefficient / 10)
    if attr in (12, 51, 34):
        return coefficient + 2 * level
    if attr in (13, 52, 11):
        return coefficient * level
    if attr == 10:
        return js_round((100 * level) * coefficient) / 10000
    if kind == "倍率":
        return math.floor(coefficient * math.pow(1.79, level - 1)) / 100
    if 104 <= attr <= 107:
        return level * (coefficient / 10)
    return math.floor(coefficient * math.pow(1.8, level - 1))


def set_attribute(coefficient, score, kind):
    # expressions 5224/5225/5227. 小数 has no return branch in this function.
    score = max(score, 4)
    if kind == "百分比":
        return ((coefficient / 10) / 4) * score
    if kind == "倍率":
        return js_round((1000 * ((coefficient / 1000) / 4)) * score) / 1000
    if kind == "整数":
        return (coefficient / 5) * score
    if kind == "小数":
        return 0
    raise UnsupportedStat(f"套装属性类型未知：{kind}")


class WorkerEvaluator:
    """One saved worker's current attributes, returned in the game's raw units."""
    def __init__(self, data, tables, role):
        self.data, self.tables, self.role = data, tables, int(role)
        self._active = set()
        self._cache = {}
        self.gear = []
        for slot in range(1, 7):
            item = int(self.saved(15 + slot))
            if item > 0:
                template = int(self.item(item, 1, 9))
                self.require("wp", template)
                self.gear.append((slot, item, template))
        self.pills = []
        for slot in range(1, 9):
            item = int(self.saved(2 * slot, 16))
            if item > 0 and self.saved(2 * slot - 1, 16) > 0:
                template = int(self.item(item, 1, 17))
                self.require("wp", template)
                self.pills.append((slot, item, template))

    def table(self, table, row, field):
        return cell(self.tables.get(table, []), int(row), field)

    def require(self, table, row):
        if row <= 0 or not self.table(table, row, 1):
            raise UnsupportedStat(f"缺少 {table} 模板 {row}")

    def saved(self, field, storage=2):
        return number(cell(self.data, self.role, field, storage))

    def item(self, item, field, storage):
        return number(cell(self.data, item, field, storage))

    def kind(self, attr):
        self.require("sx", attr)
        kind = self.table("sx", attr, 4)
        if kind not in ("整数", "小数", "百分比", "倍率"):
            raise UnsupportedStat(f"属性 {attr} 的类型未知：{kind}")
        return kind

    def item_attribute_values(self, item, template, storage, attr, affixes):
        for index in range(3):
            if self.table("wp", template, 8 + index) == attr:
                yield equipment_attribute(attr, self.item(item, 6, storage), self.item(item, 3, storage),
                                          self.item(item, 8, storage), number(self.table("wp", template, 11 + index)), self.kind(attr))
        if affixes:
            for index in range(6):
                if self.item(item, 9 + index, storage) == attr:
                    yield equipment_attribute(attr, self.item(item, 6, storage), self.item(item, 3, storage),
                                              self.item(item, 15 + index, storage), 100, self.kind(attr), True)

    @memoized
    def set_info(self, set_id):
        qualities = [0.0] * 6
        same = 0
        for _, item, template in self.gear:
            if self.table("wp", template, 16) != set_id:
                continue
            same += 1
            part = int(number(self.table("wp", template, 17)))
            if 1 <= part <= 6:
                qualities[part - 1] = max(qualities[part - 1], self.item(item, 23, 9))
        return sum(q > 0 for q in qualities), sum(qualities), max(1, min(same, 6))

    @memoized
    def equipment(self, attr):
        # Preserve per-slot addition/division order from 10255..10264.
        total = 0.0
        for _, item, template in self.gear:
            for value in self.item_attribute_values(item, template, 9, attr, True):
                total += value
            set_id = int(number(self.table("wp", template, 16)))
            if set_id > 0:
                self.require("tz", set_id)
                count, score, same = self.set_info(set_id)
                for threshold, field in ((2, 3), (4, 5), (6, 7)):
                    if count >= threshold and self.table("tz", set_id, field) == attr:
                        total += set_attribute(number(self.table("tz", set_id, field + 1)), score, self.kind(attr)) / same
        return total

    @memoized
    def inner_pills(self, attr):
        total = 0.0
        for _, item, template in self.pills:
            for value in self.item_attribute_values(item, template, 17, attr, False):
                total += value
        return total

    @memoized
    def talent(self, attr):
        total = 0.0
        for slot in range(1, 4):
            template = int(self.saved(slot, 12))
            level = self.saved(slot + 20, 12)
            if template > 0 and level > 0:
                self.require("tf", template)
                for index in range(3):
                    if self.table("tf", template, 7 + index) == attr:
                        total += talent_attribute(attr, number(self.table("tf", template, 10 + index)), level, self.kind(attr))
        return total

    @memoized
    def passive(self, attr):
        profession = int(self.saved(6))
        self.require("zy", profession)
        passive = int(number(self.table("zy", profession, 21)))
        if passive <= 0:
            return 0.0
        self.require("jn", passive)
        if self.table("jn", passive, 3) != "被动":
            return 0.0
        total = 0.0
        for index in range(3):
            coefficient = number(self.table("jn", passive, 37 + index))
            if self.table("jn", passive, 11 + index) == attr and coefficient > 0:
                divisor = 1000 if self.kind(attr) == "倍率" else 10
                total += js_round((100 * (coefficient / divisor)) * ((100 + self.attribute(15)) / 100)) / 100
        return total

    @memoized
    def profession_coefficient(self):
        profession = int(self.saved(6))
        self.require("zy", profession)
        level = max(1, self.saved(profession, 4))
        base = number(self.table("zy", profession, 16))
        if base <= 0:
            raise UnsupportedStat("职业工作系数配置缺失或无效")
        displayed = math.floor((base + (level - 1) * (base / (5 * PROFESSION_LEVEL_CAP))) * 10) / 10
        return 0.6 + 1.8 * math.pow(displayed / 100, 1.2)

    @memoized
    def attribute(self, attr):
        self.kind(attr)
        if attr in self._active:
            raise UnsupportedStat(f"属性 {attr} 出现循环依赖")
        self._active.add(attr)
        try:
            result = self._attribute(attr)
            if not math.isfinite(result):
                raise UnsupportedStat(f"属性 {attr} 计算结果非有限")
            return result
        finally:
            self._active.remove(attr)

    def _attribute(self, attr):
        # Ordered 属性计算 branches 10193..10222, with buff terms exactly zero.
        if 1 <= attr <= 3:
            aptitude = js_round(100 * (self.attribute(103 + attr) + self.saved(12 + attr) / 10)) / 100
            return aptitude + self.equipment(attr) + self.inner_pills(attr)
        if 40 <= attr <= 48:
            primary = {40: 1, 43: 1, 46: 1, 41: 2, 44: 2, 48: 2, 42: 3, 45: 3, 47: 3}[attr]
            return math.floor(((0.6 * skill_base(self.saved(attr - 9)) + self.equipment(attr)) * self.profession_coefficient()) * ((100 + self.attribute(primary)) / 100))
        if attr == 49:
            denominator = 1 * ((100 + self.attribute(70)) / 100)
            if denominator <= 0:
                raise UnsupportedStat("工速加成使工作间隔无效")
            return math.floor(100 * (1 / denominator)) / 100
        if attr == 50:
            return math.floor(150 * ((100 + self.attribute(68)) / 100)) + self.inner_pills(attr) + self.equipment(attr)
        if attr in (13, 52):
            return 150 + self.equipment(attr) + self.talent(attr) + self.inner_pills(attr) + self.passive(attr)
        if attr in (12, 51):
            return 5 + self.equipment(attr) + self.talent(attr) + self.inner_pills(attr) + self.passive(attr)
        if 77 <= attr <= 80:
            return 1 + self.talent(attr) + self.equipment(attr) + self.inner_pills(attr)
        if 90 <= attr <= 103:
            return 1 + self.talent(attr) + self.equipment(attr) + self.inner_pills(attr) + self.passive(attr)
        if 104 <= attr <= 106:
            return self.saved(attr - 94) / 10 + self.talent(attr) + self.inner_pills(attr) + self.equipment(attr)
        if attr == 10:
            return 1 + math.floor((100 * self.attribute(3)) * 0.01) / 100 + self.equipment(attr) + self.talent(attr) + self.inner_pills(attr) + self.passive(attr)
        if attr == 11:
            return 100 + self.equipment(attr) + self.talent(attr) + self.inner_pills(attr) + self.passive(attr)
        if 4 <= attr <= 9:
            raise UnsupportedStat("战斗属性不在工作能力计算范围")
        if number(self.table("sx", attr, 12)) > 0:
            raise UnsupportedStat(f"属性 {attr} 需要尚未支持的全局加成属性效果")
        return self.equipment(attr) + self.talent(attr) + self.inner_pills(attr) + self.passive(attr)


def display_unit(attr, kind):
    if attr == 49:
        return "秒/次"
    if attr == 50:
        return "格"
    if attr == 52 or kind == "倍率":
        return "×"
    if attr in (65, 66, 67):
        return "点/次"
    return "%" if kind == "百分比" else "点"


def display_value(attr, value):
    return value / 100 if attr in (50, 52) else value


def calculate_worker_stats(data, tables, role, expected_range=None):
    """Read current permanent attributes, isolating unsupported dependencies."""
    warnings = []
    try:
        evaluator = WorkerEvaluator(data, tables, role)
    except (UnsupportedStat, ValueError, OverflowError) as exc:
        warnings.append(f"当前穿戴：{exc}")
        evaluator = None

    rows = []
    for attr in DISPLAY_IDS:
        kind = cell(tables.get("sx", []), attr, 4)
        label = cell(tables.get("sx", []), attr, 1) or f"属性 {attr}"
        if attr == 49:
            label = "工作间隔"
        row = {"id": attr, "label": label, "unit": display_unit(attr, kind),
               "group": "primary" if attr < 4 else "work" if attr < 49 else "basic" if attr <= 52 else "bonus"}
        if 40 <= attr <= 48:
            row.update(jobType=JOBS[attr - 40], skillLevel=cell(data, role, attr - 9, 2), skillCap=cell(data, role, attr - 18, 2))
        if attr == 80:
            row["note"] = "生产职业工作时生效；此处显示原始常驻倍率。"
        if attr in (65, 66, 67):
            row["note"] = "对应工种单次工作量的固定追加，不等同于最终物品数量。"
        try:
            row["value"] = display_value(attr, evaluator.attribute(attr)) if evaluator else None
        except (UnsupportedStat, ValueError, OverflowError, ZeroDivisionError) as exc:
            row["value"] = None
            message = f"{label}：{exc}"
            if message not in warnings:
                warnings.append(message)
        rows.append(row)

    equipment = []
    equipped = evaluator
    if equipped:
        for slot, item, template in equipped.gear:
            direct = []
            attr_ids = [int(number(equipped.table("wp", template, 8 + i))) for i in range(3)]
            attr_ids += [int(equipped.item(item, 9 + i, 9)) for i in range(6)]
            for attr in dict.fromkeys(a for a in attr_ids if a in DISPLAY_IDS or a in (15, 104, 105, 106)):
                try:
                    direct_value = sum(equipped.item_attribute_values(item, template, 9, attr, True))
                    direct_value = display_value(attr, direct_value)
                except (UnsupportedStat, ValueError, OverflowError):
                    direct_value = None
                direct.append({"id": attr, "label": equipped.table("sx", attr, 1) or f"属性 {attr}",
                               "value": direct_value, "unit": display_unit(attr, equipped.table("sx", attr, 4))})
            equipment.append({"slot": slot, "slotName": SLOT_NAMES[slot], "itemId": f"9:{item}",
                              "template": template, "name": equipped.table("wp", template, 1),
                              "level": equipped.item(item, 6, 9), "tier": equipped.item(item, 3, 9),
                              "category": equipped.table("wp", template, 4),
                              "attributes": direct, "attributesScope": "工作相关直接属性；套装在人物总值中重算"})
    if expected_range is not None:
        radius = next(row["value"] for row in rows if row["id"] == 50)
        if radius is not None and not math.isclose(radius * 100, expected_range, rel_tol=0, abs_tol=1e-9):
            warnings.append("工作范围的两条读取路径结果不一致，当前范围未确认。")
            next(row for row in rows if row["id"] == 50)["value"] = None

    profession = int(number(cell(data, role, 6, 2)))
    dependencies = None
    if evaluator:
        def safely(function, *args):
            try:
                return function(*args)
            except (UnsupportedStat, ValueError, OverflowError, ZeroDivisionError):
                return None
        dependencies = {
            "primary": [safely(evaluator.attribute, a) for a in (1, 2, 3)],
            "growth": [safely(evaluator.attribute, a) for a in (104, 105, 106)],
            "professionWorkCoefficient": safely(evaluator.profession_coefficient),
            "equipmentSkill": [safely(evaluator.equipment, a) for a in range(40, 49)],
            "rawRangePixels": safely(evaluator.attribute, 50),
        }
    return {"status": "partial" if warnings else "exact", "scope": "permanent", "note": NOTE,
            "warnings": warnings, "level": cell(data, role, 3, 2),
            "professionLevel": cell(data, role, profession, 4), "rows": rows, "equipment": equipment,
            "sources": {"model": "construct-current-worker-v2", "runtimeBuffsIncluded": False,
                        "dependencies": dependencies,
                        "innerPills": [{"slot": slot, "itemId": f"17:{item}", "template": template,
                                        "name": equipped.table("wp", template, 1)}
                                       for slot, item, template in equipped.pills] if equipped else [],
                        "events": "10094–10288,10328–10331,10351–10360,10365;套装4875–4910"}}
