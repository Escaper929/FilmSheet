# -*- coding: utf-8 -*-
"""银盐册测试。

不依赖 GUI：archive/ 包刻意不导入 tkinter，所以这些测试可以直接跑。
"""

import os
import sys
import json
import time
import tempfile
import shutil
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from archive.models import Roll, Output, STATUS_ARCHIVED, STATUS_DRAFT
from archive import store as S
from archive.store import ArchiveStore, migrate, SCHEMA_VERSION
from archive.service import (ArchiveService, parse_stem, _looks_like_date,
                             _strip_style_suffix, _group_key)


class TempStoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="filmsheet-archive-test-")
        self.path = os.path.join(self.dir, "filmsheet_archive.json")
        self.store = ArchiveStore(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_config(self, **kw):
        base = {
            "info_roll": "A01", "info_film": "Portra400",
            "info_camera": "M6", "info_shoot_date": "2026-03-04",
            "film_format": "135", "input_folder": "/tmp/scan",
            "thumb_width": 400, "columns": 6,
        }
        base.update(kw)
        return base

    def fake_output(self, name="a.jpg", style="lightbox"):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0not-really-a-jpeg")
        return {"path": p, "style": style, "width": 100, "height": 80}


# --------------------------------------------------------------------------- #
#  Roll 实体
# --------------------------------------------------------------------------- #

class TestRollEntity(TempStoreCase):
    def test_id_is_stable_for_same_identity(self):
        """同一卷（卷号+日期+胶卷）必须算出同一个 id —— 自动建卷幂等的基础。"""
        a = Roll.compute_id("A01", "2026-03-04", "Portra400")
        b = Roll.compute_id("A01", "2026-03-04", "Portra400")
        self.assertEqual(a, b)

    def test_id_differs_on_any_identity_field(self):
        base = Roll.compute_id("A01", "2026-03-04", "Portra400")
        self.assertNotEqual(base, Roll.compute_id("A02", "2026-03-04", "Portra400"))
        self.assertNotEqual(base, Roll.compute_id("A01", "2026-03-05", "Portra400"))
        self.assertNotEqual(base, Roll.compute_id("A01", "2026-03-04", "Ektar100"))

    def test_id_ignores_case_and_padding(self):
        self.assertEqual(Roll.compute_id(" a01 ", "2026-03-04", "portra400"),
                         Roll.compute_id("A01", "2026-03-04", "Portra400"))

    def test_from_config_maps_info_roll_to_roll_no(self):
        """回归：曾经按 info_roll_no 取值，导致卷号永远为空。"""
        roll = Roll.from_config(self.make_config())
        self.assertEqual(roll.roll_no, "A01")
        self.assertEqual(roll.film, "Portra400")
        self.assertEqual(roll.camera, "M6")
        self.assertEqual(roll.shoot_date, "2026-03-04")

    def test_from_config_snapshots_render_params(self):
        roll = Roll.from_config(self.make_config(thumb_width=800))
        self.assertEqual(roll.render_config["thumb_width"], 800)

    def test_from_config_without_outputs_is_draft(self):
        self.assertEqual(Roll.from_config(self.make_config()).status, STATUS_DRAFT)

    def test_from_config_with_outputs_is_archived(self):
        roll = Roll.from_config(self.make_config(), [Output(path="/x.jpg")])
        self.assertEqual(roll.status, STATUS_ARCHIVED)

    def test_serialization_roundtrip(self):
        roll = Roll.from_config(self.make_config(),
                                [Output(path="/x.jpg", style="contact_sheet",
                                        width=10, height=20)])
        roll.note = "带备注"
        roll.thumbs["main"] = "/t.jpg"
        back = Roll.from_dict(roll.to_dict())
        self.assertEqual(back.id, roll.id)
        self.assertEqual(back.roll_no, roll.roll_no)
        self.assertEqual(back.frame_count, roll.frame_count)
        self.assertEqual(len(back.outputs), 1)
        self.assertEqual(back.outputs[0].style, "contact_sheet")
        self.assertEqual(back.note, "带备注")
        self.assertEqual(back.thumbs["main"], "/t.jpg")

    def test_from_dict_recovers_missing_id(self):
        """旧档案没有 id 字段时按标识补一个，不能所有条目挤成同一条。"""
        roll = Roll.from_dict({"roll_no": "A01", "shoot_date": "2026-03-04",
                               "film": "Portra400"})
        self.assertEqual(roll.id, Roll.compute_id("A01", "2026-03-04", "Portra400"))

    def test_all_construction_paths_agree_on_id(self):
        """回归：__post_init 曾无参调用 compute_id()，导致直接构造的 Roll
        全部拿到「空字段」的同一个 id。from_config 传参掩盖了它，
        但手动补录（create_manual）走的是直接构造，会把不同卷撞成一条。"""
        expect = Roll.compute_id("A01", "2026-03-04", "Portra400")
        self.assertEqual(Roll(roll_no="A01", shoot_date="2026-03-04",
                              film="Portra400").id, expect)
        self.assertEqual(Roll.from_dict({"roll_no": "A01", "shoot_date": "2026-03-04",
                                         "film": "Portra400"}).id, expect)
        self.assertEqual(Roll.from_config(
            {"info_roll": "A01", "info_shoot_date": "2026-03-04",
             "info_film": "Portra400"}).id, expect)

    def test_different_rolls_get_different_ids_via_manual_construction(self):
        """手动补录路径：两卷不同 id，不能被合并成一条。"""
        a = Roll(roll_no="X01", film="HP5")
        b = Roll(roll_no="X02", film="HP5")
        self.assertNotEqual(a.id, b.id)

    def test_from_dict_tolerates_garbage_types(self):
        """脏数据不能让整库读不出来。"""
        before = time.time()
        roll = Roll.from_dict({"id": "r_x", "roll_no": None,
                               "frame_count": "不是数字",
                               "outputs": "不是列表", "thumbs": None,
                               "render_config": None, "created_at": "x"})
        self.assertEqual(roll.frame_count, 0)
        self.assertEqual(roll.outputs, [])
        self.assertEqual(roll.render_config, {})
        self.assertEqual(roll.thumbs, {})
        # 非法时间戳应回退到"现在"，而不是 0（0 会被排到 1970 年）
        self.assertGreaterEqual(roll.created_at, before)

    def test_from_dict_keeps_unknown_fields(self):
        """向前兼容：未知字段不该被丢掉。"""
        roll = Roll.from_dict({"id": "r_x", "roll_no": "A01", "future_field": 42})
        self.assertEqual(roll.render_config.get("_legacy_extras", {}).get("future_field"), 42)

    def test_merge_dedupes_outputs_by_path(self):
        a = Roll.from_config(self.make_config(), [Output(path="/x.jpg", style="lightbox")])
        b = Roll.from_config(self.make_config(), [Output(path="/x.jpg", style="lightbox")])
        a.merge_from(b)
        self.assertEqual(len(a.outputs), 1)

    def test_merge_accumulates_distinct_outputs(self):
        a = Roll.from_config(self.make_config(), [Output(path="/x.jpg", style="lightbox")])
        b = Roll.from_config(self.make_config(), [Output(path="/y.jpg", style="contact_sheet")])
        a.merge_from(b)
        self.assertEqual(len(a.outputs), 2)
        self.assertEqual(a.status, STATUS_ARCHIVED)

    def test_merge_does_not_overwrite_with_empty(self):
        a = Roll.from_config(self.make_config())
        b = Roll.from_config(self.make_config(info_lab="SomeLab", info_roll=""))
        a.merge_from(b)
        self.assertEqual(a.lab, "SomeLab")
        self.assertEqual(a.roll_no, "A01")

    def test_merge_takes_max_frame_count(self):
        a = Roll.from_config(self.make_config()); a.frame_count = 36
        b = Roll.from_config(self.make_config()); b.frame_count = 12
        a.merge_from(b)
        self.assertEqual(a.frame_count, 36)

    def test_normalize_strips_newlines_from_fields(self):
        """字段会直接渲染到界面上，换行会把网格撑歪。"""
        roll = Roll(roll_no="A01\nB02", film="  Portra400  ")
        self.assertNotIn("\n", roll.roll_no)
        self.assertEqual(roll.film, "Portra400")

    def test_year_extracted_from_date(self):
        self.assertEqual(Roll(shoot_date="2026-03-04").year, "2026")
        self.assertEqual(Roll(shoot_date="20260304").year, "2026")

    def test_title_falls_back_sensibly(self):
        self.assertEqual(Roll(roll_no="A01", film="X").title, "A01")
        self.assertEqual(Roll(film="Portra400").title, "Portra400")
        self.assertEqual(Roll().title, "未命名卷宗")

    def test_to_config_restores_render_fields(self):
        roll = Roll.from_config(self.make_config(thumb_width=800))
        cfg = roll.to_config()
        self.assertEqual(cfg["info_roll"], "A01")
        self.assertEqual(cfg["info_film"], "Portra400")
        self.assertEqual(cfg["thumb_width"], 800)
        self.assertEqual(cfg["film_format"], "135")

    def test_to_config_of_empty_roll_is_usable(self):
        self.assertIsInstance(Roll().to_config(), dict)

    def test_output_exists_is_false_for_missing(self):
        self.assertFalse(Output(path="/no/such/file.jpg").exists())

    def test_primary_output_prefers_lightbox(self):
        box = self.fake_output("light.jpg", "lightbox")
        sheet = self.fake_output("contact.jpg", "contact_sheet")
        roll = Roll(outputs=[Output.from_dict(sheet), Output.from_dict(box)])
        self.assertEqual(roll.primary_output.path, box["path"])

    def test_primary_output_skips_files_that_are_gone(self):
        """回归：代表产物文件丢了、但还有别的产物在，缩略图必须出得来。

        早先 primary_output 只看 style 不看 `exists()`，于是「文件不在了 +
        后来补了新产物」的卷永远显示不出缩略图；而且 has_output() 为真，
        卡片连「文件不在了」都不提示 —— 用户只看到一张怎么点都没图的空卡片。
        """
        gone = self.fake_output("gone.jpg", "lightbox")
        roll = Roll(outputs=[Output.from_dict(gone)])
        os.unlink(gone["path"])

        live = self.fake_output("live.jpg", "contact_sheet")
        roll.outputs.append(Output.from_dict(live))
        roll._normalize()

        self.assertTrue(roll.has_output())
        self.assertEqual(roll.primary_output.path, live["path"])

    def test_primary_output_falls_back_to_first_when_nothing_exists(self):
        """全都失联时仍返回第一条 —— 调用方还要靠它区分「没产物」和「丢了」。"""
        gone = self.fake_output("gone.jpg", "lightbox")
        roll = Roll(outputs=[Output.from_dict(gone)])
        os.unlink(gone["path"])
        self.assertIsNotNone(roll.primary_output)
        self.assertFalse(roll.primary_output.exists())

    def test_primary_output_is_none_without_outputs(self):
        self.assertIsNone(Roll().primary_output)


# --------------------------------------------------------------------------- #
#  持久化
# --------------------------------------------------------------------------- #

class TestStore(TempStoreCase):
    def test_empty_store_lists_nothing(self):
        self.assertEqual(self.store.all(), [])

    def test_upsert_and_get(self):
        roll = Roll.from_config(self.make_config())
        self.store.upsert(roll)
        self.assertIsNotNone(self.store.get(roll.id))

    def test_upsert_same_identity_merges_not_duplicates(self):
        """核心不变量：同一卷反复生成只更新一条。"""
        self.store.upsert(Roll.from_config(self.make_config(),
                                           [Output(path=self.fake_output()["path"])]))
        self.store.upsert(Roll.from_config(self.make_config(),
                                           [Output(path=self.fake_output("b.jpg")["path"],
                                                   style="contact_sheet")]))
        rolls = self.store.all()
        self.assertEqual(len(rolls), 1)
        self.assertEqual(len(rolls[0].outputs), 2)

    def test_find_by_identity(self):
        self.store.upsert(Roll.from_config(self.make_config()))
        found = self.store.find_by_identity("A01", "2026-03-04", "Portra400")
        self.assertIsNotNone(found)
        self.assertIsNone(self.store.find_by_identity("ZZZ", "2026-03-04", "Portra400"))

    def test_save_writes_valid_json(self):
        self.store.upsert(Roll.from_config(self.make_config()))
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(len(data["rolls"]), 1)

    def test_reload_from_disk_preserves_data(self):
        self.store.upsert(Roll.from_config(self.make_config()))
        fresh = ArchiveStore(self.path)
        rolls = fresh.all()
        self.assertEqual(len(rolls), 1)
        self.assertEqual(rolls[0].roll_no, "A01")

    def test_corrupt_file_is_backed_up_not_overwritten(self):
        """回归：曾把解析失败当空库静默 pass掉，下次保存就把数据写没了。"""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ not json at all")
        store = ArchiveStore(self.path)
        self.assertEqual(store.all(), [])
        self.assertTrue(store.load_warning)
        backups = [x for x in os.listdir(self.dir) if ".corrupt-" in x]
        self.assertTrue(backups, "损坏文件应被备份")

        # 空库再保存，不会吃掉备份
        store.upsert(Roll.from_config(self.make_config()))
        self.assertTrue(any(".corrupt-" in x for x in os.listdir(self.dir)))

    def test_save_is_atomic_no_temp_left_behind(self):
        self.store.upsert(Roll.from_config(self.make_config()))
        leftovers = [x for x in os.listdir(self.dir)
                     if x.startswith(".archive-") and x.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_delete_removes_only_that_roll(self):
        a = self.store.upsert(Roll.from_config(self.make_config()))
        self.store.upsert(Roll.from_config(self.make_config(info_roll="B02",
                                                           info_film="Ektar100")))
        self.assertTrue(self.store.delete(a.id))
        self.assertEqual(len(self.store.all()), 1)

    def test_delete_unknown_id_returns_false(self):
        self.assertFalse(self.store.delete("r_nope"))

    def test_dedupe_merges_manually_duplicated_entries(self):
        """手改JSON 造成重复 id 时，载入应自动合并而不是显示两张一样的卡。"""
        roll = Roll.from_config(self.make_config())
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1,
                       "rolls": [roll.to_dict(), roll.to_dict()]}, f)
        store = ArchiveStore(self.path)
        self.assertEqual(len(store.all()), 1)

    def test_update_fields_does_not_clobber_with_none(self):
        roll = self.store.upsert(Roll.from_config(self.make_config()))
        self.store.update_fields(roll.id, camera="X100", film=None)
        self.assertEqual(self.store.get(roll.id).camera, "X100")
        self.assertEqual(self.store.get(roll.id).film, "Portra400")

    def test_migrate_bare_array(self):
        """极早期把卷宗直接存成数组。"""
        roll = Roll.from_config(self.make_config())
        data = migrate([roll.to_dict()])
        self.assertEqual(len(data["rolls"]), 1)
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)

    def test_migrate_fills_missing_keys(self):
        data = migrate({})
        self.assertEqual(data["rolls"], [])

    def test_one_bad_record_does_not_wipe_the_library(self):
        """回归：任何一条记录抛异常，整库都会被改名备份、当空库启动。

        用户看到的是「银盐册还是空的」，得自己去目录里翻 `.corrupt-*` 手工修。
        产物层和数据层早就逐条容错了，只有列表层是全有全无。
        """
        good = Roll.from_config(self.make_config())
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1,
                       "rolls": [good.to_dict(),
                                 {"id": "r_poison"},
                                 good.to_dict()]}, f)

        real = Roll.from_dict

        def boom(d):
            if isinstance(d, dict) and d.get("id") == "r_poison":
                raise ValueError("模拟一条读不出来的记录")
            return real(d)

        with mock.patch.object(Roll, "from_dict", staticmethod(boom)):
            store = ArchiveStore(self.path)
            rolls = store.all()

        self.assertEqual(len(rolls), 1, "坏记录只该丢它自己")
        self.assertEqual(rolls[0].roll_no, "A01")
        self.assertIn("1 条", store.load_warning)
        self.assertFalse([x for x in os.listdir(self.dir) if ".corrupt-" in x],
                         "不该整库备份 —— 好数据还要继续用")
        self.assertTrue([x for x in os.listdir(self.dir) if ".damaged-" in x],
                        "被跳过的记录要留副本，否则下次保存就无声丢了")

    def test_load_warning_clears_when_library_is_clean(self):
        self.store.upsert(Roll.from_config(self.make_config()))
        fresh = ArchiveStore(self.path)
        fresh.all()
        self.assertEqual(fresh.load_warning, "")


