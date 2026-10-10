# -*- coding: utf-8 -*-
"""把归档库页面渲染成 PNG —— 不依赖屏幕捕获，直接从 Tk 控件树自绘。

用途：在拿不到前台窗口（多显示器、辅助功能权限受限、CI 环境）时，
仍能"看见"归档库长什么样。

跑法：
    FS_THEME=light FS_OUT=/tmp/a.png python3 tests/preview_archive_ui.py
    FS_THEME=dark  FS_OUT=/tmp/b.png python3 tests/preview_archive_ui.py

注意：这是**近似还原**，不是屏幕截图 —— 自绘控件（Canvas 图元）按
主题色重绘而非读原始像素。用于确认布局、层级、信息密度足够；
要像素级验证仍需真实运行。

做法：遍历窗口内所有控件，读出它们的坐标/尺寸/文本/颜色，用 PIL 重绘。
这不是像素级还原（自绘控件的 Canvas 图元要额外处理），但足以看清
布局、层级、配色和信息密度 —— 也就是看界面"长什么样"。
"""
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import tkinter as tk, tkinter.messagebox as MB
THEME = os.environ.get("FS_THEME", "")
MB.showinfo = lambda *a, **k: None
from ui.app import App
from ui.archive_page import CARD_IMG_H
from PIL import Image, ImageDraw, ImageFont

W, H = 1400, 860
THEME = os.environ.get("FS_THEME", "")
if THEME:
    import utils.helpers as _H
    _cfg = _H.load_config(); _cfg["ui_theme"] = THEME; _H.save_config(_cfg)
root = tk.Tk()
app = App(root)
root.geometry(f"{W}x{H}+0+0")
app.archive_page.refresh()
app._show_page(4)
for _ in range(10): root.update_idletasks(); root.update()
ap = ap_ = app.archive_page
ap.load_visible_thumbs()
for _ in range(10): root.update_idletasks(); root.update()
ap.select(ap._cards[0][1].id)
for _ in range(6): root.update_idletasks(); root.update()

img = Image.new("RGB", (W, H), app.colors["bg"])
d = ImageDraw.Draw(img)

def font(sz, bold=False):
    for p in ["/System/Library/Fonts/PingFang.ttc",
              "/System/Library/Fonts/Helvetica.ttc"]:
        if os.path.exists(p):
            try:
                idx = 4 if ("PingFang" in p and bold) else (2 if bold else 0)
                return ImageFont.truetype(p, sz, index=idx)
            except Exception:
                continue
    return ImageFont.load_default()

F = lambda s, b=False: font(s, b)

def rgb(c):
    try:
        if isinstance(c, str) and c.startswith("#"):
            return tuple(int(c[i:i+2],16) for i in (1,3,5))
    except Exception:
        pass
    return (200,200,200) if not isinstance(c,str) or c=="white" else (255,255,255)

# --- 顶部导航栏 ---
navy = 16
nav_h = app.nav.winfo_height()
d.rounded_rectangle([16, navy, W-16, navy+nav_h], radius=10, fill=rgb(app.colors["seg_track"]))
# 选中药丸（第5项）
n = len(app._pages)
seg_w = (W-32)/n
d.rounded_rectangle([16+seg_w*4+3, navy+3, 16+seg_w*5-3, navy+nav_h-3],
                    radius=9, fill=rgb(app.colors["seg_thumb"]))
for i,(t,ic) in enumerate([("输入与输出",0),("画幅与排版",1),("外观",2),("拍摄信息",3),("归档库",4)]):
    cx = 16 + seg_w*(i+0.5)
    col = rgb(app.colors["accent"]) if i==4 else rgb(app.colors["fg"])
    bb = d.textbbox((0,0), t, font=F(12, i==4))
    d.text((cx-(bb[2]-bb[0])/2, navy+nav_h/2-(bb[3]-bb[1])/2-1), t,
           font=F(12, i==4), fill=col)

# --- 内容区：从归档库页真实几何取 ---
top = navy + nav_h + 12
bar_h = ap.count_lbl.winfo_height()+26
# 工具栏：搜索框 + 下拉
d.rounded_rectangle([16, top, 214, top+bar_h], radius=6, fill=rgb(app.colors["field"]),
                    outline=rgb(app.colors["border"]))
