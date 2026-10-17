# -*- coding: utf-8 -*-
"""Roll 领域实体 —— 一卷胶片的档案记录。

设计要点
--------
1. **与config 解耦**：渲染参数（缩略图宽度、间距、列数…）只是产生这份档案的
   副产品，被快照进``render_config``，但档案本身不依赖它。改了渲染参数不会
   让已有档案变形。
2. **一次生成可能产出多张图**（批量导出灯板+ 接触印相两种风格），所以产物是
   列表而不是单个路径。
3. **向前兼容**：``from_dict`` 对未知字段保留、缺失字段补默认，schema 升级时
   旧档案不会因为多一个字段就整个读不出来。
4. **纯数据**：不碰 Tk、不碰 Pillow、不碰文件系统（除 ``exists`` 之类的显式
   调用外），便于单测。
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any

# 实体自身的结构版本。与 store 的文件级 SCHEMA_VERSION 分开：
# 这里是「一条记录长什么样」，那里是「整个文件长什么样」。
ENTITY_VERSION = 1

# 卷宗状态：draft = 手动补录、尚未出图；archived = 已有产物
STATUS_DRAFT = "draft"
STATUS_ARCHIVED = "archived"

# 产物风格键，与 utils.helpers.STYLE_COLORS 的键一致
STYLE_LIGHTBOX = "lightbox"
STYLE_CONTACT = "contact_sheet"

# 归档卡片上展示的信息字段，顺序即详情面板的展示顺序
INFO_KEYS = (
    "roll_no", "film", "camera",
    "shoot_date", "dev_date", "proc", "lab", "scanner",
)

# Roll 属性名 → config 里的键。多数同名，只有卷号是 roll_no ↔ info_roll。
_INFO_SOURCE = {k: f"info_{k}" for k in INFO_KEYS}
_INFO_SOURCE["roll_no"] = "info_roll"

_INFO_MAXLEN = 200


def _now() -> float:
    return time.time()


def _s(v: Any, limit: int = _INFO_MAXLEN) -> str:
    """收敛成去空白、单行、有上限的字符串。

    档案字段会直接渲染到界面上，不收敛就会因为一个换行把网格撑歪。
    """
    if v is None:
        return ""
    s = str(v).replace("\r", " ").replace("\n", " ").strip()
    return s[:limit]


def _ts(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f > 0 else default


@dataclass
class Output:
    """一个产物文件。"""

    path: str = ""          # 绝对路径
    style: str = ""         # lightbox / contact_sheet / single_* 等
    width: int = 0
    height: int = 0
    created_at: float = field(default_factory=_now)

    def exists(self) -> bool:
        import os
        return bool(self.path) and os.path.isfile(self.path)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Output":
        d = d or {}
        return Output(
            path=_s(d.get("path"), 4096),
            style=_s(d.get("style"), 64),
            width=int(_ts(d.get("width"))),
            height=int(_ts(d.get("height"))),
            created_at=_ts(d.get("created_at"), _now()),
        )


def _safe_outputs(raw) -> list["Output"]:
    """把任意形状的 outputs 收敛成Output 列表。

    单条坏记录不该让整库读不出来 —— 手改 JSON、早期 bug、字段类型漂移都可能
    造出字符串或字典混在里面。逐条 try，坏的丢掉，好的留下。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        try:
            out.append(item if isinstance(item, Output) else Output.from_dict(item))
        except Exception:
            continue
    return out


