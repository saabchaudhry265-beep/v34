"""Тропы Эльдории — action RPG. Запуск: python game.py"""
import math
import os
import random
import shutil
from array import array

import pygame

from tropy import common as C
from tropy.common import *
from tropy.content import *
from tropy.content import legacy_char
from tropy.models import Item, Enemy
from tropy.combat import CombatMixin
from tropy.ai import AIMixin
from tropy.render import RenderMixin
from tropy.ui import UIMixin
from tropy.saves import SaveMixin
from tropy import world as W

SPRITES = (
    ["enemy_slime", "enemy_wolf", "enemy_golem", "enemy_bat",
     "enemy_archer", "enemy_shaman", "enemy_finalboss", "boss_forest", "boss_ruins", "boss_ash", "enemy_spore",
     "enemy_wisp", "enemy_ghost", "enemy_scarab", "enemy_imp", "enemy_magma", "enemy_cultist"]
    + ["npc_" + n for n in NPCS] + ["char%d_%s" % (c, v) for c in range(3) for v in ("down", "up", "side")]
)


class Game(SaveMixin, CombatMixin, AIMixin, RenderMixin, UIMixin):
    def __init__(self):
        pygame.mixer.pre_init(22050, -16, 1, 512)
        pygame.init()
        pygame.display.set_caption("Тропы Эльдории")
        try:
            pygame.display.set_icon(pygame.image.load(str(ASSETS_PATH() / "icon.png")))
        except (OSError, pygame.error):
            pass
        self.clock = pygame.time.Clock()
        self.canvas = pygame.Surface((VW, VH))
        self.fonts = {size: pygame.font.SysFont("dejavusans,arial", size, bold=size in (21, 32)) for size in (14, 17, 21, 32)}
        self.load_settings()
        self.load_profile()
        self.window = None
        self.viewport = pygame.Rect(0, 0, VW, VH)
        self.apply_display()
        self.sprites = {}
        for name in SPRITES:
            try:
                self.sprites[name] = pygame.image.load(str(ASSETS_PATH() / (name + ".png"))).convert_alpha()
            except (OSError, pygame.error):
                pass
        self.sprite_cache = {}
        self.sounds = self.make_sounds()
        self.music = self.make_music()
        self.music_channel = None

        self.state = "menu"
        self.back_state = "menu"
        self.session = False
        self.running = True
        self.worlds, self.terrain_cache, self.mini_cache = {}, {}, {}
        self.areas = {}
        self.area = "zone0"
        self.buttons = []
        self.inv_page = 0
        self.discard_index = None
        self.confirm_new = False
        self.toast, self.toast_color, self.toast_time = "", GOLD, 0
        self.autosave_time = 0
        self.pending_save = False
        self.time = 0.0
        self.vignette = None
        self.menu_index = 0
        self.last_backup = -1e9
        self.journal_tab = 0
        self.creator = {"char": 0, "mode": "campaign"}
        self.cut = None
        self.mode = "campaign"
        self.relics, self.abyss_run, self.run_stats = {}, {"floor": 1, "zi": 0, "shards": 0}, {"kills": 0}
        self.scale_mul = 1.0
        self.return_area = "zone0"
        self.boon = None
        self.arena = None
        self.last_immune = self.last_seal_msg = -10.0
        self.p = self.new_player()
        self.reset_runtime()
        self.menu_index = self.first_menu_index()

    # ------------------------------------------------------------ setup
    def reset_runtime(self):
        self.projectiles, self.hazards, self.particles, self.floaters = [], [], [], []
        self.slashes, self.bolts, self.meteors, self.bullets = [], [], [], []
        self.ripples, self.ghosts, self.deaths = [], [], []
        self.buffs = {}
        self.skill_cd = {sid: 0.0 for sid, s in SKILLS.items() if "key" in s}
        self.shield = 0.0
        self.shake = self.flash = self.hitstop = self.slowmo = self.hurt_t = 0.0
        self.swing = self.lunge = 0.0
        self.swing_special = False
        self.moving = False
        self.ghost_timer = self.step_timer = 0.0
        self.banner = None
        self.fx = []
        self.last_hit = None
        self.orb_t = 0.0
        self.orb_cd = {}
        self.chaining = self.blasting = False
        self.rock_t = 0.0
        self.transition = None
        self.dialog = None
        self.levelfx = None
        self.seen_bosses = set()
        self.p["invuln"] = self.p["dash"] = 0
        if self.area in self.areas:
            for e in self.areas[self.area]["enemies"]:
                e.wind = e.dash = 0
                e.cd = max(e.cd, 0.8)

    def transient_clear(self):
        self.projectiles.clear(); self.hazards.clear(); self.particles.clear(); self.floaters.clear()
        self.slashes.clear(); self.bolts.clear(); self.meteors.clear(); self.bullets.clear()
        self.ripples.clear(); self.ghosts.clear(); self.deaths.clear()
        self.swing = self.lunge = 0.0
        self.p["invuln"] = self.p["dash"] = 0
        if self.area in self.areas:
            for e in self.current()["enemies"]:
                e.wind = e.dash = 0
                e.kx = e.ky = 0.0
                e.cd = max(e.cd, 0.8)

    def new_player(self):
        return {
            "x": CAMP[0], "y": CAMP[1] + 90, "hp": 120, "level": 1, "xp": 0, "xpnext": 40, "char": 0,
            "str": 5, "vit": 5, "agi": 5, "points": 0, "sp": 0, "gold": 35, "potions": 3, "kills": 0,
            "zonekills": [0, 0, 0], "bosses": [False] * 3, "chests": [[], [], []], "dungeons": [False] * 6, "passes": [False] * 2,
            "complete": False, "equip": {"weapon": None, "armor": None, "amulet": None}, "inv": [],
            "face_x": 0.0, "face_y": 1.0, "attack_cd": 0.0, "skill_cd": 0.0, "pulse_cd": 0.0,
            "dash_cd": 0.0, "dash": 0.0, "invuln": 0.0, "return_x": CAMP[0], "return_y": CAMP[1],
            "name": "Странник", "skills": {}, "quests": {}, "seen": [],
        }

    def make_world(self, key):
        return W.build_zone(int(key[-1]))

    def char(self):
        ch = CHARACTERS[self.p.get("char", 0)]
        return ch if self.p["name"] == ch["name"] else legacy_char(self.p["name"], ch["gender"])

    def apply_character(self, ch):
        p = self.p
        p["char"], p["name"] = ch["id"], ch["name"]
        p["str"], p["vit"], p["agi"] = ch["stats"]
        p["skills"][ch["skill"][0]] = ch["skill"][1]
        kind = ch["weapon"]
        names = {"sword": "Походный меч", "spear": "Походное копьё", "staff": "Походный посох"}
        p["equip"]["weapon"] = Item(names[kind], "weapon", 0, power=4, weapon=kind)
        extra = ch["item"]
        p["equip"][extra["slot"]] = Item(extra["name"], extra["slot"], 1, extra.get("power", 0), extra.get("armor", 0),
                                         extra.get("crit", 0), extra.get("hp", 0))
        p["hp"] = self.max_hp()

    def build_worlds(self):
        for key in self.areas:
            self.worlds[key] = self.make_world(key)

    # ------------------------------------------------- sound and display
    def make_sounds(self):
        result = {}
        info = pygame.mixer.get_init()
        if not info or info[1] != -16:
            return result
        rate, _, channels = info
        for name, freq, duration, noise in (("hit", 220, 0.055, 0.45), ("hurt", 115, 0.11, 0.35), ("loot", 660, 0.10, 0.0),
                                            ("level", 880, 0.25, 0.0), ("dash", 350, 0.07, 0.5), ("win", 1046, 0.4, 0.0)):
            samples = array("h")
            count = int(rate * duration)
            for i in range(count):
                fade = (1 - i / count) ** 2
                tone = math.sin(i * math.tau * freq / rate)
                sample = int((tone * (1 - noise) + random.uniform(-1, 1) * noise) * 6500 * fade)
                for _ in range(channels):
                    samples.append(sample)
            try:
                result[name] = pygame.mixer.Sound(buffer=samples.tobytes())
            except pygame.error:
                pass
        return result

    def make_music(self):
        info = pygame.mixer.get_init()
        if not info or info[1] != -16:
            return None
        rate, _, channels = info
        notes = [220.0, 261.6, 329.6, 392.0, 329.6, 261.6, 196.0, 246.9,
                 293.7, 392.0, 293.7, 246.9, 220.0, 261.6, 329.6, 261.6]
        count = int(rate * 0.5)
        samples = array("h")
        for freq in notes:
            for i in range(count):
                t = i / rate
                env = min(1.0, i / 200) * min(1.0, (count - i) / 300) * math.exp(-3 * t)
                wave = math.sin(math.tau * freq * t) + 0.4 * math.sin(math.tau * freq * 2 * t)
                value = int(wave * env * 2600)
                for _ in range(channels):
                    samples.append(value)
        try:
            return pygame.mixer.Sound(buffer=samples.tobytes())
        except pygame.error:
            return None

    def update_music(self):
        if not self.music:
            return
        if self.settings["music"] and self.settings["volume"] > 0:
            if self.music_channel is None or not self.music_channel.get_busy():
                self.music_channel = self.music.play(loops=-1)
            if self.music_channel:
                self.music_channel.set_volume(self.settings["volume"] * 0.35)
        elif self.music_channel:
            self.music_channel.stop()
            self.music_channel = None

    def sound(self, name):
        snd = self.sounds.get(name)
        if snd:
            snd.set_volume(self.settings["volume"])
            snd.play()

    def apply_display(self):
        full = self.settings["fullscreen"]
        flags = pygame.FULLSCREEN if full else pygame.RESIZABLE
        size = (0, 0) if full else RESOLUTIONS[self.settings["resolution"]]
        try:
            self.window = pygame.display.set_mode(size, flags)
        except pygame.error:
            self.settings["fullscreen"] = False
            self.settings["resolution"] = 1
            self.window = pygame.display.set_mode((1000, 700), pygame.RESIZABLE)
        self.update_viewport()

    def update_viewport(self):
        self.window = pygame.display.get_surface() or self.window
        w, h = self.window.get_size()
        scale = min(w / VW, h / VH)
        sw, sh = max(1, int(VW * scale)), max(1, int(VH * scale))
        self.viewport = pygame.Rect((w - sw) // 2, (h - sh) // 2, sw, sh)

    def mouse(self, pos=None):
        pos = pos or pygame.mouse.get_pos()
        if not self.viewport.collidepoint(pos):
            return None
        return ((pos[0] - self.viewport.x) * VW / self.viewport.w, (pos[1] - self.viewport.y) * VH / self.viewport.h)

    def notify(self, message, color=GOLD):
        self.toast, self.toast_color, self.toast_time = message, color, 5.5

    def set_state(self, state):
        self.state = state
        self.buttons = []
        self.discard_index = None
        if state == "menu":
            self.menu_index = self.first_menu_index()
        else:
            self.confirm_new = False

    # --------------------------------------------------------- new game
    def start_new(self):
        if self.session and not self.confirm_new:
            self.confirm_new = True
            self.notify("Нажми «Новая игра» ещё раз: текущая сессия будет заменена.")
            return
        if self.has_save() and not self.session and not self.confirm_new:
            self.confirm_new = True
            self.notify("Есть сохранение. Ещё одно нажатие начнёт новую игру; старое уйдёт в архив.")
            return
        self.confirm_new = False
        self.creator = {"char": 0, "mode": "campaign"}
        self.back_state = "menu"
        self.set_state("creator")

    def start_abyss_menu(self):
        self.creator = {"char": 0, "mode": "abyss"}
        self.back_state = "menu"
        self.set_state("creator")

    def finish_creator(self):
        ch = CHARACTERS[self.creator["char"]]
        if self.creator.get("mode") == "abyss":
            self.start_abyss(ch)
            return
        if self.session and self.mode == "campaign":
            self.save_game(quiet=True)
        if not self.archive_save():
            return
        self.mode, self.relics, self.scale_mul = "campaign", {}, 1.0
        self.p = self.new_player()
        self.apply_character(ch)
        self.areas, self.worlds, self.terrain_cache, self.mini_cache = {}, {}, {}, {}
        self.area = self.return_area = "zone0"
        camp = W.zone_camp(0)
        self.p["x"], self.p["y"] = camp[0], camp[1] + 90
        self.p["return_x"], self.p["return_y"] = self.p["x"], self.p["y"]
        self.session = True
        self.ensure_area(self.area)
        self.reset_runtime()
        self.pending_save = True

        def begin():
            self.back_state = "play"
            self.set_state("help")
            self.notify("Поговори со Старейшиной у костра (знак «!»).")
        self.start_cutscene(INTRO, begin)

    def start_cutscene(self, slides, done):
        self.cut = {"slides": slides, "i": 0, "t": 0.0, "done": done}
        self.set_state("cutscene")

    def advance_cutscene(self):
        cut = self.cut
        text = fmt(cut["slides"][cut["i"]]["text"], self.char())
        if cut["t"] * 38 < len(text):
            cut["t"] = len(text) / 38
            return
        cut["i"] += 1
        cut["t"] = 0.0
        if cut["i"] >= len(cut["slides"]):
            self.finish_cutscene()

    def finish_cutscene(self):
        cb = self.cut["done"]
        self.cut = None
        cb()

    # ----------------------------------------------------------- quests
    def quest_state(self, qid):
        return self.p["quests"].get(qid, {"s": 0, "n": 0})

    def quest_progress(self, qid):
        q = QUESTS[qid]
        if q["kind"] == "boss":
            done = self.p["complete"] if q["target"] == 3 else self.p["bosses"][q["target"]]
            return (1 if done else 0), q["n"]
        return min(self.quest_state(qid)["n"], q["n"]), q["n"]

    def quest_status(self, qid):
        q = QUESTS[qid]
        s = self.quest_state(qid)["s"]
        if s == 3:
            return "done"
        if s == 2:
            return "ready"
        if s == 1:
            have, need = self.quest_progress(qid)
            return "ready" if have >= need else "active"
        if "after" in q and self.quest_status(q["after"]) != "done":
            return "locked"
        if q.get("boss_req") is not None and not self.p["bosses"][q["boss_req"]]:
            return "locked"
        return "available"

    def quest_event(self, kind, target, zone=None):
        for qid, q in QUESTS.items():
            st = self.quest_state(qid)
            if st["s"] != 1:
                continue
            if q["kind"] == "kill" and kind == "kill" and q["target"] == target and st["n"] < q["n"]:
                st["n"] += 1
                self.p["quests"][qid] = st
                self.floater(self.p["x"], self.p["y"] - 60, "{} {}/{}".format(q["title"], st["n"], q["n"]), GREEN)
            elif q["kind"] == "collect" and kind == "kill" and zone == q["zone"] and st["n"] < q["n"] and random.random() < q["chance"]:
                st["n"] += 1
                self.p["quests"][qid] = st
                self.floater(self.p["x"], self.p["y"] - 60, "+1 " + q["target"], GOLD)
            if self.quest_status(qid) == "ready" and not st.get("notified"):
                self.notify("Задание «{}» выполнено! Вернись к {}.".format(q["title"], NPCS[q["giver"]]["name"]), GREEN)

    def npc_marker(self, npc):
        statuses = [self.quest_status(qid) for qid, q in QUESTS.items() if q["giver"] == npc]
        if "ready" in statuses:
            return "?"
        if "available" in statuses:
            return "!"
        if "active" in statuses:
            return "."
        return None

    def open_dialog(self, npc):
        quests = [(qid, self.quest_status(qid)) for qid, q in QUESTS.items() if q["giver"] == npc]
        ready = next((qid for qid, st in quests if st == "ready"), None)
        avail = next((qid for qid, st in quests if st == "available"), None)
        active = next((qid for qid, st in quests if st == "active"), None)
        close = ("Закрыть", self.close_dialog)
        if ready:
            text, buttons = QUESTS[ready]["done"], [("Забрать награду", lambda: self.claim_quest(ready))]
        elif avail:
            q = QUESTS[avail]
            text = "{}: {}".format(q["title"], q["text"])
            buttons = [("Принять", lambda: self.accept_quest(avail)), ("Позже", self.close_dialog)]
        elif active:
            have, need = self.quest_progress(active)
            text = "Как успехи? «{}»: {}/{}. {}".format(QUESTS[active]["title"], have, need, QUESTS[active]["text"])
            buttons = [close]
        else:
            text, buttons = random.choice(NPC_IDLE[npc]), [close]
        self.dialog = {"npc": npc, "text": fmt(text, self.char()), "buttons": buttons, "t": 0.0}
        self.set_state("dialog")

    def close_dialog(self):
        self.dialog = None
        self.set_state("play")

    def accept_quest(self, qid):
        self.p["quests"][qid] = {"s": 1, "n": 0}
        self.notify("Задание принято: " + QUESTS[qid]["title"], GOLD)
        self.sound("loot")
        self.pending_save = True
        self.close_dialog()

    def claim_quest(self, qid):
        q = QUESTS[qid]
        r = q["reward"]
        self.p["quests"][qid] = {"s": 3, "n": self.quest_state(qid)["n"]}
        self.p["gold"] += r.get("gold", 0)
        self.p["xp"] += int(r.get("xp", 0) * (1 + 0.08 * self.rank("wisdom")))
        self.p["potions"] = min(8, self.p["potions"] + r.get("potions", 0))
        if r.get("sp"):
            self.p["sp"] += r["sp"]
        if r.get("item"):
            self.add_item(self.make_item(NPCS[q["giver"]]["zone"], True, min_rarity=r["item"]))
        self.notify("Награда за «{}» получена!".format(q["title"]), GREEN)
        self.sparkle(self.p["x"], self.p["y"], GOLD, 20)
        self.sound("win")
        self.level_up()
        self.pending_save = True
        self.close_dialog()

    # ---------------------------------------------------------- skills
    def req_ok(self, sid):
        req = SKILLS[sid]["req"]
        return req is None or self.rank(req[0]) >= req[1]

    def can_buy(self, sid):
        s = SKILLS[sid]
        return self.rank(sid) < s["max"] and self.req_ok(sid) and self.p["sp"] >= s.get("cost", 1)

    def buy_skill(self, sid):
        if self.can_buy(sid):
            self.p["sp"] -= SKILLS[sid].get("cost", 1)
            self.p["skills"][sid] = self.rank(sid) + 1
            self.hp_cap()
            self.sound("level")
            self.notify("Изучено: " + SKILLS[sid]["name"], GOLD)
            self.pending_save = True

    def hp_cap(self):
        self.p["hp"] = min(self.p["hp"], self.max_hp())

    # ------------------------------------------------------------ smith
    def upgrade_cost(self, item):
        return 35 * (item.plus + 1) * (1 + item.tier)

    def upgrade_item(self, slot):
        item = self.p["equip"][slot]
        if not item or item.plus >= 5:
            return
        cost = self.upgrade_cost(item)
        if self.p["gold"] < cost:
            self.notify("Не хватает золота.", RED)
            return
        self.p["gold"] -= cost
        item.plus += 1
        if slot == "weapon":
            item.power += 2 + item.tier
            item.crit += 1
        elif slot == "armor":
            item.armor += 1
            item.hp += 4
        else:
            item.power += 1
            item.crit += 1
            item.hp += 5
        self.sound("win")
        self.notify("Улучшено: " + item.title, item.color)
        self.pending_save = True

    # ----------------------------------------------------- world / POIs
    def available_pois(self):
        zi = self.zone_index()
        result = []
        for poi in self.world().pois:
            if poi["kind"] == "chest" and poi["id"] in self.p["chests"][zi]:
                continue
            if poi["kind"] == "portal" and self.p["complete"]:
                continue
            result.append(poi)
        area = self.current()
        if area["reward"]:
            rx, ry = area.get("reward_pos", (WW / 2, WH / 2))
            result.append({"kind": "reward", "x": rx, "y": ry})
        return result

    def nearest_interaction(self):
        options = []
        for poi in self.available_pois():
            d = distance(self.p["x"], self.p["y"], poi["x"], poi["y"])
            if d < (95 if poi["kind"] in ("gate_next", "gate_back", "portal") else 80):
                options.append((d, "poi", poi))
        for drop in self.current()["drops"]:
            d = distance(self.p["x"], self.p["y"], drop["x"], drop["y"])
            if d < 65 and drop.get("age", 9) > 0.5:
                options.append((d, "drop", drop))
        return min(options, key=lambda q: q[0]) if options else None

    def go(self, callback, duration=1.1):
        if not self.transition:
            self.transition = {"t": 0.0, "dur": duration, "cb": callback, "fired": False}

    def interact(self):
        result = self.nearest_interaction()
        if not result or self.transition:
            return
        _, kind, obj = result
        zi = self.zone_index()
        if kind == "drop":
            self.fly_item(obj)
            if self.add_item(obj["item"]):
                self.current()["drops"].remove(obj)
            return
        what = obj["kind"]
        if what == "camp":
            self.p["hp"] = self.max_hp()
            self.p["potions"] = max(self.p["potions"], 3)
            self.sparkle(self.p["x"], self.p["y"], (255, 190, 90), 14)
            self.notify("Отдых у костра: здоровье восстановлено, зелья пополнены до 3.")
            self.save_game()
        elif what == "npc":
            npc = obj["npc"]
            if npc == "merchant":
                self.set_state("shop")
            elif npc == "smith":
                self.set_state("smith")
            else:
                self.open_dialog(npc)
        elif what == "chest":
            self.p["chests"][zi].append(obj["id"])
            self.p["gold"] += int((12 + zi * 8) * (1 + 0.25 * self.mod_count("gold")))
            self.burst(obj["x"], obj["y"], GOLD, 24)
            self.shake = max(self.shake, 3)
            self.add_drop(obj["x"], obj["y"], self.make_item(zi))
            self.sound("loot")
            self.pending_save = True
        elif what == "entrance":
            idx = DUNGEON_IDS.index(obj["dg"])
            if self.p["dungeons"][idx]:
                self.notify("Это подземелье уже пройдено, награда получена.")
            else:
                self.go(lambda: self.enter(obj["dg"]))
        elif what in ("exit", "exit_back", "exit_fwd"):
            self.go(lambda: self.leave_through(what))
        elif what == "gate_next":
            nxt = zi + 1
            if not self.p["bosses"][zi]:
                self.notify("Путь закрыт. Победи хранителя: " + ZONES[zi]["boss"] + ".", RED)
            elif self.p["level"] < ZONES[nxt]["level"]:
                self.notify("Нужен уровень {} для региона «{}».".format(ZONES[nxt]["level"], ZONES[nxt]["name"]), RED)
            else:
                self.go(lambda: self.enter_pass(zi, True))
        elif what == "gate_back":
            self.go(lambda: self.enter_pass(zi - 1, False))
        elif what == "portal":
            if all(self.p["bosses"]):
                self.go(lambda: self.enter("final"))
            else:
                self.notify("Портал спит, пока не разрушены все три печати.", RED)
        elif what == "reward":
            self.claim_reward()
        elif what == "altar":
            self.world().pois.remove(obj)
            self.open_boon("Алтарь реликвий", self.relic_offers(3), "free")
        elif what == "stall":
            price = 40 + 12 * self.abyss_run["floor"]
            self.open_boon("Торговец Бездны", self.relic_offers(3, price), "shop")
        elif what == "stairs":
            self.go(self.next_floor)

    def claim_reward(self):
        zi = self.zone_index()
        area = self.current()
        area["reward"] = False
        if self.area_kind() == "dungeon":
            idx = DUNGEON_IDS.index(self.area)
            self.p["dungeons"][idx] = True
            item = self.make_unique(self.area)
            self.p["gold"] += int((90 + zi * 40) * (1 + 0.25 * self.mod_count("gold")))
            self.p["xp"] += int((90 + zi * 40) * (1 + 0.08 * self.rank("wisdom")))
            self.add_drop(*area.get("reward_pos", (WW / 2, WH / 2)), item)
            self.notify("Уникальная добыча подземелья: " + item.name, GOLD)
            self.p["sp"] += 1
            self.level_up()
        self.shake = max(self.shake, 5)
        self.pending_save = True

    def fly_item(self, drop):
        self.sparkle(drop["x"], drop["y"], drop["item"].color, 12, 10)

    def go_zone(self, zi, arrive_kind):
        self.transient_clear()
        self.area = self.return_area = "zone" + str(zi)
        self.ensure_area(self.area)
        poi = next(p for p in self.world().pois if p["kind"] == arrive_kind)
        camp = self.world().camp_xy
        dx, dy = direction(camp[0] - poi["x"], camp[1] - poi["y"])
        self.p["x"], self.p["y"] = poi["x"] + dx * 120, poi["y"] + dy * 120
        self.p["return_x"], self.p["return_y"] = self.p["x"], self.p["y"]
        self.pending_save = True
        self.banner = {"t": 0.0, "title": ZONES[zi]["name"], "sub": "Рекомендуемый уровень: " + str(ZONES[zi]["level"]), "color": GREEN}
        self.notify("Переход: " + ZONES[zi]["name"])

    def enter_pass(self, index, forward):
        self.return_area = self.area
        self.p["return_x"], self.p["return_y"] = self.p["x"], self.p["y"]
        self.transient_clear()
        key = "pass%d" % index
        self.areas.pop(key, None)
        self.worlds.pop(key, None)
        self.terrain_cache.pop(key, None)
        self.area = key
        self.ensure_area(key)
        room = self.world().rooms[0 if forward else -1]
        self.p["x"], self.p["y"] = room["center"][0], room["center"][1] + 40
        self.banner = {"t": 0.0, "title": PASSES[index]["name"], "sub": PASSES[index]["desc"], "color": PURPLE}
        self.pending_save = True

    def enter(self, key):
        self.return_area = self.area
        self.p["return_x"], self.p["return_y"] = self.p["x"], self.p["y"]
        self.transient_clear()
        self.areas.pop(key, None)
        self.worlds.pop(key, None)
        self.terrain_cache.pop(key, None)
        self.area = key
        self.ensure_area(key)
        if key == "final":
            self.p["x"], self.p["y"] = WW / 2, 1500
            self.notify("Сердце Пустоты ждёт. Выход находится позади.")
        else:
            c = self.world().rooms[0]["center"]
            self.p["x"], self.p["y"] = c[0], c[1] + 40
            self.banner = {"t": 0.0, "title": DUNGEONS[key]["name"], "sub": "Двери запираются, пока в комнате есть враги", "color": PURPLE}
        self.pending_save = True

    def leave_through(self, what):
        kind = self.area_kind()
        if kind == "pass":
            idx = int(self.area[-1])
            w = self.world()
            if any(r["state"] != "cleared" for r in w.rooms):
                self.notify("Сначала расправься с засадой.", RED)
                return
            self.p["passes"][idx] = True
            if what == "exit_fwd":
                self.go_zone(idx + 1, "gate_back")
            else:
                self.go_zone(idx, "gate_next")
            return
        self.leave_instance()

    def leave_instance(self):
        old = self.area
        self.transient_clear()
        self.area = self.return_area
        self.ensure_area(self.area)
        self.p["x"], self.p["y"] = self.p["return_x"], self.p["return_y"]
        for key in [k for k in self.areas if not k.startswith("zone")]:
            self.areas.pop(key, None)
            self.worlds.pop(key, None)
        self.pending_save = True
        self.notify("Ты вернулся в регион.")

    def equip(self, index):
        if not 0 <= index < len(self.p["inv"]):
            return
        item = self.p["inv"].pop(index)
        old = self.p["equip"][item.slot]
        self.p["equip"][item.slot] = item
        if old:
            self.p["inv"].append(old)
        self.hp_cap()
        self.discard_index = None
        self.notify("Экипировано: " + item.title, item.color)
        self.sparkle(self.p["x"], self.p["y"], item.color, 12)
        self.sound("loot")

    def discard(self, index):
        if not 0 <= index < len(self.p["inv"]):
            return
        item = self.p["inv"][index]
        if self.near_camp():
            if item.unique and self.discard_index != index:
                self.discard_index = index
                self.notify("Уникальный предмет! Нажми продажу ещё раз для подтверждения.")
                return
            self.p["gold"] += item.price
            self.p["inv"].pop(index)
            self.notify("Продано за " + str(item.price) + " золота.")
            self.discard_index = None
        elif self.discard_index == index:
            self.add_drop(self.p["x"], self.p["y"], self.p["inv"].pop(index))
            self.discard_index = None
            self.notify("Предмет оставлен на земле.")
        else:
            self.discard_index = index
            self.notify("Нажми «Выбросить» ещё раз для подтверждения.")

    def change_inv_page(self, step):
        self.inv_page += step
        self.discard_index = None

    def spend_point(self, key):
        if self.p["points"] > 0:
            self.p["points"] -= 1
            self.p[key] += 1

    def buy_potion(self):
        if self.p["potions"] >= 8:
            self.notify("Можно нести не больше 8 зелий.")
        elif self.p["gold"] < 20:
            self.notify("Не хватает золота.", RED)
        else:
            self.p["gold"] -= 20
            self.p["potions"] += 1
            self.notify("Зелье куплено.")
            self.sound("loot")

    def buy_weapon(self, kind):
        if self.p["gold"] < 55:
            self.notify("Не хватает золота.", RED)
            return
        zi = self.zone_index()
        names = {"sword": "Меч путешественника", "spear": "Копьё путешественника", "staff": "Посох путешественника"}
        item = Item(names[kind], "weapon", 1, power=7 + zi * 3, weapon=kind, tier=zi)
        if self.add_item(item):
            self.p["gold"] -= 55

    def respawn(self):
        self.p["gold"] = int(self.p["gold"] * 0.9)
        self.p["hp"] = self.max_hp()
        self.p["potions"] = max(2, self.p["potions"])
        self.transient_clear()
        self.area = self.return_area if self.area_kind() != "zone" else self.area
        for key in [k for k in self.areas if not k.startswith("zone")]:
            self.areas.pop(key, None)
            self.worlds.pop(key, None)
        self.ensure_area(self.area)
        zi = self.zone_index()
        self.arena = None
        for e in self.current()["enemies"]:
            e.x, e.y = e.home_x, e.home_y
            e.wind = e.dash = 0
            e.kx = e.ky = 0.0
            e.pat = ""
            e.cd = 1
            if e.kind == "boss":
                e.hp, e.phase, e.awake, e.attack = e.maxhp, 1, 0, ""
        camp = self.world().camp_xy
        self.p["x"], self.p["y"] = camp[0], camp[1] + 90
        for key in ("attack_cd", "skill_cd", "pulse_cd", "dash_cd"):
            self.p[key] = 0
        self.buffs.clear()
        self.shield = 0
        self.last_hit = None
        self.set_state("play")
        self.pending_save = True
        self.notify("Ты вернулся в лагерь. Потеряно 10% золота.")

    def continue_after_win(self):
        self.transient_clear()
        self.area = self.return_area = "zone2"
        for key in [k for k in self.areas if not k.startswith("zone")]:
            self.areas.pop(key, None)
            self.worlds.pop(key, None)
        self.ensure_area(self.area)
        camp = self.world().camp_xy
        self.p["x"], self.p["y"] = camp[0], camp[1] + 90
        self.p["hp"] = self.max_hp()
        self.set_state("play")
        self.pending_save = True
        self.notify("Эльдория спасена. Можно завершить задания или спуститься в Бездну!")

    def menu_rows(self):
        return [
            ("Продолжить сессию", self.resume, self.session),
            ("Загрузить сохранение", self.load_game, self.has_save()),
            ("Новая игра" if not self.confirm_new else "Подтвердить новую игру", self.start_new, True),
            ("Бездна (рогалик)", self.start_abyss_menu, True),
            ("Святилище душ", self.open_sanctum, True),
            ("Настройки", self.open_settings, True),
            ("Как играть", self.open_help, True),
            ("Сохранить и выйти" if self.session and self.mode == "campaign" else "Выйти", self.quit, True),
        ]

    def first_menu_index(self):
        return next((i for i, row in enumerate(self.menu_rows()) if row[2]), 0)

    def menu_move(self, step):
        rows = self.menu_rows()
        for _ in rows:
            self.menu_index = (self.menu_index + step) % len(rows)
            if rows[self.menu_index][2]:
                break

    def quit(self):
        if self.mode == "abyss":
            self.end_run()
            self.running = False
        elif not self.session or self.save_game(quiet=True):
            self.running = False
        else:
            self.notify("Выход отменён: не удалось сохранить. F12 — выйти без сохранения.", RED)

    def to_menu(self):
        if self.mode == "abyss":
            self.end_run()
            self.session = False
            self.set_state("menu")
            return
        if not self.session or self.save_game():
            self.set_state("menu")

    def open_sanctum(self):
        self.set_state("sanctum")

    def buy_meta(self, key):
        name, desc, maxr, base = META[key]
        rank = self.meta["upgrades"].get(key, 0)
        cost = base * (rank + 1)
        if rank >= maxr or self.meta["shards"] < cost:
            return
        self.meta["shards"] -= cost
        self.meta["upgrades"][key] = rank + 1
        self.save_profile()
        self.sound("level")
        self.notify("Улучшено: " + name, PURPLE)

    def respec(self):
        spent = 0
        for sid, rank in self.p["skills"].items():
            spent += rank * SKILLS[sid].get("cost", 1)
        ch = CHARACTERS[self.p.get("char", 0)]
        self.p["skills"] = {ch["skill"][0]: ch["skill"][1]} if self.mode == "abyss" or self.p["name"] == ch["name"] else {}
        free = SKILLS[ch["skill"][0]].get("cost", 1) * ch["skill"][1] if self.p["skills"] else 0
        self.p["sp"] += spent - free
        self.hp_cap()
        self.notify("Навыки сброшены: очки возвращены.", GOLD)

    # ------------------------------------------------------ rooms
    def trap_phase(self, tr):
        ph = (self.time + tr[4]) % 3.0
        return "warn" if ph < 0.9 else "active" if ph < 1.6 else "off"

    def spawn_room(self, i):
        w = self.world()
        room = w.rooms[i]
        x, y, rw, rh = room["rect"]
        cx, cy = room["center"]
        zi = self.zone_index()
        kind = room["kind"]
        enemies = self.current()["enemies"]
        pool = SPAWN[min(2, zi)] if self.area_kind() != "pass" else SPAWN[min(2, zi + 1)]
        kinds, weights = list(pool), list(pool.values())
        floor = self.abyss_run["floor"] if self.mode == "abyss" else 1
        n = {"combat": 4 + zi + random.randint(0, 2) + (floor // 3 if self.mode == "abyss" else 0), "trap": 3}.get(kind, 2)

        def place(k, special=None):
            for attempt in range(90):
                ex, ey = random.randint(int(x + 70), int(x + rw - 70)), random.randint(int(y + 70), int(y + rh - 70))
                if w.blocked(ex, ey, 18) or (attempt < 60 and distance(ex, ey, self.p["x"], self.p["y"]) < 150):
                    continue
                e = special or self.make_enemy(k, ex, ey, zi, self.area_kind() != "pass")
                e.x = e.home_x = ex
                e.y = e.home_y = ey
                e.room, e.cd = i, random.uniform(0.8, 1.8)
                enemies.append(e)
                return e
        if kind in ("combat", "trap"):
            for _ in range(n):
                place(random.choices(kinds, weights=weights)[0])
        elif kind in ("miniboss", "boss"):
            if kind == "boss" and self.mode == "abyss" and self.abyss_run["floor"] % 3 == 0:
                boss = self.make_boss(min(2, (self.abyss_run["floor"] // 3 - 1) % 3), cx, cy, 1)
                boss.room = i
                enemies.append(boss)
                self.banner = {"t": 0.0, "title": BOSSES[boss.zone]["name"], "sub": BOSSES[boss.zone]["title"], "color": BOSSES[boss.zone]["color"]}
            else:
                e = self.make_enemy("miniboss", cx, cy, zi, True)
                e.awake, e.room = 1, i
                if self.area_kind() == "dungeon":
                    e.tag = DUNGEONS[self.area]["mini"]
                else:
                    e.tag = random.choice(["Страж Бездны", "Хранитель этажа", "Пожиратель душ"])
                enemies.append(e)
                self.banner = {"t": 0.0, "title": e.tag, "sub": "Страж подземелья", "color": ZONES[zi]["boss_color"]}
            for _ in range(2):
                place(random.choices(kinds, weights=weights)[0])

    def start_room(self, i):
        w = self.world()
        room = w.rooms[i]
        kind = room["kind"]
        if kind == "treasure":
            room["state"] = "cleared"
            w.pois.append({"kind": "altar", "x": room["center"][0], "y": room["center"][1]})
            return
        if kind == "shop":
            room["state"] = "cleared"
            w.pois.append({"kind": "stall", "x": room["center"][0], "y": room["center"][1]})
            return
        room["state"] = "locked"
        w.set_doors(i, True)
        self.spawn_room(i)
        self.shake = max(self.shake, 3)
        self.sound("hurt")

    def clear_room(self, i):
        w = self.world()
        room = w.rooms[i]
        room["state"] = "cleared"
        w.set_doors(i, False)
        self.sparkle(room["center"][0], room["center"][1], GOLD, 20, 60)
        self.sound("win")
        kind = room["kind"]
        self.hazards = [h for h in self.hazards if h["owner"] is not None or h.get("linger")]
        if self.area_kind() == "abyss":
            self.p["gold"] += int(8 * self.abyss_run["floor"] * (1 + 0.25 * self.mod_count("gold")))
        if kind in ("miniboss", "boss"):
            cx, cy = room["center"]
            if self.area_kind() == "dungeon":
                area = self.current()
                area["reward"] = True
                area["reward_pos"] = (cx, cy)
                self.notify("Подземелье зачищено! Награда появилась в центре зала.", GOLD)
            elif self.area_kind() == "abyss":
                w.pois.append({"kind": "stairs", "x": cx, "y": cy + 60})
                self.abyss_run["shards"] += 3 + self.abyss_run["floor"]
                self.open_boon("Награда за этаж", self.relic_offers(3), "free")
        elif self.area_kind() == "abyss" and random.random() < 0.4:
            self.add_drop(room["center"][0], room["center"][1], self.make_item(self.zone_index()))

    def update_rooms(self, dt):
        w = self.world()
        if not w.rooms:
            return
        px, py = self.p["x"], self.p["y"]
        enemies = self.current()["enemies"]
        for i, room in enumerate(w.rooms):
            x, y, rw, rh = room["rect"]
            if room["state"] == "idle" and x + 50 < px < x + rw - 50 and y + 50 < py < y + rh - 50:
                self.start_room(i)
            elif room["state"] == "locked" and not any(e.room == i for e in enemies):
                self.clear_room(i)
        for tr in w.traps:
            if self.trap_phase(tr) == "active" and tr[0] - 10 < px < tr[0] + tr[2] + 10 and tr[1] - 10 < py < tr[1] + tr[3] + 10:
                self.take_damage(10 * ZONES[min(2, self.zone_index())]["scale"] * self.scale_mul, "Шипы", (tr[0] + tr[2] / 2, tr[1] + tr[3] / 2))
        if w.biome_style == "bridge" and any(r["state"] == "locked" for r in w.rooms):
            self.rock_t -= dt
            if self.rock_t <= 0:
                self.rock_t = 1.1
                rx, ry = px + random.uniform(-160, 160), py + random.uniform(-160, 160)
                self.hazards.append({"x": rx, "y": ry, "radius": 58, "line": 0, "width": 0, "dx": 0, "dy": 1, "delay": 0.9, "total": 0.9,
                                     "life": 0.22, "done": False, "damage": 16 * self.scale_mul, "bdmg": 0, "burst": None,
                                     "owner": None, "src": "Камнепад", "linger": 0, "ldmg": 0, "tick": 0, "color": None})

    # ------------------------------------------------------- abyss
    def relic_offers(self, n, price=None):
        mods = random.sample(list(MODS), n)
        return [{"mod": m, "price": price} for m in mods]

    def open_boon(self, title, offers, kind):
        self.boon = {"title": title, "offers": offers, "kind": kind}
        self.set_state("boon")

    def pick_boon(self, offer):
        if offer["price"] is not None:
            if self.p["gold"] < offer["price"] or offer.get("sold"):
                return
            self.p["gold"] -= offer["price"]
            offer["sold"] = True
        self.relics[offer["mod"]] = self.relics.get(offer["mod"], 0) + 1
        self.hp_cap()
        self.sound("win")
        self.notify("Реликвия: " + MODS[offer["mod"]][0], PURPLE)
        if self.boon["kind"] == "free":
            self.close_boon()

    def close_boon(self):
        self.boon = None
        self.set_state("play")

    def start_abyss(self, ch):
        if self.session and self.mode == "campaign":
            self.save_game(quiet=True)
        self.mode = "abyss"
        self.relics = {}
        self.abyss_run = {"floor": 1, "zi": 0, "shards": 0, "ended": False}
        self.run_stats = {"kills": 0}
        self.p = self.new_player()
        self.apply_character(ch)
        self.p["potions"] = min(8, 3 + self.meta_rank("potion"))
        for _ in range(self.meta_rank("relic")):
            m = random.choice(list(MODS))
            self.relics[m] = self.relics.get(m, 0) + 1
        self.areas, self.worlds, self.terrain_cache, self.mini_cache = {}, {}, {}, {}
        self.session = True
        self.build_abyss_floor(1)
        self.reset_runtime()
        self.p["hp"] = self.max_hp()
        self.set_state("play")
        self.banner = {"t": 0.0, "title": "БЕЗДНА", "sub": "Этаж 1 · реликвии и души ждут", "color": PURPLE}

    def build_abyss_floor(self, floor):
        zi = min(2, (floor - 1) // 3)
        self.abyss_run["floor"], self.abyss_run["zi"] = floor, zi
        self.scale_mul = 1 + 0.18 * (floor - 1)
        n_combat = 3 + min(3, floor // 2)
        kinds = ["combat"] * 2 + ["treasure" if floor % 2 else "shop"] + ["combat"] * (n_combat - 2) + ["boss"]
        seed = random.randint(1, 10 ** 6)
        self.terrain_cache.pop("abyss", None)
        self.mini_cache = {}
        w = W.build_rooms("abyss", zi, kinds, seed, "cave", biome=ZONES[zi]["biome"])
        self.worlds["abyss"] = w
        self.areas = {"abyss": {"enemies": [], "drops": [], "reward": False}}
        self.area = self.return_area = "abyss"
        c = w.rooms[0]["center"]
        self.p["x"], self.p["y"] = c[0], c[1]
        self.p["return_x"], self.p["return_y"] = c

    def next_floor(self):
        self.transient_clear()
        self.p["hp"] = min(self.max_hp(), self.p["hp"] + self.max_hp() * 0.3)
        self.build_abyss_floor(self.abyss_run["floor"] + 1)
        self.banner = {"t": 0.0, "title": "Этаж {}".format(self.abyss_run["floor"]), "sub": ZONES[self.abyss_run["zi"]]["name"], "color": PURPLE}
        self.shake = 6

    def end_run(self):
        if self.mode != "abyss" or self.abyss_run.get("ended"):
            return
        self.abyss_run["ended"] = True
        floor = self.abyss_run["floor"]
        shards = self.abyss_run["shards"] + floor * 3 + self.run_stats["kills"] // 15
        self.abyss_run["shards"] = shards
        self.meta["shards"] += shards
        self.meta["best"] = max(self.meta["best"], floor)
        self.meta["runs"] += 1
        self.save_profile()

    def on_player_death(self):
        self.arena = None
        if self.mode == "abyss":
            self.end_run()

    def open_settings(self):
        self.back_state = self.state
        self.set_state("settings")

    def open_help(self):
        self.back_state = self.state
        self.set_state("help")

    def resume(self):
        if self.session:
            self.dialog = None
            self.set_state("dead" if self.p["hp"] <= 0 else "play")

    def change_resolution(self, step):
        self.settings["resolution"] = (self.settings["resolution"] + step) % len(RESOLUTIONS)
        if not self.settings["fullscreen"]:
            self.apply_display()
        self.save_settings()

    def toggle_fullscreen(self):
        self.settings["fullscreen"] = not self.settings["fullscreen"]
        self.apply_display()
        self.save_settings()

    def toggle_music(self):
        self.settings["music"] = not self.settings["music"]
        self.save_settings()
        self.update_music()

    def change_volume(self, step):
        self.settings["volume"] = round(clamp(self.settings["volume"] + step, 0, 1), 2)
        self.save_settings()
        self.update_music()
        self.sound("loot")

    # ----------------------------------------------------------- events
    def cam_base(self):
        return (clamp(self.p["x"] - VW / 2, 0, WW - VW), clamp(self.p["y"] - VH / 2, 0, WH - VH))

    def aim(self, mouse):
        cx, cy = self.cam_base()
        self.p["face_x"], self.p["face_y"] = direction(mouse[0] + cx - self.p["x"], mouse[1] + cy - self.p["y"])

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            self.quit()
            return
        if event.type in (pygame.VIDEORESIZE, getattr(pygame, "WINDOWRESIZED", pygame.VIDEORESIZE)):
            self.update_viewport()
            return
        if event.type == pygame.MOUSEBUTTONDOWN:
            mouse = self.mouse(event.pos)
            if self.state == "cutscene":
                self.advance_cutscene()
                return
            if self.state != "play" and mouse:
                for rect, action, enabled in reversed(self.buttons[:]):
                    if enabled and rect.collidepoint(mouse):
                        action()
                        return
            elif self.state == "play" and mouse and not self.transition:
                self.aim(mouse)
                if event.button == 1:
                    self.attack()
                elif event.button == 3:
                    self.attack(special=True)
        if event.type == pygame.MOUSEWHEEL and self.state == "inventory":
            pages = max(1, math.ceil(len(self.p["inv"]) / 6))
            self.inv_page = int(clamp(self.inv_page - event.y, 0, pages - 1))
        if event.type == pygame.MOUSEMOTION and self.state == "play":
            mouse = self.mouse(event.pos)
            if mouse:
                self.aim(mouse)
        if event.type != pygame.KEYDOWN:
            return
        key = event.key
        if key == pygame.K_F12:
            self.running = False
            return
        if key == pygame.K_F11:
            self.toggle_fullscreen()
            return
        if key == pygame.K_F5 and self.session and self.state in ("play", "pause", "dead"):
            if self.save_game() and self.state == "pause":
                self.resume()
            return
        if key == pygame.K_F9 and self.state not in ("creator", "cutscene"):
            self.load_game()
            return
        if key == pygame.K_F10:
            if self.state == "play":
                self.set_state("pause")
            elif self.state == "pause":
                self.resume()
            return
        if key == pygame.K_ESCAPE:
            if self.state == "play":
                self.set_state("pause")
            elif self.state in ("pause", "inventory", "stats", "journal", "map", "shop", "smith", "skills", "dialog"):
                self.resume()
            elif self.state == "boon":
                self.close_boon()
            elif self.state == "sanctum":
                self.set_state("menu")
            elif self.state in ("help", "settings"):
                self.set_state(self.back_state)
            elif self.state == "menu":
                self.quit()
            elif self.state == "creator":
                self.set_state("menu")
            elif self.state == "cutscene":
                self.finish_cutscene()
            return
        if self.state == "cutscene":
            if key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_KP_ENTER):
                self.advance_cutscene()
            return
        if self.state == "creator":
            c = self.creator
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.finish_creator()
            elif key in (pygame.K_LEFT, pygame.K_a):
                c["char"] = (c["char"] - 1) % 3
            elif key in (pygame.K_RIGHT, pygame.K_d):
                c["char"] = (c["char"] + 1) % 3
            elif pygame.K_1 <= key <= pygame.K_3:
                c["char"] = key - pygame.K_1
            return
        if self.state == "sanctum":
            return
        if self.state == "boon":
            offers = self.boon["offers"] if self.boon else []
            if pygame.K_1 <= key < pygame.K_1 + len(offers):
                self.pick_boon(offers[key - pygame.K_1])
            return
        if self.state == "menu":
            rows = self.menu_rows()
            if key == pygame.K_n:
                self.start_new()
            elif key in (pygame.K_UP, pygame.K_w):
                self.menu_move(-1)
            elif key in (pygame.K_DOWN, pygame.K_s):
                self.menu_move(1)
            elif pygame.K_1 <= key < pygame.K_1 + len(rows):
                index = key - pygame.K_1
                if rows[index][2]:
                    self.menu_index = index
                    rows[index][1]()
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if rows[self.menu_index][2]:
                    rows[self.menu_index][1]()
            return
        if self.state == "dead":
            if key == pygame.K_r and self.mode != "abyss":
                self.respawn()
            return
        if self.state == "victory":
            if key == pygame.K_RETURN:
                self.continue_after_win()
            return
        if self.state == "help":
            if key == pygame.K_RETURN:
                self.resume() if self.session else self.set_state("menu")
            return
        if self.state == "dialog":
            buttons = self.dialog["buttons"] if self.dialog else []
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_e) and buttons:
                buttons[0][1]()
            elif key == pygame.K_2 and len(buttons) > 1:
                buttons[1][1]()
            return
        if self.state == "pause":
            if key in (pygame.K_RETURN, pygame.K_p):
                self.resume()
            elif key == pygame.K_h:
                self.open_help()
            elif key == pygame.K_q:
                self.quit()
            elif key == pygame.K_m:
                self.to_menu()
            return
        if self.state == "settings":
            if key == pygame.K_LEFT and not self.settings["fullscreen"]:
                self.change_resolution(-1)
            elif key == pygame.K_RIGHT and not self.settings["fullscreen"]:
                self.change_resolution(1)
            elif key == pygame.K_f:
                self.toggle_fullscreen()
            elif key == pygame.K_m:
                self.toggle_music()
            elif key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self.change_volume(-0.1)
            elif key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                self.change_volume(0.1)
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.set_state(self.back_state)
            return
        panels = {pygame.K_i: "inventory", pygame.K_c: "stats", pygame.K_j: "journal", pygame.K_m: "map",
                  pygame.K_z: "map", pygame.K_k: "skills"}
        if key in panels and self.state in ("play", "inventory", "stats", "journal", "map", "skills"):
            target = panels[key]
            self.set_state("play" if self.state == target else target)
            return
        if self.state == "stats" and key in (pygame.K_1, pygame.K_2, pygame.K_3):
            self.spend_point({pygame.K_1: "str", pygame.K_2: "vit", pygame.K_3: "agi"}[key])
            return
        if self.state != "play" or self.transition:
            return
        if key == pygame.K_SPACE:
            if self.p["attack_cd"] <= 0:
                self.autoaim()
            self.attack()
        elif key == pygame.K_r:
            if self.p["skill_cd"] <= 0:
                self.autoaim()
            self.attack(special=True)
        elif key == pygame.K_q:
            self.attack(pulse=True)
        elif key == pygame.K_h:
            self.potion()
        elif key == pygame.K_e:
            self.interact()
        elif key in (pygame.K_LSHIFT, pygame.K_RSHIFT):
            self.dash()
        elif key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
            self.use_skill(key - pygame.K_1 + 1)
        elif key == pygame.K_F1:
            self.open_help()

    # ----------------------------------------------------------- update
    def update(self, dt):
        self.time += dt
        self.toast_time = max(0, self.toast_time - dt)
        if self.state == "cutscene" and self.cut:
            self.cut["t"] += dt
            return
        if self.state == "dialog" and self.dialog:
            self.dialog["t"] += dt
        if not self.session:
            return
        self.shake = max(0, self.shake - 28 * dt)
        self.flash = max(0, self.flash - 2.2 * dt)
        self.hurt_t = max(0, self.hurt_t - dt)
        if self.levelfx:
            self.levelfx["t"] += dt
            if self.levelfx["t"] > 2.4:
                self.levelfx = None
        if self.banner:
            self.banner["t"] += dt
            if self.banner["t"] > 3.2:
                self.banner = None
        for r in list(self.ripples):
            r[1] -= dt * 2
            if r[1] <= 0:
                self.ripples.remove(r)
        for f in list(self.fx):
            f[4] -= dt
            if f[4] <= 0:
                self.fx.remove(f)
        if self.last_hit:
            self.last_hit["t"] -= dt
        if self.transition:
            tr = self.transition
            tr["t"] += dt
            if tr["t"] >= tr["dur"] / 2 and not tr["fired"]:
                tr["fired"] = True
                tr["cb"]()
            if tr["t"] >= tr["dur"]:
                self.transition = None
            if not tr["fired"]:
                return
        if self.state != "play":
            if self.pending_save:
                self.pending_save = False
                self.save_game(quiet=True)
            return
        if self.hitstop > 0:
            self.hitstop -= dt
            return
        if self.slowmo > 0:
            self.slowmo -= dt
            dt *= 0.4
        p = self.p
        for key in ("attack_cd", "skill_cd", "pulse_cd", "dash_cd", "invuln", "dash"):
            p[key] = max(0, p[key] - dt)
        self.swing = max(0.0, self.swing - dt)
        self.lunge = max(0.0, self.lunge - dt)
        self.update_skills(dt)
        if self.rank("regen") and p["hp"] < self.max_hp():
            p["hp"] = min(self.max_hp(), p["hp"] + 0.7 * self.rank("regen") * dt)
        keys = pygame.key.get_pressed()
        dx = int(keys[pygame.K_d] or keys[pygame.K_RIGHT]) - int(keys[pygame.K_a] or keys[pygame.K_LEFT])
        dy = int(keys[pygame.K_s] or keys[pygame.K_DOWN]) - int(keys[pygame.K_w] or keys[pygame.K_UP])
        self.moving = bool(dx or dy)
        if dx or dy:
            dx, dy = direction(dx, dy)
            p["face_x"], p["face_y"] = dx, dy
        if p["dash"] > 0:
            dx, dy = p["face_x"], p["face_y"]
            speed = 650
            self.ghost_timer -= dt
            if self.ghost_timer <= 0:
                self.ghost_timer = 0.03
                fx, fy = p["face_x"], p["face_y"]
                base = "hero_side" if abs(fx) > abs(fy) else ("hero_up" if fy < 0 else "hero_down")
                self.ghosts.append([p["x"], p["y"], self.hero_sprite_name(base), 48, base == "hero_side" and fx < 0, 0.2])
            self.burst(p["x"], p["y"] + 12, (190, 180, 150), 1)
        else:
            speed = self.move_speed()
            if self.moving:
                self.step_timer -= dt
                if self.step_timer <= 0:
                    self.step_timer = 0.17
                    self.particles.append([p["x"] + random.uniform(-6, 6), p["y"] + 15, random.uniform(-10, 10), -12, 0.3, (140, 130, 110)])
        p["x"], p["y"] = self.move(p["x"], p["y"], dx * speed * dt, dy * speed * dt, 14)
        for g in list(self.ghosts):
            g[5] -= dt
            if g[5] <= 0:
                self.ghosts.remove(g)
        if keys[pygame.K_SPACE]:
            if p["attack_cd"] <= 0:
                self.autoaim()
            self.attack()
        elif pygame.mouse.get_pressed()[0] and self.mouse():
            self.aim(self.mouse())
            self.attack()

        enemies = self.current()["enemies"]
        for e in list(enemies):
            if self.state != "play":
                break
            if e in enemies:
                self.update_enemy(e, dt)
        if self.state == "play":
            self.update_hazards(dt)
            self.update_bullets(dt)
            self.update_projectiles(dt)
        if self.state == "play":
            self.update_rooms(dt)
            if self.surface():
                self.update_arena(dt)
                regular = sum(e.kind not in ("boss", "finalboss") for e in enemies)
                if random.random() < dt / 3 and regular < ZONE_ENEMY_CAP:
                    self.spawn_regular()
        for part in list(self.particles):
            part[4] -= dt
            part[0] += part[2] * dt
            part[1] += part[3] * dt
            if part[4] <= 0:
                self.particles.remove(part)
        for f in list(self.floaters):
            f[1] -= 28 * dt
            f[4] -= dt
            if f[4] <= 0:
                self.floaters.remove(f)
        for s in list(self.slashes):
            s[4] -= dt
            if s[4] <= 0:
                self.slashes.remove(s)
        for d in list(self.deaths):
            d[4] += dt
            if d[4] > 0.5:
                self.deaths.remove(d)
        for drop in self.current()["drops"]:
            drop["age"] = drop.get("age", 9.0) + dt
        self.autosave_time += dt
        if self.pending_save or self.autosave_time >= 60:
            if not self.pending_save and self.in_combat():
                self.autosave_time = 50
            else:
                self.pending_save = False
                self.autosave_time = 0
                self.save_game(quiet=True)

    def run(self):
        self.update_music()
        self.draw()
        while self.running:
            dt = min(self.clock.tick(60) / 1000, 0.04)
            for event in pygame.event.get():
                self.handle_event(event)
                if not self.running:
                    break
            if not self.running:
                break
            self.update(dt)
            self.draw()
        pygame.quit()


def ASSETS_PATH():
    return C.ASSETS


if __name__ == "__main__":
    Game().run()