d.text((30, top+bar_h/2-7), "搜索卷号/ 胶卷 / 相机", font=F(11), fill=rgb(app.colors["fg_muted"]))
labels = ["全部胶卷","全部相机","全部年份","拍摄日期 ↓"]
x = 222
for i,lb in enumerate(labels):
    wlab = 108
    d.rounded_rectangle([x, top, x+wlab, top+bar_h], radius=6, fill=rgb(app.colors["field"]),
                        outline=rgb(app.colors["border"]))
    d.text((x+10, top+bar_h/2-7), lb, font=F(11), fill=rgb(app.colors["fg"]))
    d.text((x+wlab-18, top+bar_h/2-6), "⌄", font=F(11), fill=rgb(app.colors["fg_muted"]))
    x += wlab+8

# 右侧操作按钮
for txt in ["归档位置","导入已有产物","补录卷宗"]:
    tw = d.textbbox((0,0), txt, font=F(11))[2] + 24
    x -= tw
    d.rounded_rectangle([x, top, x+tw, top+bar_h], radius=6, fill=rgb(app.colors["surface"]),
                        outline=rgb(app.colors["border"]))
    d.text((x+12, top+bar_h/2-7), txt, font=F(11), fill=rgb(app.colors["fg"]))
    x -= 8

# 计数
cnt = ap.count_lbl.cget("text")
bb = d.textbbox((0,0), cnt, font=F(11))
d.text((x-8-(bb[2]-bb[0]), top+bar_h/2-7), cnt, font=F(11), fill=rgb(app.colors["fg_muted"]))