# --------------------------------------------------------------------------- #
#  日期排序键
# --------------------------------------------------------------------------- #

class TestDateKey(unittest.TestCase):
    """`_date_key` 把随性写的日期收敛成可比较的元组。

    紧凑写法（`20260304`）不是边角情况 —— 它正是本工具自己的产物命名格式。
    """

    def test_splits_compact_year_month_day(self):
        self.assertEqual(S._date_key("20260304"), (2026, 3, 4))

    def test_splits_compact_year_month(self):
        self.assertEqual(S._date_key("202603"), (2026, 3, 0))

    def test_common_separators_agree(self):
        for s in ("2026-03-04", "2026/3/4", "2026.3.4", "2026年3月4日"):
            self.assertEqual(S._date_key(s), (2026, 3, 4), s)

    def test_year_only(self):
        self.assertEqual(S._date_key("2026"), (2026, 0, 0))
        self.assertEqual(S._date_key("2026年"), (2026, 0, 0))

    def test_bare_year_and_compact_date_do_not_collide(self):
        """回归：早先 20260304 被压成 (2026,0,0)，与裸 2026 撞成同一个排序键。"""
        self.assertEqual(S._date_key("20260304"), S._date_key("2026-03-04"))
        self.assertNotEqual(S._date_key("20260304"), S._date_key("2026"))

    def test_compact_dates_keep_their_real_order(self):
        keys = [S._date_key(d) for d in ("20260101", "20260201", "20261231")]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), 3, "三天不能塌成同一个键")

    def test_empty_and_garbage_degrade_to_zero(self):
        self.assertEqual(S._date_key(""), (0, 0, 0))
        self.assertEqual(S._date_key("不知道"), (0, 0, 0))

    def test_never_returns_more_or_fewer_than_three(self):
        for s in ("20260304", "2026", "", "2026-03-04-05", "1月2日3时4分"):
            self.assertEqual(len(S._date_key(s)), 3, s)


