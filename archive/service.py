# -*- coding: utf-8 -*-
"""ArchiveService —— 归档库的门面。

上层（UI / API）只跟这个类打交道，不直接碰 store 或 models。它保证几条
不变量：

- **自动建卷幂等**：同一卷（卷号+日期+胶卷）反复生成只更新同一条记录，
  产物累加而不是刷出一堆重复卡片
- **产物丢失不静默**：图被挪走时保留档案，但 ``has_output()`` 为 False，
  由界面明确提示「文件已丢失」
- **写盘失败不吞**：``last_error`` 暴露给界面提示，不静默丢数据
"""

from __future__ import annotations

import os

from .models import Roll, Output, STATUS_ARCHIVED, STATUS_DRAFT
from .store import ArchiveStore
from . import thumbs as thumbs_mod


class ArchiveService:
    def __init__(self, store: ArchiveStore | None = None):
        self.store = store or ArchiveStore()
        self.last_error: str = ""
        self.enabled: bool = True      # 用户可在设置里关掉自动归档

    # ------------------------------------------------------------------ #
    #  读取
    # ------------------------------------------------------------------ #

    def list_rolls(self, **kw) -> list[Roll]:
        return self.store.query(**kw)

    def get(self, roll_id: str) -> Roll | None:
        return self.store.get(roll_id)

    def facets(self) -> dict:
        return self.store.facets()

    def reload(self):
        return self.store.load(force=True)

    @property
    def load_warning(self) -> str:
        return self.store.load_warning

    # ------------------------------------------------------------------ #
    #  自动建卷：渲染完成后调用
    # ------------------------------------------------------------------ #

    def record_render(self, config: dict, outputs: list[dict] | list[Output],
                      auto_archive: bool | None = None) -> Roll | None:
        """把一次渲染结果写进归档库。

        Args:
            config: 本次渲染的完整 config（含 info_* 与 frame_count）
            outputs: 产物列表，元素可为 dict（``{"path","style","width","height"}``）
                     或 Output 实例
            auto_archive: 覆盖 ``self.enabled``；显式传值优先

        Returns:
            最终的卷宗；未开启归档时返回 None。
        """
        want = self.enabled if auto_archive is None else auto_archive
        if not want:
            return None

        outs = [o if isinstance(o, Output) else Output.from_dict(o) for o in (outputs or [])]
        roll = Roll.from_config(config, outs, auto=True)
        if not roll.outputs and not (roll.roll_no or roll.film):
            #既没产物也没标识信息 —— 建出来的是一张空卡片，不入库
            return None
        saved = self.store.upsert(roll)
        self.last_error = self.store.load_warning
        return saved

    # ------------------------------------------------------------------ #
    #  手动补录
    # ------------------------------------------------------------------ #

    def create_manual(self, fields: dict, outputs: list[dict] | None = None) -> Roll | None:
        """手动新建卷宗。允许没有产物 —— 登记「已拍未冲」也是归档的一种。"""
        roll = self._roll_from_fields(fields, outputs)
        if roll is None:
            self.last_error = "卷宗信息为空，未创建。"
            return None
        saved = self.store.upsert(roll)
        self.last_error = self.store.load_warning
        return saved

    def update(self, roll_id: str, **fields) -> Roll | None:
        roll = self.store.update_fields(roll_id, **fields)
        # 改了胶卷名就可能改了身份（id依赖 roll_no/date/film），重新规整一次
        if roll is not None:
            new_id = Roll.compute_id(roll.roll_no, roll.shoot_date, roll.film)
            if new_id != roll.id:
                roll.id = new_id
                self.store.save()
        self.last_error = self.store.load_warning
        return roll

    def add_output(self, roll_id: str, path: str, style: str = "") -> Roll | None:
        """把一个已存在的图挂到卷宗上（手动补录产物）。"""
        if not path or not os.path.isfile(path):
            self.last_error = f"文件不存在：{path}"
            return None
        roll = self.store.get(roll_id)
        if roll is None:
            return None
        if any(o.path == path for o in roll.outputs):
            return roll
        roll.outputs.append(Output(path=os.path.abspath(path), style=style))
        roll.status = STATUS_ARCHIVED
        import time
        roll.updated_at = time.time()
        self.store.save()
        self.last_error = self.store.load_warning
        return roll

    def delete(self, roll_id: str, remove_files: bool = False) -> bool:
        """删除卷宗。``remove_files=True`` 时连产物一起删，默认为否。

        默认不删文件：归档是索引不是所有权，误删一卷的图是不可逆的。
        """
        roll = self.store.get(roll_id)
        if roll is None:
            return False
        if remove_files:
            for o in roll.outputs:
                try:
                    if o.exists():
                        os.unlink(o.path)
                except OSError:
                    pass
        thumbs_mod.clear_thumb(roll_id)
        ok = self.store.delete(roll_id)
        self.last_error = self.store.load_warning
        return ok

    # ------------------------------------------------------------------ #
    #  补建历史（用户明确选了不做自动迁移，这个方法留给手动触发）
    # ------------------------------------------------------------------ #

    def scan_folder(self, folder: str, parse_from_name: bool = True) -> list[Roll]:
        """扫描一个目录，把已有产物图补建成草稿卷宗。

        仅在用户手动触发时运行（界面上是「导入已有产物」），不自动执行。
        每张图建一卷还是按文件名聚成一卷，取决于 ``parse_from_name``：
        形如 ``A01_M6_Portra400_20260304.jpg`` 的命名会被拆成一条卷宗，
        同名前缀的多张图会合并（这正是 FilmSheet 自己的输出命名规则）。
        """
        if not folder or not os.path.isdir(folder):
            self.last_error = f"目录不存在：{folder}"
            return []

        from utils.helpers import SUPPORTED_FORMATS

        groups: dict[str, dict] = {}
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(SUPPORTED_FORMATS):
                continue
            path = os.path.join(folder, name)
            stem = os.path.splitext(name)[0]
            key = _group_key(stem) if parse_from_name else stem
            g = groups.setdefault(key, {"paths": [], "fields": parse_stem(stem) if parse_from_name else {}})
            g["paths"].append(Output(path=os.path.abspath(path),
                                     style=_guess_style(name)))

        made: list[Roll] = []
        for key, g in groups.items():
            fields = g["fields"] or {}
            roll = self._roll_from_fields(fields, g["paths"])
            if roll is None:
                continue
            roll.auto_created = False
            roll.source_folder = os.path.abspath(folder)
            saved = self.store.upsert(roll)
            made.append(saved)

        self.last_error = self.store.load_warning
        return made

    # ------------------------------------------------------------------ #
    #  缩略图
    # ------------------------------------------------------------------ #

    def ensure_thumb(self, roll: Roll):
        """确保卷宗有缩略图，命中缓存时不加载 Pillow。"""
        if not roll:
            return None
        cached = roll.thumbs.get("main")
        if cached and os.path.isfile(cached):
            return cached
        path = thumbs_mod.thumb_for_roll(roll)
        if path and roll.thumbs.get("main") != path:
            #缓存路径变了要落盘，否则下次又得重算
            roll.thumbs["main"] = path
            try:
                roll.updated_at = _now()
                self.store.save()
            except Exception:
                pass
        return path

    # ------------------------------------------------------------------ #
    #  内部
    # ------------------------------------------------------------------ #

    @staticmethod
    def _roll_from_fields(fields: dict, outputs=None) -> Roll | None:
        fields = {k: v for k, v in (fields or {}).items() if v not in (None, "")}
        if not fields and not outputs:
            return None
        outs = [o if isinstance(o, Output) else Output.from_dict(o) for o in (outputs or [])]
        roll = Roll(
            roll_no=str(fields.get("roll_no", "") or ""),
            film=str(fields.get("film", "") or ""),
            camera=str(fields.get("camera", "") or ""),
            shoot_date=str(fields.get("shoot_date", "") or ""),
            dev_date=str(fields.get("dev_date", "") or ""),
            proc=str(fields.get("proc", "") or ""),
            lab=str(fields.get("lab", "") or ""),
            scanner=str(fields.get("scanner", "") or ""),
            note=str(fields.get("note", "") or ""),
            film_format=str(fields.get("film_format", "135") or "135"),
            sub_format=str(fields.get("sub_format", "") or ""),
            frame_count=int(fields.get("frame_count") or 0) or len(outs),
            source_folder=str(fields.get("source_folder", "") or ""),
            outputs=outs,
            status=STATUS_ARCHIVED if outs else STATUS_DRAFT,
            auto_created=False,
        )
        return roll


