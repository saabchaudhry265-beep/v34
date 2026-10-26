"""Settings and save files (mixin for Game)."""
import json
import math
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from .common import *
from . import common as C
from .content import QUESTS, SKILLS, META
from . import world as W
from .models import Item, Enemy, KINDS

VALID_AREAS = {"zone0", "zone1", "zone2"}


class SaveMixin:
    def load_settings(self):
        self.settings = {"resolution": 1, "fullscreen": False, "volume": 0.4, "music": True}
        try:
            data = json.loads(C.SETTINGS_FILE.read_text(encoding="utf8"))
            idx, vol = data.get("resolution"), data.get("volume")
            if type(idx) is int and 0 <= idx < len(RESOLUTIONS):
                self.settings["resolution"] = idx
            if type(data.get("fullscreen")) is bool:
                self.settings["fullscreen"] = data["fullscreen"]
            if type(data.get("music")) is bool:
                self.settings["music"] = data["music"]
            if isinstance(vol, (int, float)) and not isinstance(vol, bool) and math.isfinite(vol):
                self.settings["volume"] = clamp(vol, 0, 1)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save_settings(self):
        try:
            C.DATA.mkdir(parents=True, exist_ok=True)
            tmp = C.SETTINGS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf8")
            os.replace(tmp, C.SETTINGS_FILE)
        except OSError:
            self.notify("Не удалось сохранить настройки.", RED)

    def load_profile(self):
        self.meta = {"shards": 0, "best": 0, "runs": 0, "upgrades": {}}
        try:
            data = json.loads(C.PROFILE_FILE.read_text(encoding="utf8"))
            for key in ("shards", "best", "runs"):
                if type(data.get(key)) is int and 0 <= data[key] <= 10 ** 9:
                    self.meta[key] = data[key]
            for key, rank in data.get("upgrades", {}).items():
                if key in META and type(rank) is int and 0 <= rank <= META[key][2]:
                    self.meta["upgrades"][key] = rank
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save_profile(self):
        try:
            C.DATA.mkdir(parents=True, exist_ok=True)
            tmp = C.PROFILE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf8")
            os.replace(tmp, C.PROFILE_FILE)
        except OSError:
            self.notify("Не удалось сохранить прогресс святилища.", RED)

    def archive_save(self):
        """Keep the previous progress in a separate file before a new game."""
        if not C.SAVE_FILE.exists():
            return True
        try:
            C.DATA.mkdir(parents=True, exist_ok=True)
            shutil.copy2(C.SAVE_FILE, C.DATA / "eldoria_v34.archive.json")
            return True
        except OSError:
            self.notify("Не удалось архивировать старое сохранение. Новая игра отменена.", RED)
            return False

    # Save decoding builds a complete temporary session before touching live data.
    def decode_save(self, data):
        if not isinstance(data, dict) or data.get("version") not in (32, 33, VERSION):
            raise ValueError("Неподдерживаемая версия сохранения")
        legacy = data["version"] < VERSION
        raw = data.get("player")
        if not isinstance(raw, dict):
            raise ValueError("Повреждён герой")
        p = self.new_player()
        limits = {"x": (0, WW), "y": (0, WH), "hp": (0, 1000000), "level": (1, 1000), "xp": (0, 1e100),
                  "xpnext": (1, 1e100), "str": (1, 10000), "vit": (1, 10000), "agi": (1, 10000),
                  "points": (0, 100000), "gold": (0, 1e12), "potions": (0, 8), "kills": (0, 1e12),
                  "face_x": (-1.01, 1.01), "face_y": (-1.01, 1.01), "return_x": (0, WW), "return_y": (0, WH)}
        ints = {"level", "xp", "xpnext", "str", "vit", "agi", "points", "gold", "potions", "kills"}
        for key, (lo, hi) in limits.items():
            value = raw[key]
            number(value, lo, hi)
            if key in ints and type(value) is not int:
                raise ValueError("Ожидалось целое число: " + key)
            p[key] = value
        for key, size in (("bosses", 3), ("dungeons", 3 if legacy else 6)):
            values = raw[key]
            if not isinstance(values, list) or len(values) != size or any(type(v) is not bool for v in values):
                raise ValueError("Повреждён прогресс")
            p[key] = values[:]
        if legacy:
            p["dungeons"] = [p["dungeons"][0], False, p["dungeons"][1], False, p["dungeons"][2], False]
            p["passes"] = [p["bosses"][0], p["bosses"][1]]
        else:
            passes = raw["passes"]
            if not isinstance(passes, list) or len(passes) != 2 or any(type(v) is not bool for v in passes):
                raise ValueError("Повреждены проходы")
            p["passes"] = passes[:]
        values = raw["zonekills"]
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError("Повреждены счётчики")
        for value in values:
            number(value, 0, 1e12)
            if type(value) is not int:
                raise ValueError("Повреждены счётчики")
        p["zonekills"] = values[:]
        chests = raw["chests"]
        if not isinstance(chests, list) or len(chests) != 3:
            raise ValueError("Повреждены сундуки")
        for row in chests:
            if (not isinstance(row, list) or len(row) > 3 or any(type(i) is not int or i not in (0, 1, 2) for i in row)
                    or len(set(row)) != len(row)):
                raise ValueError("Повреждены сундуки")
        p["chests"] = [row[:] for row in chests]
        if type(raw["complete"]) is not bool:
            raise ValueError("Повреждён финал")
        p["complete"] = raw["complete"]
        if p["complete"] and not all(p["bosses"]):
            raise ValueError("Противоречивый прогресс")
        equip = raw["equip"]
        if not isinstance(equip, dict) or set(equip) != set(SLOTS):
            raise ValueError("Повреждена экипировка")
        for slot in SLOTS:
            item = Item.restore(equip[slot]) if equip[slot] is not None else None
            if item and item.slot != slot:
                raise ValueError("Предмет в неправильном слоте")
            p["equip"][slot] = item
        inv = raw["inv"]
        if not isinstance(inv, list) or len(inv) > 60:
            raise ValueError("Повреждён инвентарь")
        p["inv"] = [Item.restore(item) for item in inv]

        if legacy:
            p["sp"] = max(0, (p["level"] - 1) * 2 - sum(1 for _ in ()))
            if data["version"] == 33:
                p["name"] = str(raw.get("name", "Странник"))[:14] or "Странник"
                p["skills"] = {k: v for k, v in raw.get("skills", {}).items() if k in SKILLS and type(v) is int and 0 <= v <= SKILLS[k]["max"]}
                p["sp"] = raw.get("sp", 0) if type(raw.get("sp", 0)) is int else 0
                p["seen"] = [k for k in raw.get("seen", []) if k in KINDS][:12]
                p["quests"] = {k: v for k, v in raw.get("quests", {}).items() if k in QUESTS and isinstance(v, dict)
                               and type(v.get("s")) is int and type(v.get("n")) is int and 0 <= v["s"] <= 3 and 0 <= v["n"] <= 999}
        else:
            if type(raw["char"]) is not int or not 0 <= raw["char"] <= 2:
                raise ValueError("Повреждён герой")
            p["char"] = raw["char"]
            name = raw["name"]
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 14:
                raise ValueError("Повреждено имя")
            p["name"] = name
            number(raw["sp"], 0, 10000)
            if type(raw["sp"]) is not int:
                raise ValueError("Повреждены очки навыков")
            p["sp"] = raw["sp"]
            skills = raw["skills"]
            if not isinstance(skills, dict) or len(skills) > 40:
                raise ValueError("Повреждены навыки")
            for sid, rank in skills.items():
                if sid not in SKILLS or type(rank) is not int or not 0 <= rank <= SKILLS[sid]["max"]:
                    raise ValueError("Повреждены навыки")
            p["skills"] = dict(skills)
            quests = raw["quests"]
            if not isinstance(quests, dict) or len(quests) > 40:
                raise ValueError("Повреждены задания")
            for qid, st in quests.items():
                if (qid not in QUESTS or not isinstance(st, dict) or type(st.get("s")) is not int
                        or not 0 <= st["s"] <= 3 or type(st.get("n")) is not int or not 0 <= st["n"] <= 999):
                    raise ValueError("Повреждены задания")
            p["quests"] = {k: {"s": v["s"], "n": v["n"]} for k, v in quests.items()}
            seen = raw["seen"]
            if not isinstance(seen, list) or len(seen) > 20 or any(s not in KINDS for s in seen):
                raise ValueError("Повреждён бестиарий")
            p["seen"] = list(dict.fromkeys(seen))
        maxhp = int((80 + p["vit"] * 8 + sum(it.hp for it in p["equip"].values() if it)) * (1 + 0.06 * p["skills"].get("tough", 0)))
        p["hp"] = min(p["hp"], maxhp)
        fx, fy = direction(p["face_x"], p["face_y"])
        p["face_x"], p["face_y"] = fx, fy

        area = data["area"]
        if legacy:
            # The map changed in v33: keep the hero, regenerate the world.
            tail = str(area)[-1:]
            zone = 2 if area == "final" else int(tail) if tail in ("0", "1", "2") else 0
            camp = W.zone_camp(zone)
            p["x"], p["y"] = camp[0], camp[1] + 90
            p["return_x"], p["return_y"] = p["x"], p["y"]
            return p, "zone" + str(zone), {}
        raw_areas = data["areas"]
        if not isinstance(raw_areas, dict) or len(raw_areas) > 7:
            raise ValueError("Повреждён мир")
        if area not in VALID_AREAS or area not in raw_areas:
            raise ValueError("Неизвестная локация")
        areas = {}
        for key, value in raw_areas.items():
            if key not in VALID_AREAS or not isinstance(value, dict):
                raise ValueError("Повреждена локация")
            enemies, drops = value["enemies"], value["drops"]
            if not isinstance(enemies, list) or len(enemies) > 80:
                raise ValueError("Повреждены враги")
            if not isinstance(drops, list) or len(drops) > 2000:
                raise ValueError("Повреждена добыча")
            if type(value["reward"]) is not bool:
                raise ValueError("Повреждена награда")
            parsed = {"enemies": [Enemy.restore(e) for e in enemies], "drops": [], "reward": value["reward"]}
            zi = 2 if key == "final" else int(key[-1])
            for e in parsed["enemies"]:
                if e.zone != zi:
                    raise ValueError("Враг в неправильном регионе")
            for drop in drops:
                if not isinstance(drop, dict):
                    raise ValueError("Повреждена добыча")
                parsed["drops"].append({"x": number(drop["x"], 0, WW), "y": number(drop["y"], 0, WH),
                                        "item": Item.restore(drop["item"]), "age": 9.0})
            areas[key] = parsed
        return p, area, areas

    def read_save(self, path):
        if path.stat().st_size > 8_000_000:
            raise ValueError("Слишком большой файл")
        data = json.loads(path.read_text(encoding="utf8"))
        self.decode_save(data)
        return data

    def save_game(self, quiet=False):
        if self.mode == "abyss":
            if not quiet:
                self.notify("В Бездне сохранения отключены: забег идёт до смерти.", MUTED)
            return False
        if not self.session:
            return False
        p = dict(self.p)
        area = self.area
        if self.area_kind() != "zone":
            area = self.return_area
            p["x"], p["y"] = self.p["return_x"], self.p["return_y"]
        p["equip"] = {slot: asdict(item) if item else None for slot, item in self.p["equip"].items()}
        p["inv"] = [asdict(item) for item in self.p["inv"]]
        areas = {}
        for key, value in self.areas.items():
            if not key.startswith("zone"):
                continue
            areas[key] = {"enemies": [asdict(e) for e in value["enemies"] if e.hp > 0],
                          "drops": [{"x": d["x"], "y": d["y"], "item": asdict(d["item"])} for d in value["drops"]],
                          "reward": value["reward"]}
        data = {"version": VERSION, "player": p, "area": area, "areas": areas}
        tmp = Path(str(C.SAVE_FILE) + ".tmp")
        backup = Path(str(C.SAVE_FILE) + ".bak")
        try:
            self.decode_save(data)
            C.DATA.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf8") as file:
                json.dump(data, file, ensure_ascii=False, indent=1, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            if C.SAVE_FILE.exists() and (not quiet or self.time - self.last_backup > 120):
                try:
                    self.read_save(C.SAVE_FILE)
                    backup_tmp = Path(str(backup) + ".tmp")
                    shutil.copy2(C.SAVE_FILE, backup_tmp)
                    os.replace(backup_tmp, backup)
                    self.last_backup = self.time
                except (OSError, ValueError, TypeError, KeyError, OverflowError):
                    pass  # never replace a valid backup with a corrupted main save
            os.replace(tmp, C.SAVE_FILE)
            if not quiet:
                self.notify("Игра сохранена.")
            return True
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as ex:
            self.notify("Ошибка сохранения: " + str(ex)[:100], RED)
            return False

    def save_candidates(self):
        return [C.SAVE_FILE, Path(str(C.SAVE_FILE) + ".bak"), C.LEGACY_SAVE, Path(str(C.LEGACY_SAVE) + ".bak"),
                C.DATA / "eldoria_v32.json"]

    def has_save(self):
        return any(p.exists() for p in self.save_candidates())

    def load_game(self):
        errors = []
        for path in self.save_candidates():
            if not path.exists():
                continue
            try:
                data = self.read_save(path)
                p, area, areas = self.decode_save(data)
                self.p, self.area, self.areas = p, area, areas
                self.worlds, self.terrain_cache, self.mini_cache = {}, {}, {}
                self.mode, self.relics, self.scale_mul = "campaign", {}, 1.0
                self.return_area = self.area
                self.build_worlds()
                self.ensure_area(self.area)
                self.reset_runtime()
                self.session = True
                self.confirm_new = False
                self.inv_page = 0
                self.autosave_time = 0
                self.pending_save = False
                self.set_state("dead" if p["hp"] <= 0 else "play")
                if p["complete"] and area == "final" and p["hp"] > 0:
                    self.set_state("victory")
                if data["version"] < VERSION:
                    self.notify("Старое сохранение перенесено: герой и прогресс сохранены, мир создан заново.")
                elif path.name.endswith(".bak"):
                    self.notify("Загружена резервная копия.")
                else:
                    self.notify("Сохранение загружено.")
                return
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as ex:
                errors.append(str(ex))
        self.notify("Сохранение повреждено: " + errors[-1][:90] if errors else "Сохранений пока нет.", RED)