# --------------------------------------------------------------------------- #
#  查询与排序
# --------------------------------------------------------------------------- #

class TestQuery(TempStoreCase):
    def setUp(self):
        super().setUp()
        self.store.upsert(Roll.from_config(self.make_config(
            info_roll="A01", info_film="Portra400", info_camera="M6",
            info_shoot_date="2026-03-04")))
        self.store.upsert(Roll.from_config(self.make_config(
            info_roll="B02", info_film="Ektar100", info_camera="X100",
            info_shoot_date="2025-12-31")))
        self.store.upsert(Roll.from_config(self.make_config(
            info_roll="C03", info_film="Portra400", info_camera="FM2",
            info_shoot_date="2026-01-20")))

    def test_keyword_is_case_insensitive(self):
        self.assertEqual(len(self.store.query(keyword="m6")), 1)
        self.assertEqual(len(self.store.query(keyword="PORTRA")), 2)

    def test_keyword_matches_roll_number(self):
        self.assertEqual([r.roll_no for r in self.store.query(keyword="b02")], ["B02"])

    def test_filter_by_film_and_camera(self):
        self.assertEqual(len(self.store.query(film="Portra400")), 2)
        self.assertEqual(len(self.store.query(camera="X100")), 1)

    def test_filter_by_year(self):
        self.assertEqual(len(self.store.query(year="2026")), 2)
        self.assertEqual(len(self.store.query(year="2025")), 1)

    def test_combined_filters(self):
        self.assertEqual(len(self.store.query(film="Portra400", camera="FM2")), 1)

    def test_no_match_returns_empty(self):
        self.assertEqual(self.store.query(keyword="不存在的卷"), [])

    def test_sort_date_desc_puts_dated_before_undated(self):
        self.store.upsert(Roll.from_config(self.make_config(
            info_roll="D04", info_film="HP5", info_camera="X100",
            info_shoot_date="")))     # 无日期
        rolls = self.store.query(sort="date_desc")
        self.assertEqual(rolls[-1].roll_no, "D04")

    def test_sort_date_asc_and_desc_differ(self):
        asc = [r.roll_no for r in self.store.query(sort="date_asc")]
        desc = [r.roll_no for r in self.store.query(sort="date_desc")]
        self.assertEqual(asc, list(reversed(desc)))

    def test_sort_tolerates_irregular_dates(self):
        """用户手填日期很随性，排序不能崩也不能乱。"""
        for i, d in enumerate(["2026/3/4", "20260304", "2026-13-45", "", "2026"]):
            self.store.upsert(Roll.from_config(self.make_config(
                info_roll=f"X{i}", info_film="F", info_camera="C",
                info_shoot_date=d)))
        self.assertEqual(len(self.store.query(sort="date_desc")), 8)
        self.assertEqual(len(self.store.query(sort="date_asc")), 8)

    def test_sort_by_frames(self):
        rows = self.store.query(sort="frames_desc")
        self.assertEqual(len(rows), 3)

    def test_facets_lists_unique_values(self):
        f = self.store.facets()
        self.assertEqual(f["total"], 3)
        self.assertEqual(f["films"], ["Ektar100", "Portra400"])
        self.assertEqual(f["cameras"], ["FM2", "M6", "X100"])
        self.assertEqual(f["years"], ["2026", "2025"])

    def test_facets_counts_orphaned_outputs(self):
        """登记过产物但文件不在了 —— 计入 with_output 且标记 orphaned。"""
        self.store.upsert(Roll.from_config(self.make_config(
            info_roll="Z99", info_film="F", info_camera="C",
            info_shoot_date="2026-05-05"),
            [Output(path="/definitely/not/here.jpg")]))
        f = self.store.facets()
        self.assertEqual(f["total"], 4)
        self.assertEqual(f["orphaned"], 1)


