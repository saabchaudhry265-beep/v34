"""Run from the project folder:  python -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import unittest
from collections import deque
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tropy import common as C  # noqa: E402
from tropy import world as W  # noqa: E402
from tropy.content import (CHARACTERS, DUNGEONS, ENEMIES, MODS, PASSES, PATTERNS, QUESTS, SKILLS,  # noqa: E402
                           SPAWN, UNIQUES, ZONE_LAYOUT, NPCS, fmt)
from tropy.models import Enemy, Item  # noqa: E402
import game  # noqa: E402


class GameCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        C.DATA = Path(self.tmp.name)
        C.SAVE_FILE = C.DATA / "save.json"
        C.LEGACY_SAVE = C.DATA / "legacy.json"
        C.SETTINGS_FILE = C.DATA / "settings.json"
        C.PROFILE_FILE = C.DATA / "profile.json"
        self.g = game.Game()
        self.g.creator = {"char": 2, "mode": "campaign"}
        self.g.finish_creator()
        self.g.finish_cutscene()
        self.g.set_state("play")

    def tearDown(self):
        game.pygame.quit()
        self.tmp.cleanup()

    def saved(self):
        self.assertTrue(self.g.save_game(), self.g.toast)
        return json.loads(C.SAVE_FILE.read_text(encoding="utf8"))


class ContentTests(unittest.TestCase):
    def test_name_declension_and_gender(self):
        text = "Откликнул<ся|ась> {name}. Путь {name_g}. Ты слаб<|а>."
        self.assertEqual(fmt(text, CHARACTERS[1]), "Откликнулась Лира. Путь Лиры. Ты слаба.")
        self.assertEqual(fmt(text, CHARACTERS[0]), "Откликнулся Эйрик. Путь Эйрика. Ты слаб.")

    def test_data_is_consistent(self):
        for q in QUESTS.values():
            self.assertIn(q["giver"], NPCS)
            self.assertTrue(q.get("after", next(iter(QUESTS))) in QUESTS)
        for table in SPAWN:
            self.assertTrue(set(table) <= set(ENEMIES))
        for uid in UNIQUES:
            self.assertTrue(all(m in MODS for m in UNIQUES[uid]["mods"]))
        for did in DUNGEONS:
            self.assertIn(did, UNIQUES)
        self.assertEqual(len(PASSES), 2)


class SaveTests(GameCase):
    def test_round_trip_keeps_new_fields(self):
        self.g.p["gold"], self.g.p["skills"] = 321, {"might": 2}
        self.g.p["quests"] = {"q_slime": {"s": 1, "n": 3}}
        self.g.p["dungeons"][3] = True
        self.saved()
        other = game.Game()
        other.load_game()
        self.assertEqual((other.p["gold"], other.p["name"], other.p["char"]), (321, "Альдрик", 2))
        self.assertEqual(other.p["skills"], {"might": 2})
        self.assertTrue(other.p["dungeons"][3])

    def test_backup_used_when_main_is_corrupted(self):
        self.g.save_game()
        self.g.save_game()
        C.SAVE_FILE.write_text("{broken", encoding="utf8")
        other = game.Game()
        other.load_game()
        self.assertTrue(other.session)
        self.assertIn("резервная", other.toast)

    def test_rejects_bad_data(self):
        data = self.saved()
        for mutate in (lambda d: d.update(version=1), lambda d: d["player"].update(gold=True),
                       lambda d: d["player"].update(hp=float("nan")), lambda d: d["player"].update(potions=99),
                       lambda d: d["player"].update(char=9), lambda d: d["player"].update(skills={"nope": 1}),
                       lambda d: d["player"].update(dungeons=[True]), lambda d: d.update(area="dg0_0"),
                       lambda d: d["player"].update(quests={"q_slime": {"s": 7, "n": 0}})):
            bad = json.loads(json.dumps(data))
            mutate(bad)
            with self.assertRaises(ValueError):
                self.g.decode_save(bad)

    def test_old_saves_are_migrated(self):
        data = self.saved()
        data["version"] = 33
        data["player"]["dungeons"] = [True, False, False]
        data["player"].pop("char")
        data["player"].pop("passes")
        data["areas"] = {}
        C.LEGACY_SAVE.write_text(json.dumps(data), encoding="utf8")
        C.SAVE_FILE.unlink()
        other = game.Game()
        other.load_game()
        self.assertEqual(other.state, "play")
        self.assertEqual(len(other.p["dungeons"]), 6)
        self.assertTrue(other.p["dungeons"][0])

    def test_saving_inside_a_dungeon_returns_to_the_region(self):
        g = self.g
        g.p["x"], g.p["y"] = 800, 800
        g.enter("dg0_0")
        self.assertEqual(g.area, "dg0_0")
        data = self.saved()
        self.assertEqual(data["area"], "zone0")
        self.assertEqual(data["player"]["x"], 800)

    def test_state_saved_mid_boss_fight_validates(self):
        g = self.g
        boss = next(e for e in g.current()["enemies"] if e.kind == "boss")
        boss.awake, boss.pat, boss.pi, boss.slow, boss.burn = 1, "laser", -0.01, 0.0, 0.0
        self.saved()

    def test_new_game_archives_previous_save(self):
        self.g.save_game()
        self.g.creator = {"char": 0, "mode": "campaign"}
        self.g.finish_creator()
        self.assertTrue((C.DATA / "eldoria_v34.archive.json").exists())

    def test_item_and_enemy_restore(self):
        for bad in (None, [], {"name": "x", "slot": "bad", "rarity": 0}, {"name": "x", "slot": "weapon", "rarity": 9},
                    {"name": "x", "slot": "weapon", "rarity": 0, "plus": 9},
                    {"name": "x", "slot": "weapon", "rarity": 0, "mods": ["bogus"]}):
            with self.assertRaises((ValueError, TypeError)):
                Item.restore(bad)
        e = self.g.make_enemy("boss", 500, 500, 0)
        e.wind, e.attack, e.kx, e.pat, e.awake = 0.4, "charge", 300.0, "ring", 1
        restored = Enemy.restore(json.loads(json.dumps(asdict(e))))
        self.assertEqual((restored.wind, restored.attack, restored.kx, restored.pat, restored.awake), (0, "", 0.0, "", 0))


class WorldTests(GameCase):
    def test_zone_maps_are_connected_and_distinct(self):
        grids = []
        for zi in range(3):
            w = W.build_zone(zi)
            start = (int(w.camp_xy[0] // C.CELL), int(w.camp_xy[1] // C.CELL))
            seen, queue = {start}, deque([start])
            while queue:
                cx, cy = queue.popleft()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (cx + dx, cy + dy)
                    if 0 <= n[0] < w.gw and 0 <= n[1] < w.gh and n not in seen and w.grid[n[1]][n[0]] not in W.SOLID:
                        seen.add(n)
                        queue.append(n)
            for poi in w.pois:
                self.assertIn((int(poi["x"] // C.CELL), int(poi["y"] // C.CELL)), seen, (zi, poi["kind"]))
            self.assertIn((int(w.arena_xy[0] // C.CELL), int(w.arena_xy[1] // C.CELL)), seen)
            grids.append(w.grid)
        self.assertNotEqual(grids[0], grids[1])
        self.assertNotEqual([ZONE_LAYOUT[i]["camp"] for i in range(3)].count(ZONE_LAYOUT[0]["camp"]), 3)

    def test_dungeons_and_passes_are_connected(self):
        for did, info in DUNGEONS.items():
            w = W.build_dungeon(did, info)
            self.assertEqual(len(w.rooms), len(info["kinds"]) + 1)
            self.assertTrue(w.doors)
            self.assertTrue(w.pois)
        for i, info in enumerate(PASSES):
            self.assertEqual(len(W.build_pass(i, info).pois), 2)


class CombatTests(GameCase):
    def test_defense_never_zeroes_damage(self):
        self.g.p["equip"]["armor"] = Item("a", "armor", 0, armor=200)
        self.g.p["hp"], self.g.p["invuln"] = 100, 0
        self.g.take_damage(6)
        self.assertLess(self.g.p["hp"], 100)

    def test_weapon_types_use_enemy_weaknesses(self):
        golem = self.g.make_enemy("golem", 800, 800, 1)
        self.assertGreater(self.g.resist(golem, "pierce"), self.g.resist(golem, "slash"))
        shaman = self.g.make_enemy("shaman", 800, 800, 2)
        self.assertLess(self.g.resist(shaman, "magic"), self.g.resist(shaman, "slash"))

    def test_mods_are_applied(self):
        g = self.g
        base = g.damage()
        g.relics["might"] = 2
        self.assertGreater(g.damage(), base)
        e = g.make_enemy("slime", 700, 700, 0)
        g.current()["enemies"].append(e)
        g.relics["burn"] = 1
        g.relics["frost"] = 1
        g.hit(e, 1)
        self.assertGreater(e.burn, 0)
        self.assertGreater(e.slow, 0)

    def test_dormant_boss_is_invulnerable_and_arena_needs_kills(self):
        g = self.g
        boss = next(e for e in g.current()["enemies"] if e.kind == "boss")
        hp = boss.hp
        g.hit(boss, 5)
        self.assertEqual(boss.hp, hp)
        ax, ay = g.world().arena_xy
        g.p["x"], g.p["y"] = ax, ay + 300
        g.update_arena(0.016)
        self.assertEqual(boss.awake, 0)
        g.p["zonekills"][0] = 9
        g.p["x"], g.p["y"] = ax, ay + 300
        g.update_arena(0.016)
        self.assertEqual(boss.awake, 1)
        self.assertIsNotNone(g.arena)

    def test_boss_patterns_and_two_forms(self):
        g = self.g
        g.p["hp"] = 10 ** 5
        boss = g.make_enemy("boss", 1000, 800, 1)
        boss.awake = 1
        g.current()["enemies"].append(boss)
        g.p["x"], g.p["y"] = 800, 800
        for name in PATTERNS:
            g.start_pattern(boss, name)
            for _ in range(120):
                g.update_enemy(boss, 0.016)
                g.update_hazards(0.016)
                g.update_bullets(0.016)
            boss.pat, boss.wind, boss.dash, boss.attack = "", 0, 0, ""
        boss.hp = boss.maxhp * 0.4
        for _ in range(130):
            g.update_enemy(boss, 0.016)
        self.assertEqual(boss.phase, 2)

    def test_every_enemy_kind_can_attack(self):
        g = self.g
        g.p["hp"] = 10 ** 5
        for kind in list(ENEMIES):
            if kind in ("boss", "finalboss", "miniboss"):
                continue
            e = g.make_enemy(kind, 900, 900, 1)
            g.current()["enemies"].append(e)
            g.p["x"], g.p["y"] = 1000, 900
            for _ in range(240):
                g.update_enemy(e, 0.016)
                g.update_hazards(0.016)
                g.update_bullets(0.016)
            g.current()["enemies"].remove(e)

    def test_loot_drops_animate(self):
        g = self.g
        g.add_drop(900, 900, g.make_unique("dg0_0"))
        drop = g.current()["drops"][-1]
        self.assertEqual(drop["age"], 0.0)
        for _ in range(60):
            g.update(0.016)
        self.assertGreater(drop["age"], 0.9)


class ProgressTests(GameCase):
    def test_quest_flow_and_rewards(self):
        g = self.g
        self.assertEqual(g.quest_status("q_slime"), "available")
        g.accept_quest("q_slime")
        for _ in range(6):
            e = g.make_enemy("slime", 700, 700, 0)
            g.current()["enemies"].append(e)
            g.kill(e)
        self.assertEqual(g.quest_status("q_slime"), "ready")
        g.claim_quest("q_slime")
        self.assertEqual(g.quest_status("q_slime"), "done")

    def test_faster_progression(self):
        g = self.g
        g.p["xp"] = 10 ** 5
        g.level_up()
        self.assertGreaterEqual(g.p["level"], 10)
        self.assertGreaterEqual(g.p["sp"], 18)

    def test_skill_requirements_respec_and_costs(self):
        g = self.g
        g.p["sp"] = 6
        g.buy_skill("crit")
        self.assertEqual(g.rank("crit"), 0)
        g.buy_skill("might")
        g.buy_skill("might")
        g.buy_skill("cry")
        self.assertEqual(g.rank("cry"), 1)
        g.p["x"], g.p["y"] = g.world().camp_xy
        g.respec()
        self.assertEqual(g.p["sp"], 6)

    def test_passes_replace_portals(self):
        g = self.g
        gate = next(p for p in g.world().pois if p["kind"] == "gate_next")
        g.p["x"], g.p["y"] = gate["x"], gate["y"] + 60
        g.interact()
        self.assertIsNone(g.transition)
        g.p["bosses"][0], g.p["level"] = True, 5
        g.interact()
        for _ in range(100):
            g.update(0.016)
        self.assertEqual(g.area, "pass0")
        self.assertTrue(g.world().rooms)

    def test_abyss_run_awards_souls(self):
        g = self.g
        g.start_abyss(CHARACTERS[0])
        self.assertEqual(g.area, "abyss")
        g.p["hp"] = 1
        g.p["invuln"] = 0
        g.take_damage(10 ** 6, "тест")
        self.assertEqual(g.state, "dead")
        self.assertGreater(g.meta["shards"], 0)
        self.assertEqual(game.Game().meta["shards"], g.meta["shards"])


if __name__ == "__main__":
    unittest.main()
