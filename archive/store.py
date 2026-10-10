# -*- coding: utf-8 -*-
"""底片册持久化 —— 单个 JSON 文件。

为什么不是 SQLite：几十到几千卷的量级，JSON 的读取是毫秒级；换来的是
数据可读、可diff、手改方便、备份就是拷一个文件。对这个工具来说更值。

三个必须处理好的失败面：
1. **原子写**：先写临时文件再 ``os.replace``。中途崩了旧文件完好，不会半截 JSON。
2. **损坏不丢数据**：读不出来就把坏文件改名备份、当空库处理，并在下次写入时
   留一份警告。绝不能因为解析失败就 ``pass`` 掉然后下次覆盖写空。
3. **schema 迁移**：文件级``SCHEMA_VERSION`` 显式管理，旧版本走迁移函数链。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time

from .models import Roll

# 文件结构版本。加字段时 +1，并在 _MIGRATIONS 里补一级。
SCHEMA_VERSION = 1

ARCHIVE_FILENAME = "filmsheet_archive.json"

# 允许用环境变量把底片册指向别处（NAS / 同步盘 / 测试目录）。
# 优先级：环境变量 > filmsheet_config.json 里的archive_dir > 默认配置目录。
# 环境变量最优先是因为它连配置文件都读不到时也能生效（配置损坏时的逃生通道）。
ENV_ARCHIVE_DIR = "FILMSHEET_ARCHIVE_DIR"


def default_config_dir() -> str:
    """平台标准的配置目录。"""
    import platform
    system = platform.system()
    if system == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, "FilmSheet")


def _configured_dir() -> str:
    """从配置文件里读用户设定的底片册目录（读不到就返回空串）。"""
    try:
        # 延迟导入：store 是领域层，不该硬依赖 utils.helpers
        from utils.helpers import load_config
        v = (load_config() or {}).get("archive_dir", "")
        return v.strip() if isinstance(v, str) else ""
    except Exception:
        # 配置文件损坏/不存在时不能连带底片册一起打不开
        return ""


def archive_dir() -> str:
    """底片册所在目录。

    默认与配置文件同处（用户找得到）；用户可在配置里改到NAS / 同步盘，
    也可用 ``FILMSHEET_ARCHIVE_DIR`` 环境变量临时覆盖（优先级最高）。
    """
    env = os.environ.get(ENV_ARCHIVE_DIR, "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    cfg = _configured_dir()
    if cfg:
        return os.path.abspath(os.path.expanduser(cfg))
    return default_config_dir()


def archive_path() -> str:
    return os.path.join(archive_dir(), ARCHIVE_FILENAME)


def _date_key(s: str) -> tuple:
    """把各种写法的日期规整成可比较的元组。

    用户手填日期不会那么规整：``2026/3``、``202603``、``2026.3.1``、``2026年``都见过。
    抽取连续数字补齐成 (年, 月, 日)，抽不出就退化成 (0,0,0)，让排序稳定
    而不是按字符串字典序乱排。

    **单个数字里塞了年月日的紧写必须拆开**（``20260304`` → (2026,3,4)、
    ``202603`` → (2026,3,0)）。早先一律 ``int(n[:4])``，把它们统统压成
    ``(2026, 0, 0)`` —— 与裸 ``2026`` 撞成同一个排序键，年份之间分不出先后。
    而紧凑写法正是本工具自己的产物命名格式，不是边角情况。
    """
    import re
    nums = re.findall(r"\d+", s or "")
    if not nums:
        return (0, 0, 0)
    parts: list[int] = []
    for n in nums:
        if len(parts) >= 3:
            break
        if len(n) == 8:                      # 20260304 —— 年月日挤在一个数字里
            parts += [int(n[:4]), int(n[4:6]), int(n[6:8])]
        elif len(n) == 6:                    # 202603 —— 年月挤在一起
            parts += [int(n[:4]), int(n[4:6])]
        elif len(n) > 4:                     # 更长的怪东西：只敢信前四位是年
            parts.append(int(n[:4]))
        else:
            parts.append(int(n))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _migrate_0_to_1(data: dict) -> dict:
    if isinstance(data, list):
        # 极早期把卷宗直接存成数组
        data = {"rolls": data}
    if not isinstance(data, dict):
        raise ValueError("底片册文件根节点不是对象或数组")
    data.setdefault("rolls", [])
    if not isinstance(data["rolls"], list):
        data["rolls"] = []
    return data


_MIGRATIONS = [_migrate_0_to_1]


def migrate(data):
    """把任意历史版本的结构升级到当前版本。

    裸数组（极早期格式）必须先转成 dict 再取版本号，所以这里不能一上来就
    ``data.get("schema_version")`` —— 迁移函数正是负责处理这种形状的。
    """
    if isinstance(data, list):
        data = {"rolls": data}
    if not isinstance(data, dict):
        raise ValueError("底片册文件根节点既不是对象也不是数组")

    ver = int(data.get("schema_version") or 0)
    for fn in _MIGRATIONS:
        if fn.__name__.endswith(f"{ver}_to_{ver + 1}") or ver < 1:
            data = fn(data)
            ver = 1
            break
    data["schema_version"] = SCHEMA_VERSION
    return data


class ArchiveStore:
    """卷宗集合的读写与查询。

    内存里始终持有一份全量列表（``self._rolls``）。这个量级下没有分页的必要，
    但每次读盘会做脏检查：文件 mtime/大小没变就直接复用内存副本，
    避免界面重绘时反复解析 JSON。
    """

    def __init__(self, path: str | None = None):
        # 不传路径时每次都重新解析 —— archive_dir() 现在会读配置与环境变量，
        # 用户改了设置后同一个 ArchiveService 实例也必须跟着换位置。
        self._explicit_path = path
        self.path = path or archive_path()
        self._rolls: list[Roll] = []
        self._stamp: tuple | None = None      # (mtime, size)
        self.load_warning: str = ""# 读盘异常时的提示，供界面提示用户

    def _resolve_path(self) -> str:
        """当前应该使用的归档文件路径。"""
        return self._explicit_path or archive_path()

    # ------------------------------------------------------------------ #
    #  读
    # ------------------------------------------------------------------ #

    def load(self, force: bool = False) -> list[Roll]:
        # 路径可能随配置/环境变量变化，变了必须重新读盘：
        # 否则内存里的旧库会被mtime 脏检查当成有效，用户会以为卷宗丢了。
        current = self._resolve_path()
        if current != self.path:
            self.path = current
            self._rolls = []
            self._stamp = None

        if not force and self._stamp is not None and not self._stamp_changed():
            return self._rolls

        self.load_warning = ""
        if not os.path.isfile(self.path):
            self._rolls = []
            self._stamp = None
            return self._rolls

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            self._backup_corrupt(str(e))
            self._rolls = []
            self._stamp = None
            return self._rolls

        try:
            data = migrate(raw)
        except Exception as e:                      # 连根节点都不对：整库没法用
            self._backup_corrupt(f"结构异常: {e}")
            self._rolls = []
            self._stamp = self._current_stamp()
            self._dedupe()
            return self._rolls

        # 逐条容错：一条坏记录只丢它自己。
        #
        # 早先这里是列表推导式，任何一条 `Roll.from_dict` 抛异常都会落到
        # `except` → 整库改名备份、当空库启动。用户看到的是「底片册还是空的」，
        # 得自己去目录里翻 `.corrupt-*` 手工修 —— 手改 JSON 写错一个字段类型
        # 就够触发。产物层（`_safe_outputs`）和字段层（`Roll.from_dict`）早就
        # 逐条容错了，只有这一层是全有全无。
        rolls: list[Roll] = []
        bad = 0
        for d in data.get("rolls", []) or []:
            try:
                rolls.append(Roll.from_dict(d))
            except Exception:
                bad += 1
        self._rolls = rolls

        # 被跳过的记录在下次 save 时会从文件里消失，所以先把原文件留个副本。
        # 不搬走（不像 _backup_corrupt）—— 好数据还要接着用。
        if bad:
            kept = self._preserve_damaged()
            self.load_warning = (
                f"底片册有 {bad} 条记录读不出来，已跳过；"
                f"其余 {len(rolls)} 卷正常。"
                + (f"原文件已另存为 {kept}" if kept else "（原文件副本创建失败）")
            )

        self._stamp = self._current_stamp()

        self._dedupe()
        return self._rolls

    def _preserve_damaged(self) -> str:
        """把读不干净的原始文件**复制**一份留档，返回副本文件名（失败返回空串）。

        与 ``_backup_corrupt`` 的区别：那个是整库不可读，必须把文件搬走，
        否则下次启动又踩同一颗雷；这里只是坏了几条，好数据还要继续用，
        所以留副本而不是搬走 —— 但坏掉的那几条因此不会在下次保存时无声消失。
        """
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bak = f"{self.path}.damaged-{stamp}"
            shutil.copy2(self.path, bak)
            return os.path.basename(bak)
        except OSError:
            return ""

    def _current_stamp(self) -> tuple | None:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def _stamp_changed(self) -> bool:
        cur = self._current_stamp()
        if cur is None:
            return True
        return cur != self._stamp

    def _backup_corrupt(self, reason: str):
        """把坏文件挪走，绝不原地覆盖。"""
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bak = f"{self.path}.corrupt-{stamp}"
            shutil.move(self.path, bak)
            self.load_warning = (
                f"归档文件无法读取（{reason}），已备份为 {os.path.basename(bak)}，"
                f"本次按空库处理。"
            )
        except OSError:
            self.load_warning = f"归档文件无法读取（{reason}），且备份失败：{self.path}"

    def _dedupe(self):
        """同id 只留一条，后者并入前者。防止手工编辑文件后出现重复条目。"""
        by_id: dict[str, Roll] = {}
        out: list[Roll] = []
        for r in self._rolls:
            if r.id in by_id:
                by_id[r.id].merge_from(r)
            else:
                by_id[r.id] = r
                out.append(r)
        self._rolls = out

    # ------------------------------------------------------------------ #
    #  写
    # ------------------------------------------------------------------ #

    def save(self) -> bool:
        """原子写。失败返回 False 并保留内存数据，不做破坏性操作。"""
        self._dedupe()
        path = self._resolve_path()
        self.path = path
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "rolls": [r.to_dict() for r in self._rolls],
        }
        d = os.path.dirname(path)
        try:
            if d:
                os.makedirs(d, exist_ok=True)
            # 同目录建临时文件，保证 os.replace 是同分区的原子替换
            fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".archive-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())      # 落盘再做replace，断电也不丢
                os.replace(tmp, path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except Exception as e:
            self.load_warning = f"归档写入失败：{e}"
            return False
        self._stamp = self._current_stamp()
        return True

    # ------------------------------------------------------------------ #
    #  增删改查
    # ------------------------------------------------------------------ #

    def all(self) -> list[Roll]:
        return list(self.load())

    def get(self, roll_id: str) -> Roll | None:
        for r in self.load():
            if r.id == roll_id:
                return r
        return None

    def find_by_identity(self, roll_no: str, shoot_date: str = "", film: str = "") -> Roll | None:
        """按稳定标识找已有卷宗 —— 自动建卷的幂等性靠它。"""
        target = Roll.compute_id(roll_no, shoot_date, film)
        for r in self.load():
            if r.id == target:
                return r
        return None

    def upsert(self, roll: Roll) -> Roll:
        """写入或按 id 合并。返回最终形态的卷宗。

        同 id 已存在时**合并**而不是替换：同一卷重出一版灯板不该把上一版
        产物弄丢，信息字段也不该被空值覆盖。
        """
        rolls = self.load()
        for i, r in enumerate(rolls):
            if r.id == roll.id:
                merged = r.merge_from(roll)
                rolls[i] = merged
                self.save()
                return merged
        rolls.append(roll)
        self.save()
        return roll

    def update_fields(self, roll_id: str, **fields) -> Roll | None:
        """局部更新若干字段（编辑面板用），空值不覆盖已有内容。"""
        r = self.get(roll_id)
        if r is None:
            return None
        for k, v in fields.items():
            if v is None:
                continue
            if hasattr(r, k):
                setattr(r, k, v)
        r._normalize()
        r.updated_at = time.time()
        self.save()
        return r

    def delete(self, roll_id: str) -> bool:
        rolls = self.load()
        keep = [r for r in rolls if r.id != roll_id]
        if len(keep) == len(rolls):
            return False
        self._rolls = keep
        return self.save()

    # ------------------------------------------------------------------ #
    #  查询
    # ------------------------------------------------------------------ #

    def query(self, keyword: str = "", film: str = "", camera: str = "",
              year: str = "", sort: str = "date_desc") -> list[Roll]:
        """筛选 + 排序。

        关键词在卷号/胶卷/相机/备注/冲洗信息里做不区分大小写的子串匹配——
        底片册的使用方式是「记不清全名，只记得是台 M6」，模糊比精确好用。
        """
        rolls = self.load()
        kw = keyword.strip().lower()

        def hay(r: Roll) -> str:
            return " ".join([r.roll_no, r.film, r.camera, r.proc, r.lab,
                             r.scanner, r.dev_date, r.note]).lower()

        if kw:
            rolls = [r for r in rolls if kw in hay(r)]
        if film:
            rolls = [r for r in rolls if r.film == film]
        if camera:
            rolls = [r for r in rolls if r.camera == camera]
        if year:
            rolls = [r for r in rolls if r.year == year]

        return self._sorted(rolls, sort)

    @staticmethod
    def _sorted(rolls: list[Roll], sort: str) -> list[Roll]:
        if sort == "date_asc":
            # 无日期的排最后，而不是混在最前面
            dated = sorted((r for r in rolls if r.shoot_date), key=lambda r: _date_key(r.shoot_date))
            undated = sorted((r for r in rolls if not r.shoot_date), key=lambda r: -r.updated_at)
            return dated + undated
        if sort == "created_desc":
            return sorted(rolls, key=lambda r: -r.created_at)
        if sort == "created_asc":
            return sorted(rolls, key=lambda r: r.created_at)
        if sort == "frames_desc":
            return sorted(rolls, key=lambda r: -r.frame_count)
        if sort == "title":
            return sorted(rolls, key=lambda r: (r.roll_no or r.film or "￿").lower())
        # 默认：有拍摄日期的按日期倒序在前；无日期的按更新时间倒序沉底。
        # 不用整体 reverse —— 那会把无日期的又甩回最前面。
        dated = sorted((r for r in rolls if r.shoot_date),
                       key=lambda r: (_date_key(r.shoot_date), -r.updated_at),
                       reverse=True)
        undated = sorted((r for r in rolls if not r.shoot_date),
                         key=lambda r: -r.updated_at)
        return dated + undated

    def facets(self) -> dict:
        """可用筛选项：胶卷品牌、相机、年份、去重值列表。"""
        rolls = self.load()
        def uniq(fn):
            return sorted({v for v in (fn(r) for r in rolls) if v})
        return {
            "films": uniq(lambda r: r.film),
            "cameras": uniq(lambda r: r.camera),
            "years": sorted({r.year for r in rolls}, reverse=True),
            "total": len(rolls),
            # 只要登记过产物就算有图；产物是否还在盘上是另一回事
            # （用户可能把图挪走了，这时卡片显示「文件已丢失」而不是从库里消失）
            "with_output": sum(1 for r in rolls if r.outputs),
            "orphaned": sum(1 for r in rolls if r.outputs and not r.has_output()),
        }