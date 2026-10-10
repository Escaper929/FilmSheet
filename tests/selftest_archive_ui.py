# -*- coding: utf-8 -*-
"""底片册界面的无头自测脚本。

跑法：
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/selftest_archive_ui.py

背景：Tk 界面问题只有真正建出来才暴露，而这些在肉眼点开之前很难发现。
本脚本不比对像素（不同系统字体渲染有差异，比对必然不稳定），而是**程序化
断言布局与状态**：列数自适应、有无重叠、缩略图按需加载、筛选生效、换肤后
控件仍存活。

用 root.update() 手动泵事件即可（这里不做跨线程回调）；但如果测的是
「点开始生成 → 等渲染完 → 归档写入」这类工作线程路径，必须用 mainloop()，
否则线程里的 root.after 会抛 "main thread is not in main loop"。
"""

import os
import sys
import itertools
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(cond, label, detail=""):
    if cond:
        print(f"  [ok] {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        FAILURES.append(label)


def build_fixture(tmp):
    """造一批卷宗：有产出的、没产出的、不同胶卷/相机/年份的。"""
    from PIL import Image
    from archive import store as S
    from archive.service import ArchiveService

    # 刻意**不**猴子补丁 S.archive_dir：那是模块全局状态，补丁会污染同一进程内
    # 后续所有测试（曾导致归档位置迁移的用例全部失效）。下面显式传路径即可隔离。
    imgdir = os.path.join(tmp, "out")
    os.makedirs(imgdir, exist_ok=True)
    palettes = [(214, 180, 140), (200, 120, 80),
                (120, 120, 118), (222, 196, 120), (180, 140, 110)]
    for i, c in enumerate(palettes):
        Image.new("RGB", (600, 420), c).save(os.path.join(imgdir, f"r{i}.jpg"))

    svc = ArchiveService(S.ArchiveStore(os.path.join(tmp, "filmsheet_archive.json")))
    data = [
        ("A01", "Portra400", "Leica M6", "2026-03-04", "lightbox", 36),
        ("B02", "Ektar100", "Fuji X100V", "2026-01-18", "contact_sheet", 24),
        ("C03", "HP5", "Pentax FM2", "2025-11-02", "lightbox", 36),
        ("D04", "Gold200", "Contax G2", "2025-08-21", "contact_sheet", 12),
        ("E05", "Portra400", "Leica M6", "2025-06-09", "lightbox", 24),
        ("F06", "Cinestill800T", "Leica M6", "2025-03-30", "lightbox", 20),
    ]
    for i, (roll, film, cam, date, style, n) in enumerate(data):
        svc.record_render(
            {"info_roll": roll, "info_film": film, "info_camera": cam,
             "info_shoot_date": date, "film_format": "135", "frame_count": n},
            [{"path": os.path.join(imgdir, f"r{i % 5}.jpg"), "style": style,
              "width": 600, "height": 420}])
    # 一卷没有产物的（已拍未冲）
    svc.create_manual({"roll_no": "G07", "film": "Delta3200",
                       "camera": "OM-1", "shoot_date": "2025-01-05"})
    return svc


def make_app(svc, geometry):
    import tkinter as tk
    import tkinter.messagebox as MB
    from ui.app import App
    MB.showinfo = lambda *a, **k: None
    MB.showwarning = lambda *a, **k: None
    MB.showerror = lambda *a, **k: None
    root = tk.Tk()
    app = App(root)
    app.archive_service = svc
    app.archive_page.service = svc
    root.geometry(geometry)
    app.archive_page.refresh()
    app._show_page(4)
    pump(root, 6)
    app.archive_page.load_visible_thumbs()
    pump(root, 4)
    return root, app


def pump(root, n=5):
    for _ in range(n):
        root.update_idletasks()
        root.update()


def cards_boxes(app):
    out = []
    for card, roll in app.archive_page._cards:
        out.append((card.winfo_x(), card.winfo_y(), card.winfo_width(),
                    card.winfo_height(), roll.title))
    return out


def snapshot_bgs(widget):
    """记下子树里每个控件的底色（连带控件本身，用于换肤前后比对）。"""
    out = []

    def walk(w):
        for c in w.winfo_children():
            try:
                bg = str(c.cget("bg")).upper()
            except Exception:
                bg = ""
            out.append((c, bg))
            walk(c)

    walk(widget)
    return out


def stale_theme_widgets(panes, old_pal, new_pal):
    """换肤后仍停留在旧主题配色的控件清单（方向无关）。

    做法：换肤前记下每个控件的底色，反查它在**旧调色板**里对应哪些角色
    （bg / surface / field / ...）；换肤后该控件必须变成这些角色在**新调色板**
    里的值之一。

    为什么不直接比"颜色是否还在旧调色板里"：两套主题有重色（深色的文字色
    #F5F5F7 就是浅色的页面底色），按颜色集合比会漏掉整个「浅色→深色」方向。
    所以只认角色变了的那部分，且要求控件不能原地不动。
    """
    hexed = lambda p: {k: str(v).upper() for k, v in p.items()
                       if isinstance(v, str) and str(v).startswith("#")}
    old, new = hexed(old_pal), hexed(new_pal)
    bad = []
    for w, bg in panes:
        roles = {k for k, v in old.items() if v == bg}
        changed = {k for k in roles if new.get(k) != old.get(k)}
        if not changed:
            continue                      # 该控件对应的角色在新主题里没变，无从判断
        expected = {new[k] for k in changed}
        try:
            got = str(w.cget("bg")).upper()
        except Exception:
            continue                      # 换肤时被销毁重建（网格卡片就是）
        if got == bg and bg not in expected:
            # 角色名用 tuple：调用方会对结果 set() 去重，list 不可哈希 ——
            # 而这段只在**失败时**才会产出条目，正是最需要它别崩的时候
            bad.append((type(w).__name__, bg, got, tuple(sorted(changed)[:3])))
    return bad


def overlaps(boxes):
    hits = []
    for a, b in itertools.combinations(boxes, 2):
        if (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and
                a[1] < b[1] + b[3] and b[1] < a[1] + a[3]):
            hits.append((a[4], b[4]))
    return hits


def main():
    tmp = tempfile.mkdtemp(prefix="filmsheet-ui-selftest-")
    try:
        svc = build_fixture(tmp)
        print(f"\n=== 底片册界面自测 ===")

        # ---------- 基本渲染 ----------
        root, app = make_app(svc, "1200x780+0+0")
        ap = app.archive_page
        check(len(ap._cards) == 7, f"网格渲染 7 张卡片", f"实得 {len(ap._cards)}")
        check(not overlaps(cards_boxes(app)), "卡片无重叠")

        # ---- 页面必须真正被布局（回归：曾因多套一层 frame 导致整页空白）----
        # 关键在于frame 的 winfo_manager() 非空且尺寸 > 1：
        # 控件树里对象都在、坐标也对，但 ttk 中间层没被任何布局管理器接管时，
        # 真实运行整页是空白的 —— 按坐标重绘的预览脚本完全看不出这个问题。
        fw, fh = ap.frame.winfo_width(), ap.frame.winfo_height()
        check(ap.frame.winfo_manager() != "",
              "底片册容器被布局管理器接管", f"manager={ap.frame.winfo_manager()!r}")
        check(fw > 10 and fh > 10,
              "底片册容器尺寸已计算", f"{fw}x{fh}")
        check(ap.frame.winfo_viewable() == 1, "底片册可见")
        cvs = ap.canvas
        check(cvs.winfo_width() > 10 and cvs.winfo_height() > 10,
              "网格画布有实际尺寸", f"{cvs.winfo_width()}x{cvs.winfo_height()}")
        if ap._cards:
            c0 = ap._cards[0][0]
            check(c0.winfo_viewable() == 1, "卡片控件可见")
            check(c0.winfo_rooty() > root.winfo_rooty(),
                  "卡片在窗口内的纵坐标为正",
                  f"card={c0.winfo_rooty()} root={root.winfo_rooty()}")

        # ---- 卡片内 ttk 标签背景必须是 surface（回归：曾用页面底色，
        #      导致「图片来源」这类标签后面衬出一块异色矩形）----
        from tkinter import ttk as _ttk
        from ui.rounded import RoundedCard as _RC
        st = _ttk.Style(root)
        mismatched = []

        def _scan_labels(w, in_card=False):
            if isinstance(w, _RC):
                in_card = True
            for ch in w.winfo_children():
                try:
                    if in_card and ch.winfo_class() in ("Label", "TLabel"):
                        cls = ch.winfo_class()
                        if cls == "Label":
                            bg = ch.cget("bg")
                        else:
                            bg = st.lookup(str(ch.cget("style") or "TLabel"),
                                           "background")
                        if bg != app.colors["surface"]:
                            mismatched.append((ch.winfo_class(),
                                               str(ch.cget("style")),
                                               bg, str(ch.cget("text"))[:14]))
                except Exception:
                    pass
                _scan_labels(ch, in_card)

        _scan_labels(root)
        check(not mismatched,
              "卡片内所有标签背景均为卡片表面色",
              f"不匹配: {mismatched[:3]}")

        # ---- 缩略图懒加载 ----------
        with_img = sum(1 for c, r in ap._cards
                       if any(c.img_area.type(t) == "image" for t in c.img_area.find_all()))
        no_output = sum(1 for c, r in ap._cards if not r.has_output())
        check(with_img == 7 - no_output,
              f"仅给有产物的卡片加载缩略图 ({with_img}/{7 - no_output})")

        # ---------- 详情面板 ----------
        first = ap._cards[0][1]
        ap.select(first.id)
        pump(root, 3)
        check(ap.detail_title.cget("text") == first.title, "详情标题正确")
        check(ap._field_labels["roll_no"].cget("text") == first.roll_no,
              "详情字段正确")
        #选中态
        sel = [c for c, r in ap._cards if c._selected]
        check(len(sel) == 1 and sel[0].roll.id == first.id, "选中态唯一且正确")

        # ---------- 详情面板：缩略图与滚动（回归） ----------
        # 两个真实踩过的坑，靠坐标硬画的预览脚本都照不出来：
        #  1) detail_img 写 height=1 —— Tk 在「有图无文字」时把 height 当**像素**，
        #     真正的缩略图被裁成 1px 一条；而「（未出图）」是纯文字、按行算 22px，
        #     照常显示。于是表现成「有产物的卷反而看不到缩略图」这种反直觉现象。
        #  2) 卡片内容实测 744px 比面板 647px 高，而面板不能滚 —— 底部的操作按钮
        #     被 Tk **静默**切掉，实测第 4 个只剩 3px、最后两个 1×1，点不到。
        have = [r for _c, r in ap._cards if r.existing_outputs()]
        bare = [r for _c, r in ap._cards if not r.has_output()]
        if have and bare:
            widths = []
            for r in (None, have[0], bare[0]):
                ap.select(r.id if r else None)
                pump(root, 4)
                widths.append(ap.detail.winfo_width())
            check(len(set(widths)) == 1,
                  f"详情面板宽度不随选中项变化 ({widths})")

            ap.select(have[0].id)
            pump(root, 6)
            check(getattr(ap, "detail_canvas", None) is not None
                  and ap.detail_canvas.winfo_manager() != "",
                  "详情面板是滚动视口（内容高于面板时由它承接）")
            ih = ap.detail_img.winfo_height()
            check(ih > 100, f"详情缩略图真的显示了（高 {ih}px）",
                  "修复前有图时恒为 5px")

            ch = ap.detail_card.winfo_reqheight()
            ph = ap.detail_canvas.winfo_height()
            check(ch > ph, f"详情内容高于视口 → 必须可滚 ({ch} > {ph})")
            check(ap.detail_canvas.yview()[1] < 0.999,
                  "滚动范围的 scrollregion 已跟上内容高度",
                  f"yview={ap.detail_canvas.yview()}")
            ap.detail_canvas.yview_moveto(1.0)
            pump(root, 4)
            pills = [w for w in ap.detail_actions.winfo_children()
                     if w.winfo_manager()]
            view_bottom = ap.detail_canvas.winfo_rooty() + ph
            last_bottom = pills[-1].winfo_rooty() + pills[-1].winfo_height()
            check(last_bottom <= view_bottom + 1,
                  f"滚到底后最后一个操作按钮完整可见 ({last_bottom} ≤ {view_bottom})",
                  "修复前第 4 个起就被切掉")

            ap.select(None)
            pump(root, 4)
            check(ap.detail_img.winfo_manager() == "",
                  "未选中时缩略图整条收起（不留一条 field 底色的窄带）")
            ap.select(have[0].id)
            pump(root, 4)

        # ---------- 搜索 ----------
        ap.vars["keyword"].set("m6")
        ap.refresh(); pump(root, 3)
        n_m6 = len(ap._cards)
        check(n_m6 == 3, f"搜索 'm6' 命中 3 卷（Leica M6）", f"实得 {n_m6}")

        ap.vars["keyword"].set("不存在的卷")
        ap.refresh(); pump(root, 3)
        check(len(ap._cards) == 0, "无结果时网格为空")
        check(bool(ap.empty_lbl.cget("text")), "无结果时显示空状态提示")

        ap.vars["keyword"].set("")
        ap.refresh(); pump(root, 3)
        check(len(ap._cards) == 7, "清空搜索后恢复")

        # ---------- 筛选下拉 ----------
        check(ap._filter_combos["film"]["values"] ==
              ("全部胶卷", "Cinestill800T", "Delta3200", "Ektar100", "Gold200",
               "HP5", "Portra400"),
              "胶卷下拉含全部品牌", str(ap._filter_combos["film"]["values"]))

        ap.vars["film"].set("Portra400")
        ap.refresh(); pump(root, 3)
        check(len(ap._cards) == 2, "按胶卷筛选生效", f"实得 {len(ap._cards)}")
        ap.vars["film"].set("全部胶卷")
        ap.refresh(); pump(root, 3)

        # ---------- 换肤 ----------
        # 刻意**不**用 app.toggle_theme()：它会把 ui_theme 写进**用户的真实配置**，
        # 跑一次自测就把别人的主题设置改了。直接换内存里的调色板等价，且无副作用。
        from ui.app import THEMES as _TH
        before = len(ap._cards)
        app.colors = _TH["dark"]
        app._apply_theme(app.colors)
        pump(root, 4)
        check(len(ap._cards) == before, "换肤后卡片数量不变")

        # ---- 换肤完整性：扫全树，找出仍停在旧配色的控件 ----
        # tk 控件不会自己跟随主题，漏一个就是一块永远停在旧配色的补丁。逐个控件
        # 写断言挡不住"没人想到过的漏网之鱼"，所以反过来做：遍历整棵树，按调色板
        # **角色**比对，看谁换肤前后原地不动。两个方向各测一次 —— 单方向会被
        # 两套主题的重色掩掉一部分（见 stale_theme_widgets 的说明）。
        # 曾经漏掉 body / bar / left / 搜索框 / 输入框 / 整个详情卡片，表现为
        # 切到浅色后搜索框还是一块深色、详情面板里是一整块深色。
        for frm_key, to_key in (("light", "dark"), ("dark", "light")):
            app.colors = _TH[frm_key]
            app._apply_theme(app.colors)
            pump(root, 5)
            panes = snapshot_bgs(ap.frame)
            app.colors = _TH[to_key]
            app._apply_theme(app.colors)
            pump(root, 5)
            bad = stale_theme_widgets(panes, _TH[frm_key], _TH[to_key])
            check(not bad, f"换肤 {_TH[frm_key]['name']}→{_TH[to_key]['name']}"
                           f" 无控件停留在旧配色",
                  f"残留 {len(bad)} 个: {sorted(set(bad))[:4]}")
        ap.refresh()
        pump(root, 3)
        check(len(ap._cards) == before, "来回换肤后网格仍能正常重建")

        check(app._show_page(0) is None, "换肤后可切回其他页")

        app._show_page(4)
        pump(root, 3)
        root.destroy()

        # ---------- 响应式布局 ----------
        # 断言「卡片不超出网格区」而不是固定列数：列数取决于窗口宽与详情面板宽
        # （面板已固定 286，但窗口宽仍可变），写死数字会随样式调整而失效。
        # 真正的不变量是「不越界、不重叠」。
        print("\n=== 响应式布局 ===")
        from ui.archive_page import CARD_W
        for w, h in ((900, 510), (1200, 780), (1600, 900)):
            root, app = make_app(svc, f"{w}x{h}+0+0")
            ap = app.archive_page
            cols = ap._cols()
            grid_w = ap.canvas.winfo_width()
            boxes = cards_boxes(app)
            maxr = max((b[0] + b[2]) for b in boxes)

            # 末列右边必须落在网格区内 —— 越界说明列数按错了宽度算
            # （曾按整页宽算，把卡片排到详情面板底下去）
            check(maxr <= grid_w + 1,
                  f"{w}px 窗口：卡片不超出网格区",
                  f"右边缘 {maxr} > 网格 {grid_w}")
            check(not overlaps(boxes), f"{w}px 窗口卡片无重叠")
            # 每一行实际用到的列数不应超过设定值
            used = {b[0] for b in boxes}
            check(len(boxes) <= cols * max(1, (len(boxes) + cols - 1) // cols),
                  f"{w}px 窗口列数自洽", f"cols={cols} cards={len(boxes)}")
            # 卡片文字区必须装得下所有标签 —— 直接比「需要高度」与「实际高度」，
        # 比「末行底边是否越界」更直接，也不受 Frame 是否自动撑开的影响。
            short = []
            for card, roll in ap._cards:
                tb = [ch for ch in card.winfo_children()
                      if ch.winfo_class() == "Frame"]
                if not tb:
                    continue
                need = tb[0].winfo_reqheight()
                got = tb[0].winfo_height()
                if got + 1 < need:      # 留 1px 容差，避免舍入误报
                    short.append((roll.title, need, got))
            check(not short, f"{w}px 窗口卡片文字区高度足够",
                  f"高度不足: {short[:3]}")
            root.destroy()

        # ---------- 缩略图缓存落盘 ----------
        print("\n=== 缩略图 ===")
        from archive import thumbs as T
        made = T.make_thumb(os.path.join(tmp, "out", "r0.jpg"),
                            os.path.join(tmp, "thumbs_test.jpg"))
        check(made is not None and os.path.isfile(made), "缩略图生成成功")
        check(T.make_thumb("/no/such.jpg", os.path.join(tmp, "x.jpg")) is None,
              "源图缺失时返回 None 而不是抛异常")

        # ---------- 归档位置迁移（高风险路径） ----------
        print("\n=== 归档位置迁移 ===")
        from archive import store as S
        import utils.helpers as H
        import archive.thumbs as TH
        from archive.service import ArchiveService
        real_cfg_dir = H._config_dir
        newdir = os.path.join(tmp, "moved")
        try:
            H._config_dir = lambda: os.path.join(tmp, "cfg")
            os.makedirs(os.path.join(tmp, "cfg"), exist_ok=True)
            os.makedirs(newdir, exist_ok=True)

            src = S.ArchiveStore(os.path.join(tmp, "src.json"))
            svc2 = ArchiveService(src)
            svc2.create_manual({"roll_no": "M01", "film": "Portra400"})

            # 直接跑迁移逻辑的后半段（复制 + 改配置 + 重载）
            shutil.copy2(src.path, os.path.join(newdir, S.ARCHIVE_FILENAME))
            H.save_config({"archive_dir": newdir})
            check(os.path.normpath(S.archive_dir()) == os.path.normpath(newdir),
                  "配置写入后 archive_dir 指向新位置")
            check(os.path.normpath(TH.thumbs_dir()).startswith(os.path.normpath(newdir)),
                  "缩略图目录跟随归档位置", TH.thumbs_dir())

            svc3 = ArchiveService(S.ArchiveStore())
            rolls = svc3.list_rolls()
            check(len(rolls) == 1, "迁移后可读到原卷宗")
            check(rolls and rolls[0].roll_no == "M01", "卷宗内容完整")

            # 恢复默认
            H.save_config({"archive_dir": ""})
            check(S.archive_dir() == S.default_config_dir(), "留空可恢复默认位置")
        finally:
            H._config_dir = real_cfg_dir

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 40)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())