# --- 网格卡片 ---
grid_top = top + bar_h + 10
sel_id = ap._selected_id
for card, roll in ap._cards:
    cx0 = card.winfo_rootx() - root.winfo_rootx() + card.winfo_x() - ap.canvas.winfo_rootx() + ap.canvas.winfo_rootx()
    # 用 grid_info 换算到窗口坐标
    gx = ap.grid_host.winfo_rootx() - root.winfo_rootx() + card.winfo_x()
    gy = ap.grid_host.winfo_rooty() - root.winfo_rooty() + card.winfo_y()
    cw, ch = card.winfo_width(), card.winfo_height()
    if gy > H-10: continue
    selected = roll.id == sel_id
    d.rounded_rectangle([gx, gy, gx+cw, gy+ch], radius=8,
                        fill=rgb(app.colors["surface"]),
                        outline=rgb(app.colors["accent"]) if selected else rgb(app.colors["border"]),
                        width=2 if selected else 1)
    # 样片区
    ih = min(CARD_IMG_H, ch)
    d.rounded_rectangle([gx+1, gy+1, gx+cw-1, gy+ih], radius=7, fill=rgb(app.colors["field"]))
    # 真实缩略图
    img_items = [t for t in card.img_area.find_all() if card.img_area.type(t)=="image"]
    if img_items and card._photo is not None:
        try:
            src = card._photo._PhotoImage__photo
            data = src.data()  # Tk PhotoImage 的原始数据比较麻烦，改用 PIL 重读
        except Exception:
            pass
    # 直接从磁盘读缓存缩略图画上去
    tp = roll.thumbs.get("main")
    if tp and os.path.isfile(tp):
        try:
            tim = Image.open(tp).convert("RGB")
            tim.thumbnail((cw-2, ih))
            img.paste(tim, (gx+1+(cw-2-tim.width)//2, gy+1+(ih-tim.height)//2))
        except Exception:
            pass
    else:
        # 占位：胶片齿孔意象
        for yy in (int(ih*0.32), int(ih*0.68)):
            xx = gx+12
            while xx < gx+cw-18:
                d.rectangle([xx, gy+yy-3, xx+7, gy+yy+3], fill=rgb(app.colors["border"]))
                xx += 13
        d.text((gx+cw/2-16, gy+ih/2-7), "未出图", font=F(10), fill=rgb(app.colors["fg_muted"]))
    # 文字
    ty = gy+ih+7
    d.text((gx+9, ty), roll.title, font=F(11,True), fill=rgb(app.colors["fg"]))
    ty += 17
    if roll.film:
        d.text((gx+9, ty), roll.film[:22], font=F(10), fill=rgb(app.colors["fg_muted"]))
    ty += 15
    if roll.camera:
        d.text((gx+9, ty), roll.camera[:22], font=F(10), fill=rgb(app.colors["fg_muted"]))
    ty += 15
    bits = []
    if roll.shoot_date: bits.append(roll.shoot_date)
    if roll.frame_count: bits.append(f"{roll.frame_count} 张")
    elif not roll.outputs: bits.append("未出图")
    d.text((gx+9, ty), " · ".join(bits), font=F(10), fill=rgb(app.colors["fg_muted"]))

# --- 右侧详情面板 ---
dx = W-300
d.rounded_rectangle([dx, top+bar_h-30, W-16, H-70], radius=12, fill=rgb(app.colors["surface"]))
sel = ap.service.get(sel_id) if sel_id else None
ty = top + bar_h - 6
d.text((dx+16, ty), "卷宗详情", font=F(12,True), fill=rgb(app.colors["fg"]))
ty += 26
if sel:
    tp = sel.thumbs.get("main")
    if tp and os.path.isfile(tp):
        try:
            tim = Image.open(tp).convert("RGB"); tim.thumbnail((266,180))
            img.paste(tim, (dx+16, ty)); ty += tim.height+12
        except Exception: pass
    d.text((dx+16, ty), sel.title, font=F(13,True), fill=rgb(app.colors["fg"])); ty += 21
    meta = " · ".join([b for b in (sel.subtitle, sel.shoot_date) if b])
    if sel.frame_count: meta += f" · {sel.frame_count} 张"
    d.text((dx+16, ty), meta, font=F(10), fill=rgb(app.colors["fg_muted"])); ty += 20
    for k, lb in [("roll_no","卷号"),("film","胶卷"),("camera","相机"),
                  ("shoot_date","拍摄日期"),("dev_date","冲洗日期"),
                  ("proc","冲洗方式"),("lab","冲洗地点"),("scanner","扫描仪")]:
        d.text((dx+16, ty), lb, font=F(10), fill=rgb(app.colors["fg_muted"]))
        d.text((dx+82, ty), getattr(sel,k) or "—", font=F(10), fill=rgb(app.colors["fg"]))
        ty += 18
    ty += 8
    for t, accent in [("打开产物",False),("在文件夹中显示",False),
                      ("用这卷参数重新出图",True),("添加产物文件…",False),
                      ("编辑信息…",False),("删除卷宗",None)]:
        fill = rgb(app.colors["accent"]) if accent is True else (
              rgb(app.colors["error"]) if accent is None else rgb(app.colors["surface"]))
        fg = (255,255,255) if accent is not None else rgb(app.colors["fg"])
        d.rounded_rectangle([dx+16, ty, W-32, ty+28], radius=6, fill=fill,
                            outline=None if accent is not None else rgb(app.colors["border"]))
        bb = d.textbbox((0,0), t, font=F(11))
        d.text((dx+16+(W-48-(bb[2]-bb[0]))/2, ty+8), t, font=F(11), fill=fg)
        ty += 32

# --- 底部操作栏 ---
by = H-52
d.line([16, by-8, W-16, by-8], fill=rgb(app.colors["border"]))
d.text((16, by+4), app.status_lbl.cget("text") or "FilmSheet Ready",
       font=F(10), fill=rgb(app.colors["fg_muted"]))
for t, right, accent in [("预览", True, False), ("取消", True, False), ("开始生成", True, True)]:
    bb = d.textbbox((0,0), t, font=F(11))
    tw = (bb[2]-bb[0])+34
    x = W-16-tw if right else 0
    if not right: x = 16
    if accent:
        d.rounded_rectangle([x, by, x+tw, by+30], radius=7, fill=rgb(app.colors["accent"]))
        col=(255,255,255)
    else:
        d.rounded_rectangle([x, by, x+tw, by+30], radius=7, fill=rgb(app.colors["surface"]),
                            outline=rgb(app.colors["border"]))
        col = rgb(app.colors["fg"]) if t!="取消" else rgb(app.colors["disabled"])
    bb = d.textbbox((0,0), t, font=F(11))
    d.text((x+tw/2-(bb[2]-bb[0])/2, by+8), t, font=F(11), fill=col)

out = os.environ.get("FS_OUT", os.path.join(ROOT, ".preview_archive.png"))
img.save(out)
print("已渲染:", out, img.size)
root.destroy()