@dataclass
class Roll:
    """一卷胶片的档案。"""

    # --- 标识 ---
    id: str = ""

    # --- 拍摄信息（对齐 config 的 info_* 字段）---
    roll_no: str = ""
    film: str = ""
    camera: str = ""
    shoot_date: str = ""
    dev_date: str = ""
    proc: str = ""
    lab: str = ""
    scanner: str = ""
    note: str = ""

    # --- 画幅 ---
    film_format: str = "135"
    sub_format: str = ""

    # --- 产物与来源 ---
    frame_count: int = 0
    outputs: list[Output] = field(default_factory=list)
    source_folder: str = ""
    render_config: dict = field(default_factory=dict)

    # --- 状态 ---
    status: str = STATUS_DRAFT
    auto_created: bool = False     # True=渲染时自动建卷，False=手动补录
    thumbs: dict = field(default_factory=dict)   # {style_or_"main": 缩略图路径}

    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)

    # ------------------------------------------------------------------ #
    #  构造
    # ------------------------------------------------------------------ #

    def __post_init__(self):
        self._normalize()
        if not self.id:
            # 必须传本实例的标识字段。早先这里无参调用 compute_id()，
            # 于是所有直接构造的 Roll 拿到的都是「空字段」的同一个 id ——
            # from_config 显式传参掩盖了这个 bug，但手动补录路径会中招。
            self.id = self.compute_id(self.roll_no, self.shoot_date, self.film)

    @staticmethod
    def compute_id(roll_no: str = "", shoot_date: str = "", film: str = "") -> str:
        """由稳定字段派生 id。

        刻意**不**用 uuid：同一卷反复生成时要落回同一条记录，用 uuid 每次都是
        新条目，银盐册立刻被重复项淹没。
        """
        key = "|".join([_s(roll_no).lower(), _s(shoot_date), _s(film).lower()])
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return f"r_{digest}"

    @classmethod
    def from_config(cls, config: dict, outputs: list[Output] | None = None,
                    auto: bool = True) -> "Roll":
        """从渲染 config 构造卷宗。

        未知字段不会进档案，但整个 config 会快照进 ``render_config``，
        这样「当时用的什么参数」可追溯。
        """
        cfg = config or {}

        # Roll 的属性名与 config 里的 info_* 键并非一一同名（roll_no→info_roll），
        # 这张表是唯一的映射来源，别再靠字符串拼接去猜。
        def info(key: str) -> str:
            return _s(cfg.get(_INFO_SOURCE.get(key, f"info_{key}"), ""))

        outputs = list(outputs or [])
        roll_no = info("roll_no")
        shoot_date = info("shoot_date")
        film = info("film")

        roll = cls(
            id=cls.compute_id(roll_no, shoot_date, film),
            roll_no=roll_no,
            film=film,
            camera=info("camera"),
            shoot_date=shoot_date,
            dev_date=info("dev_date"),
            proc=info("proc"),
            lab=info("lab"),
            scanner=info("scanner"),
            film_format=_s(cfg.get("film_format"), 8) or "135",
            sub_format=_s(cfg.get("sub_format"), 32),
            outputs=outputs,
            source_folder=_s(cfg.get("input_folder"), 4096),
            frame_count=int(_ts(cfg.get("frame_count"))),
            status=STATUS_ARCHIVED if outputs else STATUS_DRAFT,
            auto_created=auto,
            render_config=dict(cfg),
        )
        return roll

    # ------------------------------------------------------------------ #
    #  身份与合并
    # ------------------------------------------------------------------ #

    def matches(self, roll_no: str = "", shoot_date: str = "", film: str = "") -> bool:
        """判断另一组标识是否指向同一卷（大小写不敏感、空白归一）。"""
        return self.compute_id(roll_no, shoot_date, film) == self.id

    def merge_from(self, other: "Roll") -> "Roll":
        """把另一次生成的结果并入本卷。

        合并规则：
        - 产物按路径去重后累加（同一张图重复生成不产生两条）
        - 信息字段以「有值为准」补空，不覆盖已有内容
        - frame_count 取较大值
        - render_config 刷新为最新一次
        """
        seen = {o.path for o in self.outputs if o.path}
        for o in other.outputs:
            if o.path and o.path not in seen:
                seen.add(o.path)
                self.outputs.append(o)

        for key in INFO_KEYS:
            if not getattr(self, key) and getattr(other, key):
                setattr(self, key, getattr(other, key))

        self.frame_count = max(self.frame_count, other.frame_count)
        self.film_format = self.film_format or other.film_format
        self.sub_format = self.sub_format or other.sub_format
        self.source_folder = self.source_folder or other.source_folder
        if other.render_config:
            self.render_config = dict(other.render_config)
        if other.outputs:
            self.status = STATUS_ARCHIVED
        self.updated_at = _now()
        return self

    # ------------------------------------------------------------------ #
    #  展示派生
    # ------------------------------------------------------------------ #

    @property
    def title(self) -> str:
        """卡片主标题：卷号→ 胶卷 → 未命名。"""
        return self.roll_no or self.film or "未命名卷宗"

    @property
    def subtitle(self) -> str:
        """卡片副标题：胶卷 + 相机，有空就退到日期，再空就留空。"""
        bits = [b for b in (self.film, self.camera) if b]
        if bits:
            return " · ".join(bits)
        return self.shoot_date or ""

    @property
    def year(self) -> str:
        """从拍摄日期里取年份；取不到就取建档年份。"""
        m = re.search(r"(\d{4})", self.shoot_date or "")
        if m:
            return m.group(1)
        return time.strftime("%Y", time.localtime(self.created_at))

    @property
    def primary_output(self) -> Output | None:
        """代表产物：优先灯板，其次第一条 —— 但**只在文件还在的里面挑**。

        代表产物的文件被移走后，只要这卷还有别的产物在，缩略图就必须出得来。
        早先不看存在性，于是「文件不在了 + 后来补了新产物」的卷会永远显示不出
        缩略图 —— 而且 `has_output()` 为真，卡片连「文件不在了」都不提示，
        用户只能看到一张怎么点都没图的空卡片。

        全都失联时**仍然返回第一条**（而不是 None），让调用方能照旧用
        `out.exists()` 区分「没产物」和「产物丢了」。
        """
        pool = self.existing_outputs() or self.outputs
        for o in pool:
            if o.style == STYLE_LIGHTBOX:
                return o
        return pool[0] if pool else None

    def output_by_style(self, style: str) -> Output | None:
        for o in self.outputs:
            if o.style == style:
                return o
        return None

    def existing_outputs(self) -> list[Output]:
        return [o for o in self.outputs if o.exists()]

    def has_output(self) -> bool:
        return bool(self.existing_outputs())

    def to_config(self) -> dict:
        """还原成渲染 config，用于「用这卷的参数重新出图」。

        只回填档案里存着的字段，缺失的交给 ``sanitize_config`` 补默认值。
        """
        cfg = dict(self.render_config or {})
        cfg.update({
            "info_roll": self.roll_no,
            "info_film": self.film,
            "info_camera": self.camera,
            "info_shoot_date": self.shoot_date,
            "info_dev_date": self.dev_date,
            "info_proc": self.proc,
            "info_lab": self.lab,
            "info_scanner": self.scanner,
            "film_format": self.film_format or "135",
            "input_folder": self.source_folder,
        })
        if self.sub_format:
            cfg["sub_format"] = self.sub_format
        return cfg

    # ------------------------------------------------------------------ #
    #  序列化
    # ------------------------------------------------------------------ #

    def _normalize(self):
        """把外部灌进来的脏数据捋干净。必须在赋id 之前调用。"""
        for key in INFO_KEYS:
            setattr(self, key, _s(getattr(self, key)))
        self.note = _s(self.note, 2000)
        self.film_format = _s(self.film_format, 8) or "135"
        self.sub_format = _s(self.sub_format, 32)
        self.source_folder = _s(self.source_folder, 4096)
        self.status = self.status if self.status in (STATUS_DRAFT, STATUS_ARCHIVED) else STATUS_DRAFT
        self.outputs = _safe_outputs(self.outputs)
        self.render_config = self.render_config if isinstance(self.render_config, dict) else {}
        raw_thumbs = self.thumbs if isinstance(self.thumbs, dict) else {}
        self.thumbs = {str(k): _s(v, 4096) for k, v in raw_thumbs.items() if v}
        self.frame_count = max(0, int(_ts(self.frame_count)))
        self.auto_created = bool(self.auto_created)
        self.created_at = _ts(self.created_at, _now())
        self.updated_at = _ts(self.updated_at, self.created_at)

    def to_dict(self) -> dict:
        return {
            "_v": ENTITY_VERSION,
            "id": self.id,
            "roll_no": self.roll_no,
            "film": self.film,
            "camera": self.camera,
            "shoot_date": self.shoot_date,
            "dev_date": self.dev_date,
            "proc": self.proc,
            "lab": self.lab,
            "scanner": self.scanner,
            "note": self.note,
            "film_format": self.film_format,
            "sub_format": self.sub_format,
            "frame_count": self.frame_count,
            "outputs": [o.to_dict() for o in self.outputs],
            "source_folder": self.source_folder,
            "render_config": self.render_config,
            "status": self.status,
            "auto_created": self.auto_created,
            "thumbs": self.thumbs,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Roll":
        d = d or {}
        if not isinstance(d, dict):
            d = {}
        # 已知字段集合直接从 to_dict 的键派生 —— 手写这份清单迟早会漏字段
        # （曾漏掉 outputs，导致产物列表被当成未知字段塞进 render_config）。
        known = set(Roll.__dataclass_fields__.keys()) | {"_v", "id"}
        # 注意：不把 d["id"] 传进构造函数。若传了且非空，__post_init 就不会重算，
        # 而此时字段尚未归一化，算出的 id 会基于脏值 —— 与后续按干净字段算出的
        # id 不一致，同一卷会在库里裂成两条。统一交给 __post_init 处理。
        legacy_id = _s(d.get("id"), 64)
        roll = cls(
            id="",
            roll_no=d.get("roll_no", ""),
            film=d.get("film", ""),
            camera=d.get("camera", ""),
            shoot_date=d.get("shoot_date", ""),
            dev_date=d.get("dev_date", ""),
            proc=d.get("proc", ""),
            lab=d.get("lab", ""),
            scanner=d.get("scanner", ""),
            note=d.get("note", ""),
            film_format=d.get("film_format", "135"),
            sub_format=d.get("sub_format", ""),
            frame_count=int(_ts(d.get("frame_count"))),
            outputs=_safe_outputs(d.get("outputs")),
            source_folder=d.get("source_folder", ""),
            render_config=d.get("render_config") if isinstance(d.get("render_config"), dict) else {},
            status=d.get("status", STATUS_DRAFT),
            auto_created=bool(d.get("auto_created", False)),
            thumbs=d.get("thumbs") if isinstance(d.get("thumbs"), dict) else {},
            created_at=_ts(d.get("created_at"), _now()),
            updated_at=_ts(d.get("updated_at"), 0) or _ts(d.get("created_at"), _now()),
        )
        # 旧档案可能没带 id，按标识补一个，避免所有条目挤成同一个。
        # 只在**缺 id** 时补：文件里已有 id 就以它为准（它已是对外身份）。
        # 若它与重算值不同，说明早期版本用了不同标识口径 —— 那时保留原值，
        # 因为把同一卷的 id 改掉等于凭空多出一卷。
        if not legacy_id:
            roll.id = cls.compute_id(roll.roll_no, roll.shoot_date, roll.film)
        else:
            roll.id = legacy_id
        # 未知字段原样留着，日后想升级成正式字段就不用再改迁移代码
        extras = {k: v for k, v in d.items()
                  if k not in known and not k.startswith("_")}
        if extras:
            bag = roll.render_config.setdefault("_legacy_extras", {})
            if isinstance(bag, dict):
                bag.update(extras)
            else:
                # 手改文件把这一格写成了字符串之类。不能为此让整条记录读不出来
                # —— 这是纯附带的存档格，丢了也不影响任何展示字段。
                roll.render_config["_legacy_extras"] = dict(extras)
        return roll