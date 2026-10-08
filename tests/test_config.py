# -*- coding: utf-8 -*-
"""配置读写（utils/helpers.load_config / save_config）的容错测试。

背景：这两处曾有两个会让应用**启动即崩或丢数据**的缺陷 ——
1. ``save_config`` 直接覆写文件，写到一半崩溃会留下截断 JSON；
2. ``load_config`` 只``except (OSError, FileNotFoundError)``，漏掉
   ``json.JSONDecodeError``（它不是 OSError 的子类），配置一坏就直接抛穿。

这里全部用临时目录跑，绝不碰用户真实配置。
"""

import os
import sys
import json
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.helpers as H


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="filmsheet-config-test-")
        self._orig_dir = H._config_dir
        H._config_dir = lambda: self.tmp          # 隔离到临时目录
        self.cfg_path = os.path.join(self.tmp, H.CONFIG_FILE)

    def tearDown(self):
        H._config_dir = self._orig_dir
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSaveConfig(ConfigTestCase):
    def test_roundtrip(self):
        data = {"pack_images": ["/a.jpg"],
                "templates": {"T": {"thumb_width": 800}},
                "中文键": "中文值"}
        self.assertTrue(H.save_config(data))
        loaded = H.load_config()
        for k, v in data.items():
            self.assertEqual(loaded.get(k), v, k)

    def test_returns_true_on_success(self):
        self.assertIs(H.save_config({"signature": "x"}), True)

    def test_no_temp_file_left_behind(self):
        """原子写不该在配置目录留下 .config-*.tmp 垃圾。"""
        H.save_config({"a": 1})
        leftovers = [f for f in os.listdir(self.tmp) if f.startswith(".config-")]
        self.assertEqual(leftovers, [])

    def test_failed_write_keeps_old_file_intact(self):
        """核心回归：写失败时旧配置必须完好，而不是被截断成半截 JSON。"""
        good = {"templates": {"T": {"thumb_width": 800}}, "signature": "keepme"}
        H.save_config(good)
        before = read_text(self.cfg_path)

        # 传一个 JSON 无法序列化的对象 -> 写到中途必然失败
        self.assertIs(H.save_config({"bad": object()}), False)

        after = read_text(self.cfg_path)
        self.assertEqual(before, after, "失败的写入不该改动旧配置")
        self.assertEqual(json.loads(after)["signature"], "keepme")

    def test_failed_write_leaves_no_temp_file(self):
        H.save_config({"a": 1})
        H.save_config({"bad": object()})
        leftovers = [f for f in os.listdir(self.tmp) if f.startswith(".config-")]
        self.assertEqual(leftovers, [])

    def test_creates_dir_if_missing(self):
        nested = os.path.join(self.tmp, "sub", "deep")
        H._config_dir = lambda: nested
        self.assertTrue(H.save_config({"a": 1}))
        self.assertTrue(os.path.isfile(os.path.join(nested, H.CONFIG_FILE)))


class TestLoadConfigRobustness(ConfigTestCase):
    def test_broken_json_does_not_crash(self):
        """核心回归：JSONDecodeError 不是 OSError子类，原来会被漏掉直接抛穿，
        配置文件一损坏应用就启动即崩。"""
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")
        cfg = H.load_config()# 关键：不抛异常
        self.assertIsInstance(cfg, dict)
        self.assertEqual(cfg["templates"], {})

    def test_broken_json_is_backed_up(self):
        """损坏的文件要改名备份，不能原地留着等下次被空配置覆盖。"""
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("{ broken")
        H.load_config()
        backups = [f for f in os.listdir(self.tmp) if ".corrupt-" in f]
        self.assertEqual(len(backups), 1, "损坏配置应被备份")

    def test_truncated_file_does_not_crash(self):
        """写到一半断电留下的截断文件，是最真实的损坏形态。"""
        H.save_config({"templates": {"T": {"thumb_width": 800}},
                       "signature": "0123456789" * 20})
        raw = read_text(self.cfg_path)
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write(raw[:len(raw) // 2])           # 模拟写到一半
        self.assertIsInstance(H.load_config(), dict)

    def test_non_dict_root_does_not_crash(self):
        """早期版本可能把配置写成数组。update 会抛 TypeError。"""
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        cfg = H.load_config()
        self.assertIsInstance(cfg, dict)
        self.assertEqual(cfg["templates"], {})

    def test_backup_survives_subsequent_saves(self):
        """备份文件不能被后续的正常保存吃掉 —— 它是用户唯一的找回机会。"""
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("{ broken")
        H.load_config()
        H.save_config({"templates": {"A": {}}})
        backups = [f for f in os.listdir(self.tmp) if ".corrupt-" in f]
        self.assertEqual(len(backups), 1, "备份应持续存在")

    def test_missing_file_returns_defaults(self):
        cfg = H.load_config()
        self.assertIsInstance(cfg, dict)
        self.assertEqual(cfg["render_style"], "lightbox")
        self.assertEqual(cfg["pack_images"], [])

    def test_invalid_render_style_is_reset(self):
        """历史上被移除的样式名不该让界面拿到非法值。"""
        H.save_config({"render_style": "some_removed_style"})
        self.assertEqual(H.load_config()["render_style"], "lightbox")

    def test_empty_file_returns_defaults(self):
        open(self.cfg_path, "w").close()
        self.assertIsInstance(H.load_config(), dict)

    def test_unicode_path_and_values(self):
        """中文路径与中文内容都得能正常读写。"""
        H._config_dir = lambda: os.path.join(self.tmp, "配置目录")
        data = {"pack_images": ["/用户/图片/卷 01.jpg"],
                "lab": "广州显影室", "note": "反差偏大"}
        self.assertTrue(H.save_config(data))
        loaded = H.load_config()
        self.assertEqual(loaded["lab"], "广州显影室")
        self.assertEqual(loaded["pack_images"], ["/用户/图片/卷 01.jpg"])


if __name__ == "__main__":
    unittest.main()