def _now() -> float:
    import time
    return time.time()


def _guess_style(name: str) -> str:
    n = name.lower()
    if "contact" in n:
        return "contact_sheet"
    if "lightbox" in n or "_light" in n:
        return "lightbox"
    return ""


def _group_key(stem: str) -> str:
    """同一卷的不同风格产物归到一组。

    FilmSheet 批量导出会产出 ``xxx_light.jpg`` 和 ``xxx_contact.jpg``，
    要归到同一条卷宗；单张导出则是 ``..._single_light_3.jpg`` 这种带序号的，
    按前两段归组即可。
    """
    parts = stem.split("_")
    if len(parts) >= 3:
        return "_".join(parts[:2])
    return parts[0] if parts else stem


def parse_stem(stem: str) -> dict:
    """从 FilmSheet 默认输出名里抽回拍摄信息。

    生成规则是 ``卷号_相机_胶卷_日期``（见 filename_utils.generate_output_filename），
    倒着猜回字段。猜不出的部分留空，不硬塞。

    风格后缀（``_light`` / ``_contact`` / ``_single_light_3``）先剥掉，
    否则胶卷名会变成 ``Portra400_contact``。
    """
    parts = [p for p in _strip_style_suffix(stem).split("_") if p]
    fields: dict = {}
    # 末尾像日期就归为拍摄日期
    if parts and _looks_like_date(parts[-1]):
        fields["shoot_date"] = parts[-1]
        parts = parts[:-1]
    if len(parts) >= 3:
        fields["roll_no"] = parts[0]
        fields["camera"] = parts[1]
        fields["film"] = "_".join(parts[2:])
    elif len(parts) == 2:
        fields["roll_no"] = parts[0]
        fields["film"] = parts[1]
    elif len(parts) == 1:
        fields["roll_no"] = parts[0]
    return fields


