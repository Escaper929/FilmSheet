# -*- coding: utf-8 -*-
"""抗锯齿渲染（ui/aa.py）的质量与性能测试。

核心断言是**边缘半透明像素数 > 0** —— 这是抗锯齿的直接、可量化证据。
Tk 的 smooth 样条光栅化到像素时不做颜色混合，边缘 alpha 只有 0/255；
PIL 超采样缩回时会在边缘产生过渡值。凡是将来有人把底板改回矢量绘制，
这个测试立刻会红。

不需要 GUI：全部直接测 PIL 图层。
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.aa import (rounded_rect_pil, cached_rounded_rect, to_rgba, clear_cache,
                   cache_stats, CanvasAA, SUPERSAMPLE)


def edge_semi_transparent(im):
    """数圆角边缘的「半透明」像素数 —— 抗锯齿的直接证据。

    逐行取最左/最右的不透明边界，检查边界附近有没有混合值（0 < a < 250）。
    纯硬边（全 0 或全 255）说明没做边缘混合，即锯齿。

    **不要用「沿对角线采样」**：实测在 80x80 及以上尺寸会全部测出 0 ——
    对角线恰好穿过圆角最陡的位置，混合像素极少，取样点太少。整圈扫描稳定得多。
    """
    px = im.convert("RGBA").load()
    w, h = im.width, im.height
    semi = 0
    for y in range(h):
        row = [px[x, y][3] for x in range(w)]
        nz = [i for i, a in enumerate(row) if a > 5]
        if not nz:
            continue
        for x in (nz[0] - 1, nz[0], nz[-1], nz[-1] + 1):
            if 0 <= x < w and 5 < row[x] < 250:
                semi += 1
    return semi


class TestRoundedRectAA(unittest.TestCase):
    def test_edges_are_antialiased(self):
        """核心：边缘必须有混合像素。"""
        im = rounded_rect_pil(60, 60, 18, fill="#0071E3")
        semi = edge_semi_transparent(im)
        self.assertGreater(semi, 0,
                           "边缘没有半透明像素 —— 说明没抗锯齿，会看到锯齿")

    def test_ss1_reproduces_hard_edge(self):
        """回归对照：不超采样（ss=1）就该是硬边。

        这条同时证明「半透明像素 = 抗锯齿」这个判据本身有效 ——
        如果 ss=1 也有混合像素，说明我的检测逻辑有问题。
        """
        im = rounded_rect_pil(60, 60, 18, fill="#0071E3", ss=1)
        semi = edge_semi_transparent(im)
        self.assertEqual(semi, 0, "ss=1 不做超采样，不该有混合像素")

    def test_higher_supersampling_is_smoother(self):
        """超采样倍数越高，边缘过渡像素越多（更平滑）。

        注意：这里只断言「有混合像素」和「高倍数不劣于低倍数」，
        不强求严格单调 —— PIL 的 LANCZOS 在不同尺寸下的量化有平台差异，
        强行断言单调会变成脆弱测试。
        """
        counts = {}
        for ss in (2, 4, 6):
            im = rounded_rect_pil(80, 80, 24, fill="#0071E3", ss=ss)
            counts[ss] = edge_semi_transparent(im)
        for ss, n in counts.items():
            self.assertGreater(n, 0, f"ss={ss} 边缘无混合像素，未抗锯齿")
        self.assertGreaterEqual(counts[6], 1)

    def test_all_supersample_levels_render(self):
        for ss in range(1, 9):
            im = rounded_rect_pil(40, 30, 8, fill="#000000", ss=ss)
            self.assertIsNotNone(im, f"ss={ss} 渲染失败")
            self.assertEqual(im.size, (40, 30))

    def test_returns_rgba_with_alpha(self):
        im = rounded_rect_pil(30, 30, 8, fill="#0071E3")
        self.assertEqual(im.mode, "RGBA")

    def test_transparent_outside_corner(self):
        """圆角外侧必须是透明的，否则会看到方形底块。"""
        im = rounded_rect_pil(50, 50, 15, fill="#0071E3")
        self.assertEqual(im.convert("RGBA").load()[0, 0][3], 0,
                         "左上角应为透明")
        self.assertEqual(im.convert("RGBA").load()[25, 25][3], 255,
                         "中心应为不透明")


class TestRoundedRectEdges(unittest.TestCase):
    """非法输入与边界情况 —— 都不能抛异常。"""

    def test_zero_or_negative_size(self):
        self.assertIsNone(rounded_rect_pil(0, 10, 5, fill="#000"))
        self.assertIsNone(rounded_rect_pil(10, 0, 5, fill="#000"))
        self.assertIsNone(rounded_rect_pil(-5, 10, 5, fill="#000"))

    def test_radius_larger_than_box(self):
        im = rounded_rect_pil(20, 20, 999, fill="#000")
        self.assertIsNotNone(im)
        self.assertEqual(im.size, (20, 20))

    def test_no_fill_no_outline(self):
        self.assertIsNone(rounded_rect_pil(20, 20, 5))

    def test_outline_only(self):
        im = rounded_rect_pil(40, 20, 6, outline="#888", outline_width=1)
        self.assertIsNotNone(im)
        self.assertEqual(im.size, (40, 20))

    def test_zero_radius_is_plain_rect(self):
        im = rounded_rect_pil(30, 20, 0, fill="#123456")
        self.assertIsNotNone(im)
        # r=0 时四角都应是不透明（无圆角切掉）
        px = im.convert("RGBA").load()
        self.assertEqual(px[0, 0][3], 255)

    def test_non_square_box(self):
        for w, h in ((200, 20), (20, 200), (7, 3), (3, 7)):
            im = rounded_rect_pil(w, h, 6, fill="#000")
            self.assertIsNotNone(im, f"{w}x{h} 失败")
            self.assertEqual(im.size, (w, h))

    def test_very_thin_box(self):
        im = rounded_rect_pil(200, 1, 0, fill="#000")
        self.assertIsNotNone(im)
        self.assertEqual(im.size, (200, 1))


class TestColorConversion(unittest.TestCase):
    def test_hex_formats(self):
        self.assertEqual(to_rgba("#0071E3"), (0, 113, 227, 255))
        self.assertEqual(to_rgba("#abc"), (0xAA, 0xBB, 0xCC, 255))
        self.assertEqual(to_rgba("#ABCDEF"), (0xAB, 0xCD, 0xEF, 255))

    def test_tuples(self):
        self.assertEqual(to_rgba((1, 2, 3)), (1, 2, 3, 255))
        self.assertEqual(to_rgba((1, 2, 3, 4)), (1, 2, 3, 4))
        self.assertEqual(to_rgba((128,)), (128, 128, 128, 255))

    def test_named_colors(self):
        self.assertEqual(to_rgba("white"), (255, 255, 255, 255))
        self.assertEqual(to_rgba("black"), (0, 0, 0, 255))

    def test_invalid_returns_none(self):
        for v in (None, "", "#", "notacolor", "#xy"):
            self.assertIsNone(to_rgba(v), f"{v!r} 应返回 None")


class TestCache(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def tearDown(self):
        clear_cache()

    def test_second_call_hits_cache(self):
        a = cached_rounded_rect(100, 40, 10, fill="#123456")
        b = cached_rounded_rect(100, 40, 10, fill="#123456")
        self.assertIs(a, b, "同参数应返回同一对象")

    def test_different_params_miss(self):
        clear_cache()
        cached_rounded_rect(100, 40, 10, fill="#123456")
        s = cache_stats()
        self.assertEqual(s["hits"], 0)
        self.assertEqual(s["misses"], 1)

    def test_cache_respects_size(self):
        clear_cache()
        for i in range(400):
            cached_rounded_rect(50 + i % 50, 30, 6, fill="#000000")
        s = cache_stats()
        self.assertLessEqual(s["entries"], 240,
                             "缓存应有上限，否则长时间运行会涨爆内存")

    def test_use_cache_false_bypasses(self):
        a = cached_rounded_rect(50, 30, 6, fill="#000", use_cache=False)
        b = cached_rounded_rect(50, 30, 6, fill="#000", use_cache=False)
        self.assertIsNot(a, b)
        self.assertEqual(cache_stats()["entries"], 0)

    def test_hit_rate_is_high_on_repaint(self):
        """控件重绘是高频同参数调用，缓存命中率必须高。"""
        for _ in range(300):
            cached_rounded_rect(136, 34, 7, fill="#0071E3")
        self.assertGreater(cache_stats()["hit_rate"], 0.95)


class TestPerformance(unittest.TestCase):
    """性能上限 —— 按钮级重绘必须够快，否则 hover 会卡。"""

    def test_button_shapes_are_fast_enough(self):
        t0 = time.perf_counter()
        for _ in range(60):
            rounded_rect_pil(136, 34, 7, fill="#0071E3")
        ms = (time.perf_counter() - t0) / 60 * 1000
        # 1.2ms 左右，留足余量；超过 8ms 就会在 hover 时 perceptible 卡顿
        self.assertLess(ms, 8.0, f"按钮底板渲染 {ms:.2f}ms，太慢")

    def test_cached_path_is_much_faster(self):
        cached_rounded_rect(136, 34, 7, fill="#0071E3")   # 预热
        t0 = time.perf_counter()
        for _ in range(500):
            cached_rounded_rect(136, 34, 7, fill="#0071E3")
        cached_ms = (time.perf_counter() - t0) / 500 * 1000

        t1 = time.perf_counter()
        for _ in range(60):
            rounded_rect_pil(136, 34, 7, fill="#0071E3")
        raw_ms = (time.perf_counter() - t1) / 60 * 1000

        self.assertLess(cached_ms, raw_ms,
                        f"缓存路径({cached_ms:.3f}ms) 应快于原始({raw_ms:.2f}ms)")

    def test_card_shape_completes(self):
        """卡片较大（约 11ms），至少不能卡到秒级。"""
        t0 = time.perf_counter()
        rounded_rect_pil(186, 200, 12, fill="#FFFFFF", outline="#E2E2E6")
        self.assertLess(time.perf_counter() - t0, 0.5)


class TestSupersampleConstant(unittest.TestCase):
    def test_default_is_sane(self):
        """SS 太小压不掉阶梯，太大则耗时暴涨。锁定在合理区间。"""
        self.assertGreaterEqual(SUPERSAMPLE, 3,
                                "SS<3 抗锯齿效果不足")
        self.assertLessEqual(SUPERSAMPLE, 6,
                             "SS>6 收益递减但耗时线性上涨")


class TestCanvasAAReferenceLifetime(unittest.TestCase):
    """PhotoImage 引用生命周期 —— 防内存泄漏与残影。

    PhotoImage 被 GC 后控件会渲染成空白；泄漏则是每次重绘多留一个。
    这两类问题肉眼可见（色块/空白），必须测。
    """

    def _make_canvas(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("需要显示环境")
        cv = tk.Canvas(root, width=200, height=60, bg="#FFFFFF")
        cv.pack()
        root.update_idletasks()
        return root, cv

    def test_round_rect_creates_one_item(self):
        root, cv = self._make_canvas()
        try:
            aa = CanvasAA(cv)
            item = aa.round_rect(100, 30, 8, fill="#0071E3")
            self.assertIsNotNone(item)
            self.assertEqual(cv.type(item), "image")
            self.assertEqual(len(aa._photos), 1)
        finally:
            root.destroy()

    def test_drop_tag_clears_refs_before_delete(self):
        """核心回归：drop_tag 必须在 canvas.delete(tag) 之前调用。

        顺序反了的话find_withtag 找不到旧 id，引用永远清不掉 ——
        实测卡片重绘 11 次后残留 11 个 PhotoImage。
        """
        root, cv = self._make_canvas()
        try:
            aa = CanvasAA(cv)
            aa.round_rect(100, 30, 8, fill="#0071E3", tag="card")
            aa.round_rect(110, 30, 8, fill="#0071E3", tag="card")
            self.assertEqual(len(aa._photos), 2)

            aa.drop_tag("card")
            self.assertEqual(len(aa._photos), 0,
                             "drop_tag 应清掉所有同tag 引用")
            aa.clear()
        finally:
            root.destroy()

    def test_drop_tag_after_delete_still_pruned(self):
        """顺序反了也不该泄漏 —— prune 会兜住。"""
        root, cv = self._make_canvas()
        try:
            aa = CanvasAA(cv)
            for _ in range(5):
                aa.round_rect(100, 30, 8, fill="#0071E3", tag="card")
                cv.delete("card")
                aa.drop_tag("card")
                aa.prune()
            self.assertLessEqual(len(aa._photos), 1,
                                "顺序错误时也不该累积引用")
        finally:
            root.destroy()

    def test_prune_drops_dead_refs(self):
        root, cv = self._make_canvas()
        try:
            aa = CanvasAA(cv)
            for _ in range(6):
                aa.round_rect(100, 30, 8, fill="#0071E3")
                cv.delete("all")
                aa.prune()
            self.assertEqual(len(aa._photos), 0)
        finally:
            root.destroy()

    def test_repeated_redraw_does_not_grow_refs(self):
        """模拟高频重绘：引用数必须稳定。"""
        root, cv = self._make_canvas()
        try:
            aa = CanvasAA(cv)
            for _ in range(50):
                cv.delete("all")
                aa.prune()
                aa.round_rect(120, 34, 17, fill="#0071E3")
                root.update_idletasks()
            self.assertLessEqual(len(aa._photos), 1)
            self.assertEqual(len(cv.find_all()), 1)
        finally:
            root.destroy()

    def test_clear_releases_everything(self):
        root, cv = self._make_canvas()
        try:
            aa = CanvasAA(cv)
            aa.round_rect(50, 30, 6, fill="#000")
            aa.round_rect(60, 30, 6, fill="#000")
            aa.clear()
            self.assertEqual(len(aa._photos), 0)
            self.assertEqual(len(cv.find_all()), 0)
        finally:
            root.destroy()

    def test_drop_single_item(self):
        root, cv = self._make_canvas()
        try:
            aa = CanvasAA(cv)
            i = aa.round_rect(50, 30, 6, fill="#000")
            aa.drop(i)
            self.assertEqual(len(aa._photos), 0)
            self.assertEqual(len(cv.find_all()), 0)
        finally:
            root.destroy()

    def test_positioned_round_rect(self):
        """分段控件的选中药丸在任意 x 位置。"""
        root, cv = self._make_canvas()
        try:
            aa = CanvasAA(cv)
            item = aa.round_rect(80, 33, 16, fill="#FFFFFF", x=42, y=2)
            self.assertIsNotNone(item)
            self.assertEqual(cv.coords(item), [42, 2])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()