# --------------------------------------------------------------------------- #
#  服务层
# --------------------------------------------------------------------------- #

class TestService(TempStoreCase):
    def setUp(self):
        super().setUp()
        self.svc = ArchiveService(self.store)

    def test_record_render_creates_roll(self):
        roll = self.svc.record_render(self.make_config(),
                                      [self.fake_output()])
        self.assertIsNotNone(roll)
        self.assertEqual(len(self.svc.list_rolls()), 1)

    def test_record_render_is_idempotent(self):
        self.svc.record_render(self.make_config(), [self.fake_output("a.jpg")])
        self.svc.record_render(self.make_config(),
                               [self.fake_output("b.jpg", "contact_sheet")])
        rolls = self.svc.list_rolls()
        self.assertEqual(len(rolls), 1)
        self.assertEqual(len(rolls[0].outputs), 2)

    def test_record_render_records_frame_count(self):
        cfg = self.make_config()
        cfg["frame_count"] = 36
        roll = self.svc.record_render(cfg, [self.fake_output()])
        self.assertEqual(roll.frame_count, 36)

    def test_record_render_respects_disabled_flag(self):
        self.svc.enabled = False
        self.assertIsNone(self.svc.record_render(self.make_config(),
                                                 [self.fake_output()]))
        self.assertEqual(len(self.svc.list_rolls()), 0)

    def test_record_render_skips_totally_empty_roll(self):
        """既没产物也没标识信息 —— 不该造出一张空卡片。"""
        self.assertIsNone(self.svc.record_render({"film_format": "135"}, []))
        self.assertEqual(len(self.svc.list_rolls()), 0)

    def test_create_manual_allows_no_output(self):
        """已拍未冲的底片也要能登记。"""
        roll = self.svc.create_manual({"roll_no": "X01", "film": "HP5"})
        self.assertIsNotNone(roll)
        self.assertEqual(roll.status, STATUS_DRAFT)
        self.assertFalse(roll.auto_created)

    def test_create_manual_rejects_empty(self):
        self.assertIsNone(self.svc.create_manual({}))

    def test_create_manual_allows_note_only(self):
        """只写备注也是有意义的信息，不该被拒。"""
        roll = self.svc.create_manual({"note": "待冲洗"})
        self.assertIsNotNone(roll)
        self.assertEqual(roll.note, "待冲洗")

    def test_add_output_attaches_file(self):
        roll = self.svc.create_manual({"roll_no": "X01"})
        updated = self.svc.add_output(roll.id, self.fake_output()["path"], "lightbox")
        self.assertEqual(updated.status, STATUS_ARCHIVED)
        self.assertEqual(len(updated.outputs), 1)

    def test_add_output_missing_file_sets_error(self):
        roll = self.svc.create_manual({"roll_no": "X01"})
        self.assertIsNone(self.svc.add_output(roll.id, "/no/such.jpg"))
        self.assertIn("不存在", self.svc.last_error)

    def test_add_output_is_idempotent(self):
        roll = self.svc.create_manual({"roll_no": "X01"})
        p = self.fake_output()["path"]
        self.svc.add_output(roll.id, p)
        self.svc.add_output(roll.id, p)
        self.assertEqual(len(self.svc.get(roll.id).outputs), 1)

    def test_update_changes_fields(self):
        roll = self.svc.create_manual({"roll_no": "X01"})
        self.svc.update(roll.id, lab="SomeLab")
        self.assertEqual(self.svc.get(roll.id).lab, "SomeLab")

    def test_delete_keeps_files_by_default(self):
        out = self.fake_output()
        roll = self.svc.record_render(self.make_config(), [out])
        self.assertTrue(self.svc.delete(roll.id))
        self.assertTrue(os.path.isfile(out["path"]),
                        "默认删档案不应删用户的图")

    def test_scan_folder_parses_filenames(self):
        folder = os.path.join(self.dir, "history")
        os.makedirs(folder)
        for n in ["A01_M6_Portra400_20260304_light.jpg",
                  "A01_M6_Portra400_20260304_contact.jpg",
                  "B02_X100_Ektar100_20251231_light.jpg"]:
            with open(os.path.join(folder, n), "wb") as f:
                f.write(b"x")
        made = self.svc.scan_folder(folder)
        self.assertEqual(len(made), 2, "同卷两种风格应归一卷")
        titles = {r.roll_no for r in self.svc.list_rolls()}
        self.assertEqual(titles, {"A01", "B02"})

    def test_scan_folder_missing_dir_sets_error(self):
        self.assertEqual(self.svc.scan_folder("/no/such/dir"), [])
        self.assertTrue(self.svc.last_error)

    def test_ensure_thumb_generates_and_caches(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("需要 Pillow")
        img = os.path.join(self.dir, "src.jpg")
        Image.new("RGB", (600, 400), (200, 100, 50)).save(img)
        roll = self.svc.record_render(
            self.make_config(), [{"path": img, "style": "lightbox"}])
        thumb = self.svc.ensure_thumb(roll)
        self.assertIsNotNone(thumb)
        self.assertTrue(os.path.isfile(thumb))
        # 第二次应命中缓存，不再重算
        self.assertEqual(self.svc.ensure_thumb(roll), thumb)

    def test_ensure_thumb_returns_none_without_output(self):
        roll = self.svc.create_manual({"roll_no": "X01"})
        self.assertIsNone(self.svc.ensure_thumb(roll))

    def test_ensure_thumb_recovers_once_a_live_output_is_added(self):
        """补录产物之后缩略图必须出得来（端到端的回归）。

        路径：产物文件丢了 → 卡片提示「文件不在了」→ 用户「添加产物文件…」
        补一条 → 新条目排在列表末尾 → 若代表产物仍指向那条失联的旧产物，
        缩略图就永远出不来。这正是 primary_output 不看 exists() 的后果。
        """
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("需要 Pillow")
        gone = os.path.join(self.dir, "gone.jpg")
        Image.new("RGB", (600, 400), (200, 100, 50)).save(gone)
        roll = self.svc.record_render(
            self.make_config(), [{"path": gone, "style": "lightbox"}])
        os.unlink(gone)                      # 用户把图挪走了
        self.assertIsNone(self.svc.ensure_thumb(self.store.get(roll.id)),
                          "产物全失联时不该有缩略图")

        live = os.path.join(self.dir, "live.jpg")
        Image.new("RGB", (600, 400), (50, 100, 200)).save(live)
        self.svc.add_output(roll.id, live, "contact_sheet")
        thumb = self.svc.ensure_thumb(self.store.get(roll.id))
        self.assertIsNotNone(thumb, "补了新产物之后缩略图必须能出来")
        self.assertTrue(os.path.isfile(thumb))


# --------------------------------------------------------------------------- #
#  文件名解析（导入已有产物用）
# --------------------------------------------------------------------------- #

class TestArchiveLocation(unittest.TestCase):
    """银盐册位置解析：默认 / 配置 / 环境变量。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="filmsheet-loc-")
        self._old_env = os.environ.pop(S.ENV_ARCHIVE_DIR, None)

    def tearDown(self):
        if self._old_env is not None:
            os.environ[S.ENV_ARCHIVE_DIR] = self._old_env
        else:
            os.environ.pop(S.ENV_ARCHIVE_DIR, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_var_wins(self):
        """环境变量优先级最高 —— 配置损坏时也是逃生通道。"""
        os.environ[S.ENV_ARCHIVE_DIR] = self.tmp
        self.assertEqual(os.path.normpath(S.archive_dir()),
                         os.path.normpath(os.path.abspath(self.tmp)))

    def test_env_var_expands_user(self):
        os.environ[S.ENV_ARCHIVE_DIR] = "~/somewhere"
        self.assertNotIn("~", S.archive_dir())

    def test_falls_back_to_default_without_config(self):
        """配置里没设archive_dir 时用平台标准目录。"""
        self.assertEqual(S.archive_dir(), S.default_config_dir())

    def test_archive_path_uses_dir(self):
        os.environ[S.ENV_ARCHIVE_DIR] = self.tmp
        self.assertEqual(os.path.basename(S.archive_path()), S.ARCHIVE_FILENAME)

    def test_store_follows_location_change(self):
        """改了位置后同一个 store 实例必须跟着换 —— 否则用户以为卷宗丢了。"""
        a = os.path.join(self.tmp, "a")
        b = os.path.join(self.tmp, "b")
        os.makedirs(a)
        os.makedirs(b)
        pa = os.path.join(a, S.ARCHIVE_FILENAME)
        pb = os.path.join(b, S.ARCHIVE_FILENAME)

        os.environ[S.ENV_ARCHIVE_DIR] = a
        svc = ArchiveService()
        svc.create_manual({"roll_no": "X01"})
        self.assertTrue(os.path.isfile(pa))
        before = os.path.getmtime(pa)

        os.environ[S.ENV_ARCHIVE_DIR] = b
        self.assertEqual(len(svc.list_rolls()), 0, "换目录后应读到空库")

        svc.create_manual({"roll_no": "X02"})
        self.assertTrue(os.path.isfile(pb), "新记录应写入新目录")
        with open(pb, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual([r["roll_no"] for r in data["rolls"]], ["X02"])
        # 旧目录的文件不该被动 —— 那是另一个银盐册的数据
        self.assertEqual(os.path.getmtime(pa), before, "旧目录文件不应被改写")
        with open(pa, encoding="utf-8") as f:
            old = json.load(f)
        self.assertEqual([r["roll_no"] for r in old["rolls"]], ["X01"])

    def test_explicit_path_ignores_env(self):
        """显式传路径的实例（如测试）不受环境变量影响。"""
        os.environ[S.ENV_ARCHIVE_DIR] = self.tmp
        p = os.path.join(self.tmp, "explicit.json")
        store = S.ArchiveStore(p)
        store.upsert(Roll.from_config({"info_roll": "A01"}))
        self.assertTrue(os.path.isfile(p))


class TestFilenameParsing(unittest.TestCase):
    def test_parses_default_output_name(self):
        self.assertEqual(parse_stem("A01_M6_Portra400_20260304"),
                         {"roll_no": "A01", "camera": "M6",
                          "film": "Portra400", "shoot_date": "20260304"})

    def test_strips_style_suffix(self):
        """回归：_light 曾被并进胶卷名。"""
        for suffix in ["_light", "_contact", "_single_light_3",
                       "_single_contact_12"]:
            stem = f"A01_M6_Portra400_20260304{suffix}"
            got = parse_stem(stem)
            self.assertEqual(got.get("film"), "Portra400", stem)
            self.assertEqual(got.get("shoot_date"), "20260304", stem)

    def test_slash_date_is_recognised(self):
        """回归：2026/3/4 曾完全不识别，日期被塞进胶卷名。"""
        got = parse_stem("A01_M6_Portra400_2026/3/4")
        self.assertEqual(got.get("shoot_date"), "2026/3/4")
        self.assertEqual(got.get("film"), "Portra400")

    def test_bare_year_is_not_a_full_date(self):
        got = parse_stem("A01_M6_Portra400_2026")
        self.assertNotIn("shoot_date", got)
        self.assertEqual(got.get("film"), "Portra400")

    def test_short_names(self):
        self.assertEqual(parse_stem("A01"), {"roll_no": "A01"})
        self.assertEqual(parse_stem("Portra400_M6"),
                         {"roll_no": "Portra400", "film": "M6"})

    def test_pure_digits_is_still_a_roll_number(self):
        """卷号 007 不该被当成日期吃掉，也不该丢 —— 它就是卷号。"""
        self.assertEqual(parse_stem("007"), {"roll_no": "007"})

    def test_digits_with_roll_and_film(self):
        """卷号是数字、后面跟胶卷时不能误判成日期。"""
        self.assertEqual(parse_stem("007_HP5"),
                         {"roll_no": "007", "film": "HP5"})

    def test_date_detection(self):
        for yes in ["20260304", "2026-03-04", "2026/3/4", "2026.3.4", "202603"]:
            self.assertTrue(_looks_like_date(yes), yes)
        for no in ["2026", "007", "24", "abc", "20260304x", ""]:
            self.assertFalse(_looks_like_date(no), no)

    def test_group_key_groups_styles_of_same_roll(self):
        stem = "A01_M6_Portra400_20260304"
        keys = {_group_key(stem + s) for s in
                ["_light", "_contact", "_single_light_3"]}
        self.assertEqual(len(keys), 1, "同卷不同风格应归一组")


if __name__ == "__main__":
    unittest.main()