# 批量导出/单张导出在文件名尾部留下的标记
_STYLE_TOKENS = ("light", "lightbox", "contact", "contactsheet", "single")


def _strip_style_suffix(stem: str) -> str:
    """剥掉尾部的风格/序号标记。

    ``A01_M6_Portra400_20260304_single_light_3`` → ``A01_M6_Portra400_20260304``

    注意：紧邻胶卷名的单个 8 位数字是**日期**（FilmSheet 默认名就是
    ``卷号_相机_胶卷_20260304``），不能当序号剥掉。所以一旦剥到只剩孤立数字段、
    且它长得像日期，就停下让日期判定去处理。
    """
    parts = [p for p in stem.split("_") if p]
    for _ in range(4):
        if not parts:
            break
        tail = parts[-1].lower()
        if tail in _STYLE_TOKENS:
            parts.pop()
        elif tail.isdigit() and len(parts) > 1:
            prev = parts[-2].lower()
            # 风格后面的序号（_single_light_3）照剥；
            # 胶卷后面的日期（Portra400_20260304）不剥
            if _looks_like_date(tail) and prev not in _STYLE_TOKENS:
                break
            parts.pop()
        else:
            break
    return "_".join(parts)


def _looks_like_date(s: str) -> bool:
    """是否像日期。

    支持三种写法（FilmSheet 默认输出用的就是紧凑 8 位）：
        ``20260304``   紧凑
        ``2026-03-04`` / ``2026/3/4`` / ``2026.3.4``   带分隔符
    纯 4 位数（``2026``）**不算**完整日期 —— 那只是年份，当成日期会让归档卡片
    显示成一个没有月日的假日期。首位不足 4 位（如卷号 ``007``）也不算。
    """
    import re
    t = s.strip().replace("/", ".").replace("-", ".")
    if not t:
        return False
    if "." in t:
        return bool(re.fullmatch(r"\d{4}(\.\d{1,2}){1,2}", t))
    # 无分隔符：要么 YYYYMMDD（8 位），要么 YYYYMM（6 位）
    return bool(re.fullmatch(r"\d{6}(?:[01]\d|2[0-3])?(?:[0-3]\d)?", t))