#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""博饼。Python 3.10+，仅使用标准库。

运行：双击本 .pyw 文件（需要 Python 3.10+）。
数据默认保存在程序旁边的 bobing_data.json。--data 可指定另一份数据文件。
这是单人即时领奖玩法：状元立即发放；六同不扫奖池、不追夺已发奖品。
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
import random
import sys
import time
import uuid
import tkinter as tk
from tkinter import messagebox, ttk


CATEGORIES = ("状元", "对堂", "三红", "四进", "二举", "一秀", "插金花", "安慰奖")
INITIAL_STOCK = dict(zip(CATEGORIES, (1, 2, 4, 8, 16, 32, 1, 999)))
WINDOW_ICON = Path(r"C:\Users\jimmy\Pictures\bobing.ico")
RULE_SOURCE = "https://www.bobing.org/rules/"


def enable_high_dpi() -> None:
    """必须在创建 Tk 窗口前调用，避免 Windows 位图拉伸导致模糊。"""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # Windows 10 1703+：Per Monitor V2。Python 宿主已有 DPI 清单时，
        # 通过线程上下文设置创建本程序窗口所用的 DPI 感知模式。
        try:
            set_process = user32.SetProcessDpiAwarenessContext
            set_process.argtypes = [ctypes.c_void_p]
            set_process.restype = ctypes.c_bool
            if set_process(ctypes.c_void_p(-4)):
                return
            set_thread = user32.SetThreadDpiAwarenessContext
            set_thread.argtypes = [ctypes.c_void_p]
            set_thread.restype = ctypes.c_void_p
            if set_thread(ctypes.c_void_p(-4)):
                return
        except AttributeError:
            pass
        # 兼容没有上述 API 的旧 Windows。
        try:
            shcore = ctypes.WinDLL("shcore", use_last_error=True)
            shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
            shcore.SetProcessDpiAwareness.restype = ctypes.c_long
            if shcore.SetProcessDpiAwareness(2) == 0:
                return
        except (AttributeError, OSError):
            pass
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def display_scale(root: tk.Tk) -> float:
    """字体以 point 绘制，其余像素尺寸按同一个 DPI 比例放大。"""
    dpi = float(root.winfo_fpixels("1i"))
    if sys.platform == "win32":
        import ctypes
        try:
            get_dpi = ctypes.WinDLL("user32").GetDpiForWindow
            get_dpi.argtypes = [ctypes.c_void_p]
            get_dpi.restype = ctypes.c_uint
            dpi = get_dpi(root.winfo_id()) or dpi
        except (AttributeError, OSError):
            pass
    root.tk.call("tk", "scaling", dpi / 72.0)
    return max(0.75, dpi / 96.0)


@dataclass
class Prize:
    activity: str
    minutes: int
    stock: int

    def validate(self) -> None:
        if not isinstance(self.activity, str) or not self.activity.strip():
            raise ValueError("奖品内容不能为空。")
        if len(self.activity.strip()) > 80:
            raise ValueError("奖品内容最多输入 80 个字符。")
        if type(self.minutes) is not int or not 0 <= self.minutes <= 100000:
            raise ValueError("每次奖励分钟必须是 0～100000 的整数；0 表示物品奖励。")
        if type(self.stock) is not int or not 0 <= self.stock <= 100000:
            raise ValueError("初始份数必须是 0～100000 的整数。")


@dataclass
class Rules:
    four_bonus: bool = True
    five_bonus: bool = True
    consolation_enabled: bool = False
    allow_negative_stock: bool = False


@dataclass(frozen=True)
class RollResult:
    title: str
    explanation: str
    awards: tuple[tuple[str, int], ...] = ()


def parse_dice_input(text: str) -> list[int]:
    """支持连续六位数字，也支持从文本粘贴空格或逗号分隔的点数。"""
    compact = "".join(text.split()).translate(str.maketrans("", "", ",，、"))
    if len(compact) != 6 or any(character not in "123456" for character in compact):
        raise ValueError("请输入六个 1～6 的数字，例如 111665。\n也可以用空格或逗号分隔。")
    return [int(character) for character in compact]


def classify_dice(dice: list[int], rules: Rules | None = None) -> RollResult:
    """按组合优先级判定，骰子顺序不影响结果。"""
    if len(dice) != 6 or any(type(n) is not int or not 1 <= n <= 6 for n in dice):
        raise ValueError("请输入六个 1～6 的整数。")
    rules = rules or Rules()
    counts = Counter(dice)
    red = counts[4]
    most = max(counts.values())
    if most == 6:
        name = "六勃红" if red == 6 else ("遍地锦" if counts[1] == 6 else "六勃黑")
        return RollResult("状元 · " + name, "六颗骰子同点，领取一份状元和一份插金花奖励。",
                          (("状元", 1), ("插金花", 1)))
    if red == 5:
        return RollResult("状元 · 五红", "五个四点。", (("状元", 1),))
    if red == 4:
        flower = counts[1] == 2
        awards = (("状元", 1), ("插金花", 1)) if flower else (("状元", 1),)
        return RollResult("状元 · 插金花" if flower else "状元 · 四红",
                          "四个四点、两个一点，额外领取一份插金花奖励。" if flower else "四个四点。", awards)
    if most == 5:
        bonus = red == 1 and rules.five_bonus
        return RollResult("状元 · 五子带一秀" if bonus else "状元 · 五子登科",
                          "五颗非四点的骰子同点。", (("状元", 1), ("一秀", 1)) if bonus else (("状元", 1),))
    if len(counts) == 6:
        return RollResult("对堂", "一到六点各一个。", (("对堂", 1),))
    if red == 3:
        return RollResult("三红", "三个四点。", (("三红", 1),))
    if most == 4:
        awards = (("四进", 1),)
        title = "四进"
        if rules.four_bonus and red in (1, 2):
            extra = "一秀" if red == 1 else "二举"
            title += "带" + extra
            awards += ((extra, 1),)
        return RollResult(title, "四颗非四点的骰子同点。", awards)
    if red == 2:
        return RollResult("二举", "两个四点。", (("二举", 1),))
    if red == 1:
        return RollResult("一秀", "一个四点。", (("一秀", 1),))
    if rules.consolation_enabled:
        return RollResult("安慰奖", "没有符合其他奖项的组合，获得一份安慰奖。", (("安慰奖", 1),))
    return RollResult("未中奖", "没有四点，也没有符合奖项的同点组合。")


@dataclass
class Balance:
    earned_ms: int = 0
    used_ms: int = 0
    wins: int = 0

    @property
    def available_ms(self) -> int:
        return self.earned_ms - self.used_ms


@dataclass
class ItemBalance:
    earned: int = 0
    claimed: int = 0

    @property
    def available(self) -> int:
        return self.earned - self.claimed


@dataclass
class WorkCredits:
    """跨会话累计工作秒数，机会按工作时长、手动添加量与已用量计算。"""

    minutes_per_roll: int = 25
    work_seconds: int = 0
    manual_added: int = 0
    spent: int = 0
    reset_at: str | None = None

    def validate(self) -> None:
        if type(self.minutes_per_roll) is not int or not 1 <= self.minutes_per_roll <= 100000:
            raise ValueError("每次博饼所需工作分钟必须是 1～100000 的整数。")
        for value in (self.work_seconds, self.manual_added, self.spent):
            if type(value) is not int or value < 0:
                raise ValueError("工作时长或博饼次数存档无效。")
        if self.reset_at is not None:
            if not isinstance(self.reset_at, str):
                raise ValueError("工作兑换统计的重设时间无效。")
            parse_utc(self.reset_at)

    def reset(self, at) -> None:
        timestamp = at.isoformat()
        parse_utc(timestamp)
        self.work_seconds = self.manual_added = self.spent = 0
        self.reset_at = timestamp

    @property
    def earned(self) -> int:
        return self.work_seconds // (self.minutes_per_roll * 60)

    @property
    def available(self) -> int:
        return self.earned + self.manual_added - self.spent

    @property
    def seconds_to_next(self) -> int:
        return self.minutes_per_roll * 60 - self.work_seconds % (self.minutes_per_roll * 60)

    def add_work(self, delta_seconds: int) -> None:
        if type(delta_seconds) is not int or self.work_seconds + delta_seconds < 0:
            raise ValueError("修正后的累计工作时间不能小于零。")
        self.work_seconds += delta_seconds

    def add_manual(self, count: int) -> None:
        if type(count) is not int or not 1 <= count <= 100000:
            raise ValueError("手动增加次数必须是 1～100000 的整数。")
        self.manual_added += count

    def consume_roll(self) -> None:
        if self.available <= 0:
            raise ValueError("暂无可用博饼次数，请先工作或手动增加次数。")
        self.spent += 1


APP_NAME = "ActivityTracker"
BUTTON_COLUMNS = 3


class TaskCatalog:
    """任务的标识固定，界面名称和日历名称均可修改。"""

    def __init__(self, tasks=None):
        if tasks is None:
            tasks = [
                {"name": "Work", "label": "工作", "calendar_name": "Work", "work": True, "export": True},
                {"name": "Leisure", "label": "休闲", "calendar_name": "Leisure", "work": False, "export": True},
            ]
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("请至少保留一个任务。")
        self.tasks = []
        labels, identifiers = set(), set()
        for original in tasks:
            if not isinstance(original, dict):
                raise ValueError("每个任务必须是完整的任务配置。")
            task = dict(original)
            task.setdefault("label", task.get("name"))
            task.setdefault("calendar_name", task.get("name"))
            task.setdefault("enabled", True)
            for key in ("name", "label", "calendar_name"):
                value = task.get(key)
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
                    raise ValueError("任务名称和日历名称必须填写 1～80 个字符。")
                task[key] = value.strip()
            if any(type(task.get(key)) is not bool for key in ("work", "export", "enabled")):
                raise ValueError("任务的工作、日历和启用设置必须为布尔值。")
            if task["name"] in identifiers:
                raise ValueError("任务标识重复。")
            if task["enabled"] and task["label"] in labels:
                raise ValueError("任务显示名称不能重复。")
            identifiers.add(task["name"])
            if task["enabled"]:
                labels.add(task["label"])
            self.tasks.append({key: task[key] for key in ("name", "label", "calendar_name", "work", "export", "enabled")})
        if not labels:
            raise ValueError("请至少保留一个可选任务。")
        self.settings = {task["name"]: task for task in self.tasks}

    @property
    def visible(self):
        return [task for task in self.tasks if task["enabled"]]

    def label(self, name):
        return self.settings.get(name, {}).get("label", name)

    def calendar_name(self, name):
        return self.settings.get(name, {}).get("calendar_name", name)

    def to_list(self):
        return [dict(task) for task in self.tasks]


class TaskStore:
    def __init__(self, path):
        self.path = path

    def load(self, fallback=None):
        if not self.path.exists():
            return TaskCatalog(fallback)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data["version"] != 1:
                raise ValueError("不支持的任务列表版本。")
            return TaskCatalog(data["tasks"])
        except (KeyError, TypeError, UnicodeError) as error:
            raise ValueError("任务列表文件格式无效。") from error

    def save(self, catalog):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "tasks": catalog.to_list()},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)



def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def format_ics_time(dt_text):
    return datetime.fromisoformat(dt_text).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def install_startup():
    """保留 --install-startup；使用 Windows 快捷方式，不生成 BAT。"""
    if sys.platform != "win32":
        raise ValueError("开机启动快捷方式仅支持 Windows。")
    import os
    import subprocess
    import base64
    startup_folder = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    python_exe = Path(sys.executable)
    if python_exe.name.lower() == "python.exe" and python_exe.with_name("pythonw.exe").exists():
        python_exe = python_exe.with_name("pythonw.exe")
    script_path = Path(__file__).resolve()
    shortcut_path = startup_folder / "博饼工作计时.lnk"

    def ps_string(value):
        return "'" + str(value).replace("'", "''") + "'"

    command = (
        "$ErrorActionPreference = 'Stop'; "
        "$taskShell = New-Object -ComObject WScript.Shell; "
        f"$taskLink = $taskShell.CreateShortcut({ps_string(shortcut_path)}); "
        f"$taskLink.TargetPath = {ps_string(python_exe)}; "
        f"$taskLink.Arguments = {ps_string(chr(34) + str(script_path) + chr(34))}; "
        f"$taskLink.WorkingDirectory = {ps_string(script_path.parent)}; "
        "$taskLink.Save()"
    )
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                        base64.b64encode(command.encode("utf-16-le")).decode("ascii")],
                       check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.CalledProcessError as error:
        raise OSError("Windows 未能创建开机启动快捷方式。") from error
    return shortcut_path


def parse_utc(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("记录时间必须包含时区。")
    return result.astimezone(timezone.utc).replace(microsecond=0)


def local_time_text(value):
    return parse_utc(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def parse_local_time(value, reference):
    """编辑框接受完整日期时间，或所选记录同一天的 HH:MM[:SS]。"""
    text = value.strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            result = datetime.strptime(text, pattern)
        except ValueError:
            continue
        if pattern.startswith("%H"):
            date = parse_utc(reference).astimezone()
            result = result.replace(year=date.year, month=date.month, day=date.day)
        return result.astimezone(timezone.utc).replace(microsecond=0)
    raise ValueError("请输入日期时间，例如 2026-10-12 14:30:00；同一天也可只填 14:30。")


class ActivityTimeline:
    """不重叠的活动区间；空档由时间范围自动计算，不参与工作或日历导出。"""

    def __init__(self, started, catalog=None):
        self.catalog = catalog or TaskCatalog()
        self.started_utc = started.isoformat()
        self.events = []
        self.current_option = None
        self.current_start = None

    @staticmethod
    def event(name, start, end, identifier=None):
        return {"id": identifier or str(uuid.uuid4()), "option_name": name,
                "start_utc": start.isoformat(), "end_utc": end.isoformat()}

    @staticmethod
    def validate_events(events):
        if not isinstance(events, list):
            raise ValueError("活动记录必须是列表。")
        identifiers = set()
        previous_end = None
        for event in sorted(events, key=lambda item: parse_utc(item["start_utc"])):
            start, end = parse_utc(event["start_utc"]), parse_utc(event["end_utc"])
            if (not isinstance(event["id"], str) or not event["id"] or event["id"] in identifiers
                    or not isinstance(event["option_name"], str) or not event["option_name"].strip()):
                raise ValueError("活动名称或记录标识无效。")
            if end <= start:
                raise ValueError("结束时间必须晚于开始时间。")
            if previous_end is not None and start < previous_end:
                raise ValueError("时间与另一条活动记录重叠，请先调整相邻记录或拆分所选记录。")
            identifiers.add(event["id"])
            previous_end = end

    def intervals(self, now):
        rows = [dict(event) for event in self.events]
        if self.current_option and self.current_start:
            start = parse_utc(self.current_start)
            if now > start:
                rows.append(self.event(self.current_option, start, now, "current"))
        return sorted(rows, key=lambda item: parse_utc(item["start_utc"]))

    def rows(self, now):
        intervals = self.intervals(now)
        cursor = min([parse_utc(self.started_utc)] + [parse_utc(event["start_utc"]) for event in intervals])
        result = []
        for event in intervals:
            start, end = parse_utc(event["start_utc"]), parse_utc(event["end_utc"])
            if start > cursor:
                result.append({"id": "gap:" + cursor.isoformat(), "kind": "gap", "option_name": None,
                               "start_utc": cursor.isoformat(), "end_utc": start.isoformat()})
            event["kind"] = "current" if event["id"] == "current" else "event"
            result.append(event)
            cursor = max(cursor, end)
        if now > cursor:
            result.append({"id": "gap:" + cursor.isoformat(), "kind": "gap", "option_name": None,
                           "start_utc": cursor.isoformat(), "end_utc": now.isoformat()})
        return result

    def work_seconds(self, now, since=None):
        cutoff = parse_utc(since) if isinstance(since, str) else since
        seconds = 0
        for event in self.intervals(now):
            if not self.catalog.settings.get(event["option_name"], {}).get("work", False):
                continue
            start, end = parse_utc(event["start_utc"]), parse_utc(event["end_utc"])
            if cutoff is not None:
                start = max(start, cutoff)
            if end > start:
                seconds += int((end - start).total_seconds())
        return seconds

    def finalize(self, now):
        identifier = None
        if self.current_option and self.current_start:
            start = parse_utc(self.current_start)
            if now > start:
                event = self.event(self.current_option, start, now)
                self.events.append(event)
                identifier = event["id"]
        self.current_option = self.current_start = None
        return identifier

    def select(self, name, now):
        if name not in self.catalog.settings:
            raise ValueError("未知活动。")
        self.finalize(now)
        self.current_option, self.current_start = name, now.isoformat()

    def get(self, identifier):
        for event in self.events:
            if event["id"] == identifier:
                return dict(event)
        raise ValueError("这条记录已变化，请重新选择。")

    def _commit(self, events, now):
        all_events = list(events)
        if self.current_option and self.current_start:
            start = parse_utc(self.current_start)
            if now > start:
                all_events.append(self.event(self.current_option, start, now, "current"))
        self.validate_events(all_events)
        self.events = sorted(events, key=lambda item: parse_utc(item["start_utc"]))
        if events:
            self.started_utc = min(parse_utc(self.started_utc),
                                   min(parse_utc(item["start_utc"]) for item in events)).isoformat()

    def change(self, identifier, name, start, end, now):
        if name not in self.catalog.settings:
            raise ValueError("请选择活动。")
        if end <= start:
            raise ValueError("结束时间必须晚于开始时间。")
        if end > now:
            raise ValueError("不能将尚未发生的时间记为已完成活动。")
        if identifier is not None:
            self.get(identifier)
        event = self.event(name, start, end, identifier)
        events = [item for item in self.events if item["id"] != identifier] + [event]
        self._commit(events, now)
        return event["id"]

    def split(self, identifier, at, left_name, right_name, now):
        event = self.get(identifier)
        start, end = parse_utc(event["start_utc"]), parse_utc(event["end_utc"])
        if not start < at < end:
            raise ValueError("拆分时间必须在所选事件的开始与结束时间之间。")
        if left_name not in self.catalog.settings or right_name not in self.catalog.settings:
            raise ValueError("请选择拆分后两段的活动。")
        left = self.event(left_name, start, at, identifier)
        right = self.event(right_name, at, end)
        events = [item for item in self.events if item["id"] != identifier] + [left, right]
        self._commit(events, now)
        return left["id"], right["id"]

    def merge_candidates(self, identifiers, now):
        if len(identifiers) < 2 or len(set(identifiers)) != len(identifiers):
            raise ValueError("请按住 Ctrl 或 Shift 选择至少两条活动事件。")
        available = {event["id"]: event for event in self.intervals(now)}
        if any(identifier not in available for identifier in identifiers):
            raise ValueError("只能合并活动事件，不能合并未分配时间；请重新选择。")
        events = sorted((available[identifier] for identifier in identifiers), key=lambda event: parse_utc(event["start_utc"]))
        for left, right in zip(events, events[1:]):
            if parse_utc(left["end_utc"]) != parse_utc(right["start_utc"]):
                raise ValueError("所选活动的时间不相邻，不能合并。")
        return events

    def merge(self, identifiers, name, now):
        events = self.merge_candidates(identifiers, now)
        names = {event["option_name"] for event in events}
        if name is None:
            if len(names) != 1:
                raise ValueError("请选择合并后的任务。")
            name = next(iter(names))
        if name not in self.catalog.settings:
            raise ValueError("请选择合并后的任务。")
        start, end = parse_utc(events[0]["start_utc"]), parse_utc(events[-1]["end_utc"])
        remaining = [event for event in self.events if event["id"] not in identifiers]
        if "current" in identifiers:
            # 合并包含正在计时的一段时，从合并后的起点继续计时。
            self.validate_events(remaining + [self.event(name, start, end, "current")])
            self.events = remaining
            self.current_option, self.current_start = name, start.isoformat()
            return "current"
        event = self.event(name, start, end, events[0]["id"])
        self._commit(remaining + [event], now)
        return event["id"]


    def remove(self, identifier, now):
        self.get(identifier)
        self._commit([event for event in self.events if event["id"] != identifier], now)

    def to_dict(self, now, credit_since=None):
        data = {"version": 2, "started_utc": self.started_utc, "events": [dict(event) for event in self.events],
                "current_option": self.current_option, "current_start": self.current_start,
                "checkpoint_utc": now.isoformat(), "accounted_seconds": self.work_seconds(now)}
        if credit_since is not None:
            data["credit_reset_at"] = credit_since
            data["credit_accounted_seconds"] = self.work_seconds(now, credit_since)
        return data

    @classmethod
    def from_dict(cls, data, catalog=None):
        try:
            started = parse_utc(data["started_utc"])
            checkpoint = parse_utc(data["checkpoint_utc"])
            result = cls(started, catalog)
            result.events = [dict(event) for event in data["events"]]
            result.current_option, result.current_start = data.get("current_option"), data.get("current_start")
            if (result.current_option is None) != (result.current_start is None):
                raise ValueError("正在计时的记录格式无效。")
            if result.current_option is not None:
                if not isinstance(result.current_option, str) or parse_utc(result.current_start) > checkpoint:
                    raise ValueError("正在计时的活动或时间无效。")
            if any(parse_utc(event["end_utc"]) > checkpoint for event in result.events):
                raise ValueError("活动记录晚于保存时间。")
            result.validate_events(result.intervals(checkpoint))
            accounted = data["accounted_seconds"]
            if type(accounted) is not int or accounted < 0:
                raise ValueError("已结算工作时间无效。")
            credited = data.get("credit_accounted_seconds", accounted)
            if type(credited) is not int or not 0 <= credited <= accounted:
                raise ValueError("已结算兑换工作时间无效。")
            reset_at = data.get("credit_reset_at")
            if reset_at is not None and parse_utc(reset_at) > checkpoint:
                raise ValueError("统计重设时间不能晚于保存时间。")
            return result
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("活动时间轴存档格式无效。") from error


class WrappingParagraph(tk.Text):
    """按字符自然换行，并根据实际显示行数调整高度，避免中文被英文空格切碎。"""

    def __init__(self, parent, text, host):
        super().__init__(parent, wrap="char", width=1, height=1, borderwidth=0, highlightthickness=0,
                         background=host.CARD, foreground=host.MUTED,
                         font=("Microsoft YaHei UI", 10), cursor="arrow", padx=0, pady=0,
                         takefocus=False)
        self.insert("1.0", text)
        self.configure(state="disabled")
        self.pending = None
        self.last_width = None
        self.bind("<Configure>", self._resize)

    def _resize(self, event):
        if event.width != self.last_width:
            self.last_width = event.width
            if self.pending is None:
                self.pending = self.after_idle(self._fit_height)

    def _fit_height(self):
        self.pending = None
        count = self.count("1.0", "end-1c", "displaylines")
        self.configure(height=max(1, (count[0] if count else 0) + 1))


class ActivityTrackerApp:
    def __init__(self, root, parent, host, data_dir):
        self.root, self.host, self.px = root, host, host.px
        self.catalog = host.catalog
        self.data_dir = data_dir
        self.autosave_path = data_dir / "bobing_current_session.json"
        self.ics_path = data_dir / "activity_records_this_session.ics"
        self.after_id = None
        self.save_error = False
        self.row_data = {}
        self.editor = None
        self.paragraphs = []
        self.left_activity_choices = {self.catalog.label(activity["name"]): activity["name"] for activity in self.catalog.visible}
        self.timeline = ActivityTimeline(utc_now(), self.catalog)
        saved = host.game.activity_timeline
        if saved is not None:
            self.timeline = ActivityTimeline.from_dict(saved, self.catalog)
            checkpoint = parse_utc(saved["checkpoint_utc"])
            # 只结算上次已经保存的活动时间；程序关闭期间自动呈现为空档。
            self.timeline.finalize(checkpoint)
            host.work_accounted_seconds = saved.get("credit_accounted_seconds",
                self.timeline.work_seconds(checkpoint, host.game.work.reset_at) if host.game.work.reset_at
                else saved["accounted_seconds"])
        elif self.autosave_path.exists():
            self._import_legacy_session()
        self.selected_var = tk.StringVar(value="未选择活动")
        self.activity_time_var = tk.StringVar(value="暂无活动记录")
        self.work_time_var = tk.StringVar()
        self.left_minutes_var = tk.StringVar()
        self.left_activity_var = tk.StringVar(value=self.catalog.label(self.catalog.visible[0]["name"]) if self.catalog.visible else "")
        self._build(parent)
        self._rebuild_task_buttons()
        self.update_work_timer()

    @property
    def events(self):
        return self.timeline.events

    @property
    def current_option(self):
        return self.timeline.current_option

    @property
    def current_start(self):
        return self.timeline.current_start

    def _import_legacy_session(self):
        # 旧版明细已计入博饼机会，导入时设定基线，避免重复获得机会。
        try:
            data = json.loads(self.autosave_path.read_text(encoding="utf-8"))
            if data.get("version") == 2:
                timeline = ActivityTimeline.from_dict(data, self.catalog)
                checkpoint = parse_utc(data["checkpoint_utc"])
                timeline.finalize(checkpoint)
            else:
                events = []
                for event in data.get("events", []):
                    start, end = parse_utc(event["start_utc"]), parse_utc(event["end_utc"])
                    if end > start:
                        events.append(ActivityTimeline.event(event["option_name"], start, end))
                checkpoint = datetime.fromtimestamp(self.autosave_path.stat().st_mtime, timezone.utc).replace(microsecond=0)
                if data.get("current_option") and data.get("current_start"):
                    start = parse_utc(data["current_start"])
                    if checkpoint > start:
                        events.append(ActivityTimeline.event(data["current_option"], start, checkpoint))
                ActivityTimeline.validate_events(events)
                if not events:
                    return
                timeline = ActivityTimeline(min(parse_utc(item["start_utc"]) for item in events), self.catalog)
                timeline.events = sorted(events, key=lambda item: parse_utc(item["start_utc"]))
                checkpoint = max(checkpoint, max(parse_utc(item["end_utc"]) for item in events))
            credits = self.host.game.work
            if (data.get("work") is not None and credits.reset_at is None
                    and credits.work_seconds == credits.manual_added == credits.spent == 0):
                credits = WorkCredits(**data["work"])
                credits.validate()
                self.host.game.work = credits
            seconds = timeline.work_seconds(checkpoint, credits.reset_at)
            self.timeline = timeline
            self.host.work_accounted_seconds = data.get("credit_accounted_seconds", seconds)
            credits.work_seconds = max(credits.work_seconds, seconds)
        except (ValueError, KeyError, TypeError, OSError) as error:
            # 不覆盖无法识别的旧明细，避免在启动时丢失可修复的数据。
            raise ValueError(f"无法导入工作计时明细：{self.autosave_path}\n{error}") from error

    def _build(self, parent):
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, background=self.host.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas, padding=self.px((0, 0, 12, 12)))
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        content.columnconfigure(0, weight=3, uniform="tracker")
        content.columnconfigure(1, weight=2, uniform="tracker")
        activity_card = self.host._card(content)
        activity_card.grid(row=0, column=0, sticky="nsew", padx=self.px((0, 12)))
        heading = ttk.Frame(activity_card, style="Card.TFrame")
        heading.pack(fill="x")
        ttk.Label(heading, text="工作计时", style="Section.TLabel").pack(side="left")
        self.tasks_button = ttk.Button(heading, text="编辑任务列表", command=self.edit_task_list)
        self.tasks_button.pack(side="right")
        ttk.Label(activity_card, textvariable=self.activity_time_var, style="CardMuted.TLabel",
                  wraplength=self.px(620)).pack(fill="x", pady=self.px((10, 4)))
        self.text_box = ttk.Label(activity_card, textvariable=self.selected_var,
                                  font=("Microsoft YaHei UI", 22), anchor="center", style="Card.TLabel",
                                  padding=self.px((8, 12)))
        self.text_box.pack(fill="x", pady=self.px((0, 10)))
        button_frame = ttk.Frame(activity_card, style="Card.TFrame")
        self.activity_button_frame = button_frame
        button_frame.pack(fill="x")
        for column in range(BUTTON_COLUMNS):
            button_frame.columnconfigure(column, weight=1, uniform="activities")
        self.activity_buttons = {}
        for index, activity in enumerate(self.catalog.visible):
            name = activity["name"]
            button = ttk.Button(button_frame, text=self.catalog.label(name), width=15,
                                style="Activity.TButton", command=lambda option=name: self.select_option(option))
            button.grid(row=index // BUTTON_COLUMNS, column=index % BUTTON_COLUMNS,
                        sticky="ew", padx=self.px(4), pady=self.px(4))
            self.activity_buttons[name] = button
        self.clear_button = ttk.Button(activity_card, text="清空当前活动", command=self.clear_current_selection)
        self.clear_button.pack(pady=self.px((12, 0)))
        self.work_time_label = ttk.Label(activity_card, textvariable=self.work_time_var,
                                         font=("Microsoft YaHei UI", 12, "bold"), style="Card.TLabel")
        self.work_time_label.pack(fill="x", pady=self.px((16, 4)))

        chance_card = self.host._card(content)
        chance_card.grid(row=0, column=1, sticky="nsew")
        ttk.Label(chance_card, text="工作兑换博饼机会", style="Section.TLabel").pack(anchor="w")
        self.quota_var = tk.StringVar(value=str(self.host.game.work.minutes_per_roll))
        quota_row = ttk.Frame(chance_card, style="Card.TFrame")
        quota_row.pack(fill="x", pady=self.px(6))
        ttk.Label(quota_row, text="每工作", style="Card.TLabel").pack(side="left")
        ttk.Spinbox(quota_row, from_=1, to=100000, width=6, textvariable=self.quota_var).pack(side="left", padx=self.px(6))
        ttk.Label(quota_row, text="分钟，博饼一次", style="Card.TLabel").pack(side="left")
        quota_buttons = ttk.Frame(chance_card, style="Card.TFrame")
        quota_buttons.pack(fill="x", pady=self.px((4, 10)))
        ttk.Button(quota_buttons, text="保存工作兑换设置", style="Green.TButton",
                   command=self.host.save_work_config).pack(side="left")
        self.reset_credits_button = ttk.Button(quota_buttons, text="重设", command=self.host.reset_work_credits)
        self.reset_credits_button.pack(side="left", padx=self.px((8, 0)))
        ttk.Label(chance_card, textvariable=self.host.credit_detail_var, style="CardMuted.TLabel").pack(anchor="w", fill="x")
        self.credit_progress = ttk.Progressbar(chance_card, maximum=100)
        self.credit_progress.pack(fill="x", pady=self.px((12, 6)))
        ttk.Label(chance_card, textvariable=self.host.progress_var, style="Card.TLabel",
                  wraplength=self.px(440)).pack(anchor="w", fill="x")
        for paragraph in (
            "工作活动计入博饼机会，休息活动不计入。零散工作时间和未用次数会保留。",
            "修改兑换分钟数或调整事件后，会按工作时长重算次数。已用掉的次数如需扣回，会显示待补回次数，之后可用工作或手动添加补回。",
            "每次掷骰扣除一次机会，未中奖也会扣除。重新开始博饼会保留工作时长与剩余机会。",
        ):
            widget = WrappingParagraph(chance_card, paragraph, self.host)
            widget.pack(fill="x", pady=self.px((12, 0)))
            self.paragraphs.append(widget)

        records = self.host._card(content)
        records.grid(row=1, column=0, columnspan=2, sticky="ew", pady=self.px((12, 0)))
        records_heading = ttk.Frame(records, style="Card.TFrame")
        records_heading.pack(fill="x", pady=self.px((0, 8)))
        ttk.Label(records_heading, text="活动事件与未分配时间", style="Section.TLabel").pack(side="left")
        ttk.Label(records_heading, text="双击记录编辑；双击空档补记活动", style="CardMuted.TLabel").pack(side="right")
        self.event_tree = self.host._tree(records, ("activity", "english", "start", "end", "duration", "status"),
            ("活动（中文）", "日历名称（英文）", "开始时间", "结束时间", "时长", "状态"),
            (145, 150, 170, 170, 110, 90), height=6)
        self.event_tree.configure(selectmode="extended")
        self.event_tree.tag_configure("gap", foreground=self.host.MUTED, background="#f3eee5")
        self.event_tree.tag_configure("current", foreground=self.host.GREEN)
        self.event_tree.bind("<<TreeviewSelect>>", self._select_record)
        self.event_tree.bind("<Double-1>", lambda _event: self.edit_selected_event())
        tools = ttk.Frame(records, style="Card.TFrame")
        tools.pack(fill="x", pady=self.px((10, 0)))
        self.edit_button = ttk.Button(tools, text="编辑 / 分配所选时间", command=self.edit_selected_event)
        self.edit_button.pack(side="left")
        self.split_button = ttk.Button(tools, text="拆分所选事件", command=self.split_selected_event)
        self.split_button.pack(side="left", padx=self.px(8))
        self.unassign_button = ttk.Button(tools, text="恢复为未分配", command=self.unassign_selected_event)
        self.unassign_button.pack(side="left")
        self.merge_button = ttk.Button(tools, text="合并所选事件", command=self.merge_selected_events)
        self.merge_button.pack(side="left", padx=self.px(8))
        ttk.Button(tools, text="新增事件", command=self.new_event).pack(side="right")

        correction = self.host._card(content)
        correction.grid(row=2, column=0, columnspan=2, sticky="ew", pady=self.px((12, 0)))
        ttk.Label(correction, text="快速修正所选事件的最后几分钟", style="Section.TLabel").pack(anchor="w")
        left_frame = ttk.Frame(correction, style="Card.TFrame")
        left_frame.pack(fill="x", pady=self.px((10, 6)))
        left_frame.columnconfigure(3, weight=1)
        self.left_button = ttk.Button(left_frame, text="修正", command=self.record_time_away)
        self.left_button.grid(row=0, column=0, padx=self.px((0, 8)))
        self.left_minutes_entry = ttk.Entry(left_frame, textvariable=self.left_minutes_var, width=7, validate="key",
            validatecommand=(self.root.register(self.valid_minutes_input), "%P"))
        self.left_minutes_entry.grid(row=0, column=1)
        ttk.Label(left_frame, text="分钟，实际活动为", style="Card.TLabel").grid(row=0, column=2, padx=self.px(8))
        self.left_activity_dropdown = ttk.Combobox(left_frame, textvariable=self.left_activity_var,
            state="readonly", values=list(self.left_activity_choices), width=20)
        self.left_activity_dropdown.grid(row=0, column=3, sticky="ew")
        ttk.Label(correction, text="可反复修正已结束的事件；未选择事件时修正当前计时。编辑当前计时前会先结束该段。",
                  style="CardMuted.TLabel", wraplength=self.px(1030)).pack(anchor="w", fill="x")

        def layout(event):
            canvas.itemconfigure(window, width=event.width)
            if event.width < self.px(1080):
                content.columnconfigure(0, weight=1)
                content.columnconfigure(1, weight=0)
                activity_card.grid_configure(row=0, column=0, columnspan=2, padx=0)
                chance_card.grid_configure(row=1, column=0, columnspan=2, pady=self.px((12, 0)))
                records.grid_configure(row=2)
                correction.grid_configure(row=3)
            else:
                content.columnconfigure(0, weight=3)
                content.columnconfigure(1, weight=2)
                activity_card.grid_configure(row=0, column=0, columnspan=1, padx=self.px((0, 12)))
                chance_card.grid_configure(row=0, column=1, columnspan=1, pady=0)
                records.grid_configure(row=1)
                correction.grid_configure(row=2)

        canvas.bind("<Configure>", layout)

        def scroll(event):
            if str(self.host.tabs.select()) == str(parent):
                canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"

        def bind_wheel(widget):
            if isinstance(widget, WrappingParagraph) or not isinstance(widget, (ttk.Treeview, ttk.Combobox, ttk.Spinbox, tk.Text)):
                widget.bind("<MouseWheel>", scroll, add="+")
            for child in widget.winfo_children():
                bind_wheel(child)

        bind_wheel(content)
        self.scroll_canvas = canvas

    def _save_session(self):
        try:
            now = utc_now()
            data = self.timeline.to_dict(now, self.host.game.work.reset_at)
            work = asdict(self.host.game.work)
            # 备份的工作账户与同一时刻的明细一致，重设后不会恢复重设前的工作时间。
            work["work_seconds"] += self.timeline.work_seconds(now, self.host.game.work.reset_at) - self.host.work_accounted_seconds
            data["work"] = work
            temporary = self.autosave_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.autosave_path)
        except OSError as error:
            self.host.status.set(f"工作记录备份失败：{error}")
            if not self.save_error:
                messagebox.showwarning("工作记录备份失败", str(error), parent=self.root)
            self.save_error = True
            return False
        self.save_error = False
        return True

    def _changed(self, select=None):
        self.refresh_time_displays()
        if select and self.event_tree.exists(select):
            self.event_tree.selection_set(select)
            self.event_tree.see(select)
        self._save_session()
        self.host._save()

    def select_option(self, option_name):
        self.event_tree.selection_remove(*self.event_tree.selection())
        self.timeline.select(option_name, utc_now())
        self.selected_var.set(self.catalog.label(option_name))
        self._changed()

    def finish_current_selection(self, end_time):
        return self.timeline.finalize(parse_utc(end_time))

    def clear_current_selection(self):
        identifier = self.timeline.finalize(utc_now())
        self.selected_var.set("未选择活动")
        self._changed(select=identifier)

    @staticmethod
    def valid_minutes_input(value):
        return value == "" or (value.isascii() and value.isdigit())

    def selected_record(self):
        selection = self.event_tree.selection()
        return self.row_data.get(selection[0]) if len(selection) == 1 else None

    def _select_record(self, _event=None):
        row = self.selected_record()
        selected = row is not None
        event = selected and row["kind"] != "gap"
        self.edit_button.configure(state="normal" if selected else "disabled",
                                   text="分配所选空档" if selected and not event else "编辑所选事件")
        self.split_button.configure(state="normal" if event else "disabled")
        self.unassign_button.configure(state="normal" if event else "disabled")
        self.left_button.configure(state="normal" if event or self.current_option else "disabled")
        self.merge_button.configure(state="normal" if len(self.event_tree.selection()) >= 2 else "disabled")

    def _refresh_records(self, now):
        rows = self.timeline.rows(now)
        self.row_data = {row["id"]: row for row in rows}
        for identifier in self.event_tree.get_children():
            if identifier not in self.row_data:
                self.event_tree.delete(identifier)
        for index, row in enumerate(rows):
            kind, name = row["kind"], row["option_name"]
            seconds = int((parse_utc(row["end_utc"]) - parse_utc(row["start_utc"])).total_seconds())
            values = ("未分配时间" if kind == "gap" else self.catalog.label(name), "" if kind == "gap" else self.catalog.calendar_name(name),
                      local_time_text(row["start_utc"]), local_time_text(row["end_utc"]),
                      self.format_activity_duration(seconds),
                      {"gap": "可补记", "current": "正在计时", "event": "已记录"}[kind])
            if self.event_tree.exists(row["id"]):
                self.event_tree.item(row["id"], values=values, tags=(kind,))
                self.event_tree.move(row["id"], "", index)
            else:
                self.event_tree.insert("", index, iid=row["id"], values=values, tags=(kind,))
        self._select_record()

    def _editable_record(self):
        if len(self.event_tree.selection()) > 1:
            messagebox.showinfo("请选择一条记录", "编辑或拆分时请选择一条记录；多条记录可点击合并。", parent=self.root)
            return None
        row = self.selected_record()
        if row and row["kind"] == "current":
            identifier = self.timeline.finalize(utc_now())
            self.selected_var.set("未选择活动")
            self._changed(select=identifier)
            row = self.selected_record()
        return row

    def record_time_away(self):
        if len(self.event_tree.selection()) > 1:
            messagebox.showinfo("请选择一条记录", "快速修正时请选择一条活动记录。", parent=self.root)
            return
        value = self.left_minutes_var.get()
        try:
            minutes = int(value) if self.valid_minutes_input(value) and value else 0
        except ValueError:
            minutes = 0
        if minutes <= 0:
            messagebox.showerror("分钟数无效", "请输入大于零的整数分钟。", parent=self.root)
            return
        name = self.left_activity_choices.get(self.left_activity_var.get(), self.left_activity_var.get())
        if name not in self.catalog.settings:
            messagebox.showerror("请选择活动", "请选择实际进行的活动。", parent=self.root)
            return
        # 没有选中已记录事件时，仍支持原来的当前活动快速离席修正。
        self.refresh_time_displays()
        row = self.selected_record()
        if row is None or row["kind"] == "gap":
            row = self.row_data.get("current")
        if row is None or row["kind"] == "gap":
            messagebox.showerror("先选择事件", "请在记录列表中选择一个事件，或先开始工作计时。", parent=self.root)
            return
        start, end = parse_utc(row["start_utc"]), parse_utc(row["end_utc"])
        if minutes * 60 > (end - start).total_seconds():
            messagebox.showerror("时长超出范围", "修正分钟不能超过所选事件的时长。", parent=self.root)
            return
        if row["kind"] == "current":
            identifier = self.timeline.finalize(utc_now())
            row = self.timeline.get(identifier)
            start, end = parse_utc(row["start_utc"]), parse_utc(row["end_utc"])
            self.selected_var.set("未选择活动")
        identifier = row["id"]
        cutoff = end - timedelta(minutes=minutes)
        if cutoff == start:
            self.timeline.change(identifier, name, start, end, utc_now())
        else:
            self.timeline.split(identifier, cutoff, row["option_name"], name, utc_now())
        self.left_minutes_var.set("")
        self._changed(select=identifier)
        self.host.status.set("离席时间已修正。可继续选择任意事件修改或拆分。")

    def apply_event_change(self, identifier, name, start, end):
        identifier = self.timeline.change(identifier, name, start, end, utc_now())
        self._changed(select=identifier)
        return identifier

    def apply_event_split(self, identifier, at, left_name, right_name):
        identifiers = self.timeline.split(identifier, at, left_name, right_name, utc_now())
        self._changed(select=identifiers[0])
        return identifiers

    def _dialog(self, title):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.configure(background=self.host.BG)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=self.px(20))
        frame.pack(fill="both", expand=True)
        return dialog, frame

    def _show_dialog(self, dialog):
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        self.editor = dialog

    def edit_selected_event(self):
        row = self._editable_record()
        if row is None:
            messagebox.showinfo("先选择记录", "请点击一条活动记录或未分配时间。", parent=self.root)
            return
        self._edit_dialog(row)

    def new_event(self):
        row = self.selected_record()
        if row is None or row["kind"] != "gap":
            gaps = [item for item in self.timeline.rows(utc_now()) if item["kind"] == "gap"]
            if not gaps:
                messagebox.showinfo("没有未分配时间", "已有时间都已分配。可编辑事件缩短时长，或将事件恢复为未分配后补记。", parent=self.root)
                return
            row = gaps[-1]
        self._edit_dialog(row)

    def _edit_dialog(self, row):
        gap = row["kind"] == "gap"
        identifier = None if gap else row["id"]
        dialog, frame = self._dialog("分配未分配时间" if gap else "编辑活动事件")
        name_var = tk.StringVar(value=self.catalog.label(row["option_name"] or self.catalog.visible[0]["name"]))
        start_var, end_var = tk.StringVar(value=local_time_text(row["start_utc"])), tk.StringVar(value=local_time_text(row["end_utc"]))
        duration = int((parse_utc(row["end_utc"]) - parse_utc(row["start_utc"])).total_seconds())
        minutes_var = tk.StringVar(value=f"{duration / 60:g}")
        ttk.Label(frame, text="活动（中文显示，日历导出英文）").grid(row=0, column=0, sticky="w", pady=self.px(6))
        ttk.Combobox(frame, textvariable=name_var, state="readonly", values=list(self.left_activity_choices), width=28).grid(
            row=0, column=1, sticky="ew", padx=self.px((12, 0)))
        for line, label, variable in ((1, "开始时间", start_var), (2, "结束时间", end_var)):
            ttk.Label(frame, text=label).grid(row=line, column=0, sticky="w", pady=self.px(8))
            ttk.Entry(frame, textvariable=variable, width=28).grid(row=line, column=1, sticky="ew", padx=self.px((12, 0)))
        ttk.Label(frame, text="时长（分钟）").grid(row=3, column=0, sticky="w", pady=self.px(8))
        duration_frame = ttk.Frame(frame)
        duration_frame.grid(row=3, column=1, sticky="ew", padx=self.px((12, 0)))
        ttk.Entry(duration_frame, textvariable=minutes_var, width=9).pack(side="left")

        def set_end():
            try:
                seconds = round(float(minutes_var.get()) * 60)
                if seconds <= 0:
                    raise ValueError("分钟数必须大于零。")
                start = parse_local_time(start_var.get(), row["start_utc"])
                end_var.set(local_time_text((start + timedelta(seconds=seconds)).isoformat()))
            except (ValueError, OverflowError) as error:
                messagebox.showwarning("时间无效", str(error), parent=dialog)

        ttk.Button(duration_frame, text="据此设置结束时间", command=set_end).pack(side="left", padx=self.px(8))
        ttk.Label(frame, text="时间使用电脑本地时区。同一天可只填写 HH:MM 或 HH:MM:SS。\n"
                  "缩短或移动事件留下的时间会自动显示为未分配，记录不能互相重叠。",
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=self.px(12))
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew")

        def save():
            try:
                start = parse_local_time(start_var.get(), row["start_utc"])
                end = parse_local_time(end_var.get(), row["end_utc"])
                name = self.left_activity_choices.get(name_var.get(), name_var.get())
                self.apply_event_change(identifier, name, start, end)
            except ValueError as error:
                messagebox.showwarning("事件未保存", str(error), parent=dialog)
                return
            self.host.status.set("事件已保存，工作时长和博饼次数已同步更新。")
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="保存事件", style="Green.TButton", command=save).pack(side="right", padx=self.px(8))
        self._show_dialog(dialog)

    def split_selected_event(self):
        row = self._editable_record()
        if row is None or row["kind"] == "gap":
            messagebox.showinfo("先选择事件", "请选择一条活动记录进行拆分。", parent=self.root)
            return
        dialog, frame = self._dialog("拆分活动事件")
        start, end = parse_utc(row["start_utc"]), parse_utc(row["end_utc"])
        at_var = tk.StringVar(value=local_time_text((start + (end - start) / 2).replace(microsecond=0).isoformat()))
        first_minutes = tk.StringVar(value=f"{(end - start).total_seconds() / 120:g}")
        left_var = tk.StringVar(value=self.catalog.label(row["option_name"]))
        right_var = tk.StringVar(value=self.catalog.label(row["option_name"]))
        ttk.Label(frame, text=f"原记录：{local_time_text(row['start_utc'])} 至 {local_time_text(row['end_utc'])}").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=self.px((0, 12)))
        for line, label, variable in ((1, "前一段活动", left_var), (2, "后一段活动", right_var)):
            ttk.Label(frame, text=label).grid(row=line, column=0, sticky="w", pady=self.px(8))
            ttk.Combobox(frame, textvariable=variable, values=list(self.left_activity_choices), state="readonly", width=28).grid(
                row=line, column=1, sticky="ew", padx=self.px(12))
        ttk.Label(frame, text="拆分时间").grid(row=3, column=0, sticky="w", pady=self.px(8))
        ttk.Entry(frame, textvariable=at_var, width=28).grid(row=3, column=1, sticky="ew", padx=self.px(12))
        ttk.Label(frame, text="前一段分钟数").grid(row=4, column=0, sticky="w", pady=self.px(8))
        duration_frame = ttk.Frame(frame)
        duration_frame.grid(row=4, column=1, sticky="ew", padx=self.px(12))
        ttk.Entry(duration_frame, textvariable=first_minutes, width=8).pack(side="left")

        def set_split():
            try:
                seconds = round(float(first_minutes.get()) * 60)
                at_var.set(local_time_text((start + timedelta(seconds=seconds)).isoformat()))
            except (ValueError, OverflowError) as error:
                messagebox.showwarning("分钟数无效", str(error), parent=dialog)

        ttk.Button(duration_frame, text="据此设置拆分时间", command=set_split).pack(side="left", padx=self.px(8))
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=self.px((16, 0)))

        def save():
            try:
                at = parse_local_time(at_var.get(), row["start_utc"])
                left = self.left_activity_choices.get(left_var.get(), left_var.get())
                right = self.left_activity_choices.get(right_var.get(), right_var.get())
                self.apply_event_split(row["id"], at, left, right)
            except ValueError as error:
                messagebox.showwarning("未能拆分", str(error), parent=dialog)
                return
            self.host.status.set("记录已拆分，两段都可以继续编辑或再次拆分。")
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="保存拆分", style="Green.TButton", command=save).pack(side="right", padx=self.px(8))
        self._show_dialog(dialog)

    def unassign_selected_event(self):
        row = self._editable_record()
        if row is None or row["kind"] == "gap":
            return
        if messagebox.askyesno("恢复为未分配时间", "将这段时间移回未分配？之后可以再次补记活动。\n工作时间与博饼机会会相应重算。", parent=self.root):
            self.timeline.remove(row["id"], utc_now())
            self._changed()

    def _rebuild_task_buttons(self):
        for button in self.activity_buttons.values():
            button.destroy()
        self.activity_buttons = {}
        columns = min(BUTTON_COLUMNS, len(self.catalog.visible))
        for column in range(BUTTON_COLUMNS):
            self.activity_button_frame.columnconfigure(column, weight=1 if column < columns else 0, uniform="activities")
        for index, task in enumerate(self.catalog.visible):
            name = task["name"]
            button = ttk.Button(self.activity_button_frame, text=self.catalog.label(name), width=15,
                                style="Activity.TButton", command=lambda option=name: self.select_option(option))
            button.grid(row=index // columns, column=index % columns, sticky="ew",
                        padx=self.px(4), pady=self.px(4))
            self.activity_buttons[name] = button
        self.left_activity_choices = {task["label"]: task["name"] for task in self.catalog.visible}
        self.left_activity_dropdown.configure(values=list(self.left_activity_choices))
        if self.left_activity_var.get() not in self.left_activity_choices:
            self.left_activity_var.set(next(iter(self.left_activity_choices)))

    def apply_task_options(self, tasks):
        tasks = [dict(task, enabled=True) for task in tasks]
        names = {task["name"] for task in tasks}
        referenced = {event["option_name"] for event in self.events}
        if self.current_option:
            referenced.add(self.current_option)
        # 删除按钮选项后，已有记录仍保留名称与工作属性，直到导出清理。
        tasks.extend(dict(task, enabled=False) for task in self.catalog.tasks
                     if task["name"] not in names and task["name"] in referenced)
        catalog = TaskCatalog(tasks)
        self.host.task_store.save(catalog)
        if self.current_option and self.current_option not in names:
            self.timeline.finalize(utc_now())
        self.catalog = self.host.catalog = self.timeline.catalog = catalog
        self.host.game.task_options = catalog.to_list()
        self._rebuild_task_buttons()
        self._changed()
        return not self.host.save_error

    def edit_task_list(self):
        dialog, frame = self._dialog("编辑任务列表")
        rows = [dict(task) for task in self.catalog.visible]
        ttk.Label(frame, text="显示名称用于按钮；日历名称用于导出的日历。可添加、删除和调整顺序。",
                  wraplength=self.px(620)).pack(anchor="w", pady=self.px((0, 10)))
        table = ttk.Frame(frame)
        table.pack(fill="both", expand=True)
        tree = ttk.Treeview(table, columns=("label", "calendar", "work", "export"),
                            show="headings", selectmode="browse", height=8)
        for key, heading, width in (("label", "显示名称", 190), ("calendar", "日历名称", 200),
                                    ("work", "计入工作", 100), ("export", "导出日历", 100)):
            tree.heading(key, text=heading)
            tree.column(key, width=self.px(width), minwidth=self.px(70))
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        form = ttk.Frame(frame)
        form.pack(fill="x", pady=self.px(12))
        label, calendar = tk.StringVar(), tk.StringVar()
        work, export = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        ttk.Label(form, text="显示名称").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=label, width=26).grid(row=0, column=1, padx=self.px(8), sticky="ew")
        ttk.Label(form, text="日历名称").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=calendar, width=26).grid(row=0, column=3, padx=self.px(8), sticky="ew")
        ttk.Checkbutton(form, text="计入工作时间", variable=work).grid(row=1, column=1, sticky="w", pady=self.px(8))
        ttk.Checkbutton(form, text="导出到日历", variable=export).grid(row=1, column=3, sticky="w", pady=self.px(8))

        def selected():
            return int(tree.selection()[0]) if tree.selection() else None

        def refresh(index=None):
            tree.delete(*tree.get_children())
            for i, task in enumerate(rows):
                tree.insert("", "end", iid=str(i), values=(task["label"], task["calendar_name"],
                    "是" if task["work"] else "否", "是" if task["export"] else "否"))
            if index is not None and 0 <= index < len(rows):
                tree.selection_set(str(index))
                load()

        def load(_event=None):
            index = selected()
            if index is None:
                return
            task = rows[index]
            label.set(task["label"])
            calendar.set(task["calendar_name"])
            work.set(task["work"])
            export.set(task["export"])

        def apply_row(add=False):
            index = None if add else selected()
            if index is None and not add:
                return True
            task = {"name": str(uuid.uuid4()) if add else rows[index]["name"],
                    "label": label.get().strip(), "calendar_name": calendar.get().strip(),
                    "work": work.get(), "export": export.get(), "enabled": True}
            try:
                TaskCatalog([task])
            except ValueError as error:
                messagebox.showwarning("任务输入有误", str(error), parent=dialog)
                return False
            if add:
                rows.append(task)
                index = len(rows) - 1
            else:
                rows[index] = task
            refresh(index)
            return True

        def remove():
            index = selected()
            if index is not None:
                rows.pop(index)
                refresh(min(index, len(rows) - 1) if rows else None)
                if not rows:
                    label.set("")
                    calendar.set("")

        def move(delta):
            index = selected()
            if index is not None and 0 <= index + delta < len(rows):
                rows[index], rows[index + delta] = rows[index + delta], rows[index]
                refresh(index + delta)

        row_buttons = ttk.Frame(frame)
        row_buttons.pack(fill="x")
        for text, action in (("新增任务", lambda: apply_row(True)), ("应用这一行", apply_row),
                             ("删除任务", remove), ("上移", lambda: move(-1)), ("下移", lambda: move(1))):
            ttk.Button(row_buttons, text=text, command=action).pack(side="left", padx=self.px((0, 6)))
        ttk.Label(frame, text="删除选项不会删除已有计时记录。修改工作属性会重算尚未导出活动的工作时长和博饼机会。",
                  wraplength=self.px(670), style="Muted.TLabel").pack(anchor="w", pady=self.px(12))

        def save():
            if not apply_row():
                return
            try:
                if self.apply_task_options(rows):
                    dialog.destroy()
            except (ValueError, OSError) as error:
                messagebox.showwarning("任务列表未保存", str(error), parent=dialog)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="保存任务列表", style="Green.TButton", command=save).pack(side="right", padx=self.px(8))
        tree.bind("<<TreeviewSelect>>", load)
        refresh(0)
        self._show_dialog(dialog)

    def show_activity_mapping(self):
        self.edit_task_list()

    def merge_selected_events(self):
        identifiers = list(self.event_tree.selection())
        try:
            rows = self.timeline.merge_candidates(identifiers, utc_now())
        except ValueError as error:
            messagebox.showwarning("不能合并", str(error), parent=self.root)
            return
        names = {row["option_name"] for row in rows}
        if len(names) == 1:
            self.apply_event_merge(identifiers, next(iter(names)))
            return
        dialog, frame = self._dialog("选择合并后的任务")
        ttk.Label(frame, text="所选相邻事件属于不同任务，请选择合并后的任务。").pack(anchor="w")
        choice = tk.StringVar(value=self.catalog.label(rows[0]["option_name"]))
        ttk.Combobox(frame, textvariable=choice, values=list(self.left_activity_choices),
                     state="readonly", width=30).pack(fill="x", pady=self.px(16))

        def merge():
            try:
                name = self.left_activity_choices.get(choice.get())
                self.apply_event_merge(identifiers, name)
            except ValueError as error:
                messagebox.showwarning("不能合并", str(error), parent=dialog)
                return
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="合并", style="Green.TButton", command=merge).pack(side="right", padx=self.px(8))
        self._show_dialog(dialog)

    def apply_event_merge(self, identifiers, name=None):
        identifier = self.timeline.merge(identifiers, name, utc_now())
        self._changed(select=identifier)
        return identifier


    @staticmethod
    def format_activity_duration(total_seconds):
        hours, remainder = divmod(max(0, int(total_seconds)), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def format_work_time(total_seconds):
        hours, remainder = divmod(total_seconds, 3600)
        return f"{hours} 小时 {remainder // 60} 分钟"

    def refresh_time_displays(self):
        now = utc_now()
        if self.current_option and self.current_start:
            seconds = (now - parse_utc(self.current_start)).total_seconds()
            text = f"当前：{self.catalog.label(self.current_option)}｜{self.format_activity_duration(seconds)}"
        elif self.events:
            last = self.events[-1]
            seconds = (parse_utc(last["end_utc"]) - parse_utc(last["start_utc"])).total_seconds()
            text = f"上一段：{self.catalog.label(last['option_name'])}｜{self.format_activity_duration(seconds)}（已结束）"
        else:
            text = "暂无活动记录"
        self.activity_time_var.set(text)
        self.selected_var.set(self.catalog.label(self.current_option) if self.current_option else "未选择活动")
        for name, button in self.activity_buttons.items():
            button.configure(style="SelectedActivity.TButton" if name == self.current_option else "Activity.TButton")
        seconds = self.timeline.work_seconds(now)
        self.work_time_var.set(f"记录中的工作时长：{self.format_work_time(seconds)}")
        self._refresh_records(now)
        self.host._work_changed(self.timeline.work_seconds(now, self.host.game.work.reset_at))

    def work_seconds_this_session(self, now=None):
        return self.timeline.work_seconds(now or utc_now())

    def update_work_timer(self):
        self.after_id = None
        self.refresh_time_displays()
        self.after_id = self.root.after(1000, self.update_work_timer)

    def cancel_tick(self):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    def hide_window(self):
        self.root.iconify()

    def end_program(self):
        self.host.close()

    def export_to_ics(self, events=None, exit_time=None):
        exit_time = exit_time or utc_now()
        events = list(self.events[:-1] if events is None else events)

        def escape_text(value):
            return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")

        lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
                 f"PRODID:-//{APP_NAME}//Activity Records//EN", "CALSCALE:GREGORIAN"]
        for event in events:
            name = event["option_name"]
            if not self.catalog.settings.get(name, {}).get("export", True):
                continue
            lines.extend(["BEGIN:VEVENT", f"UID:{event['id']}",
                          f"DTSTAMP:{format_ics_time(exit_time.isoformat())}",
                          f"DTSTART:{format_ics_time(event['start_utc'])}",
                          f"DTEND:{format_ics_time(event['end_utc'])}",
                          f"SUMMARY:{escape_text(self.catalog.calendar_name(name))}", "END:VEVENT"])
        lines.append("END:VCALENDAR")
        folded = []
        for line in lines:
            chunk = ""
            for character in line:
                if len((chunk + character).encode("utf-8")) > 75:
                    folded.append(chunk)
                    chunk = " "
                chunk += character
            folded.append(chunk)
        payload = ("\r\n".join(folded) + "\r\n").encode("utf-8")
        # 退出保存失败后重试，复用同一文件，避免为同一批事件生成重复日历。
        signature = tuple((event["id"], event["option_name"], event["start_utc"], event["end_utc"]) for event in events)
        pending = getattr(self, "pending_exit_export", None)
        if pending and pending[0] == signature:
            path = pending[1]
        else:
            stem = "activity_records_" + exit_time.astimezone().strftime("%Y-%m-%d_%H-%M-%S")
            path = self.data_dir / (stem + ".ics")
            suffix = 2
            while path.exists():
                path = self.data_dir / f"{stem}_{suffix}.ics"
                suffix += 1
            self.pending_exit_export = (signature, path)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
        self.ics_path = path
        return path


@dataclass
class Game:
    prizes: dict[str, Prize] = field(default_factory=lambda: {
        c: Prize("默认奖品", m, INITIAL_STOCK[c])
        for c, m in zip(CATEGORIES, (30, 20, 10, 8, 5, 2, 30, 1))
    })
    remaining: dict[str, int] = field(default_factory=lambda: INITIAL_STOCK.copy())
    rules: Rules = field(default_factory=Rules)
    balances: dict[str, Balance] = field(default_factory=dict)
    item_balances: dict[str, ItemBalance] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    roll_count: int = 0
    session: dict | None = None
    work: WorkCredits = field(default_factory=WorkCredits)
    activity_timeline: dict | None = None
    task_options: list | None = None

    def award(self, dice: list[int]) -> dict:
        result = classify_dice(dice, self.rules)
        granted, depleted = [], []
        for category, requested in result.awards:
            count = (requested if self.rules.allow_negative_stock
                     else min(requested, max(0, self.remaining[category])))
            if count:
                prize = self.prizes[category]
                self.remaining[category] -= count
                if prize.minutes == 0:
                    self.item_balances.setdefault(prize.activity, ItemBalance()).earned += count
                else:
                    balance = self.balances.setdefault(prize.activity, Balance())
                    balance.earned_ms += prize.minutes * count * 60000
                    balance.wins += count
                granted.append({"category": category, "activity": prize.activity,
                                "minutes": prize.minutes * count, "count": count})
            if count < requested:
                depleted.append(category + ("库存不足" if count else "已领完"))
        self.roll_count += 1
        row = {"number": self.roll_count, "time": datetime.now().strftime("%H:%M:%S"),
               "dice": list(dice), "title": result.title, "explanation": result.explanation,
               "granted": granted, "depleted": depleted}
        self.history.append(row)
        # 保留最近 3000 条记录；余额及当前局掷骰总次数不受此限制影响。
        self.history = self.history[-3000:]
        return row

    def configure(self, prizes: dict[str, Prize], rules: Rules) -> None:
        """配置只改变今后的奖励，已获得的时间和物品保持原值。"""
        updated_remaining = {}
        if set(prizes) != set(CATEGORIES):
            raise ValueError("必须配置全部八个奖项（含插金花和安慰奖）。")
        for category in CATEGORIES:
            prize = prizes[category]
            prize.validate()
            claimed = self.prizes[category].stock - self.remaining[category]
            # 关闭超库存领奖时，已有负库存仍保留；修改其他设置不能因此失败。
            if (prize.stock < claimed and prize.stock != self.prizes[category].stock
                    and not rules.allow_negative_stock):
                raise ValueError(f"{category}本局已领取 {claimed} 份，初始份数不能少于此数。\n"
                                 "请先重新开始博饼，再减少份数。")
            updated_remaining[category] = prize.stock - claimed
        self.prizes = prizes
        self.remaining = updated_remaining
        self.rules = rules

    def restart(self) -> None:
        """只重置本局库存和博饼记录，奖励余额、计时与工作账户均保留。"""
        self.remaining = {c: self.prizes[c].stock for c in CATEGORIES}
        self.history.clear()
        self.roll_count = 0

    def claim_item(self, name: str, count: int) -> None:
        """记录实际领取的物品；库存已在中奖时扣减，这里只扣待领取件数。"""
        if type(count) is not int or count <= 0:
            raise ValueError("领取件数必须是大于零的整数。")
        balance = self.item_balances.get(name)
        if balance is None:
            raise ValueError("请先选择一项物品奖励。")
        if count > balance.available:
            raise ValueError(f"该物品只剩 {balance.available} 件待领取。")
        balance.claimed += count

    def withdraw_time(self, activity: str, count: int) -> None:
        if type(count) is not int or count <= 0:
            raise ValueError("领取件数必须是大于零的整数；时间奖励一件等于一分钟。")
        balance = self.balances.get(activity)
        if balance is None or count * 60000 > balance.available_ms:
            raise ValueError("该时间奖励的剩余分钟不足。")
        balance.used_ms += count * 60000
        if self.session and self.session["activity"] == activity:
            self.session["remaining_ms"] = min(self.session["remaining_ms"], balance.available_ms)
            if self.session["remaining_ms"] <= 0:
                self.session = None

    def start_timer(self, activity: str, duration_ms: int) -> None:
        if self.session is not None:
            raise ValueError("请先结束当前倒计时，或继续已暂停的倒计时。")
        if type(duration_ms) is not int or duration_ms <= 0:
            raise ValueError("使用时间必须大于零。")
        balance = self.balances.get(activity)
        if balance is None or duration_ms > balance.available_ms:
            raise ValueError("该奖品的剩余时间不足。")
        self.session = {"activity": activity, "remaining_ms": duration_ms, "total_ms": duration_ms}

    def consume(self, elapsed_ms: int) -> bool:
        """只扣实际经过的时间；不会预先扣除整场倒计时的分钟。"""
        if self.session is None:
            return False
        session = self.session
        balance = self.balances[session["activity"]]
        used = min(max(0, elapsed_ms), session["remaining_ms"], balance.available_ms)
        balance.used_ms += used
        session["remaining_ms"] -= used
        finished = session["remaining_ms"] <= 0
        if finished:
            self.session = None
        return finished

    def to_dict(self) -> dict:
        return {"version": 1, "prizes": {c: asdict(p) for c, p in self.prizes.items()},
                "remaining": self.remaining, "rules": asdict(self.rules),
                "balances": {a: asdict(b) for a, b in self.balances.items()},
                "item_balances": {a: asdict(b) for a, b in self.item_balances.items()},
                "history": self.history, "roll_count": self.roll_count, "session": self.session,
                "work": asdict(self.work), "activity_timeline": self.activity_timeline, "task_options": self.task_options}

    @classmethod
    def from_dict(cls, data: dict) -> Game:
        try:
            if data["version"] != 1:
                raise ValueError("不支持的数据版本。")
            game = cls()
            game.task_options = data.get("task_options")
            if game.task_options is not None:
                game.task_options = TaskCatalog(game.task_options).to_list()
            game.work = WorkCredits(**data.get("work", {}))
            game.work.validate()
            game.activity_timeline = data.get("activity_timeline")
            if game.activity_timeline is not None:
                timeline = ActivityTimeline.from_dict(game.activity_timeline, TaskCatalog(game.task_options))
                checkpoint = parse_utc(game.activity_timeline["checkpoint_utc"])
                if game.work.reset_at is not None and parse_utc(game.work.reset_at) > checkpoint:
                    raise ValueError("统计重设时间不能晚于保存时间。")
                saved_reset = game.activity_timeline.get("credit_reset_at")
                if saved_reset is not None and (game.work.reset_at is None
                        or parse_utc(saved_reset) != parse_utc(game.work.reset_at)):
                    raise ValueError("工作账户与活动记录的重设时间不一致。")
                credited = game.activity_timeline.get("credit_accounted_seconds",
                    timeline.work_seconds(checkpoint, game.work.reset_at) if game.work.reset_at
                    else game.activity_timeline["accounted_seconds"])
                if credited > game.work.work_seconds:
                    raise ValueError("活动记录的兑换工作时长超过累计工作时长。")
            # 原六个奖项必须完整；旧文件缺少的新增奖项使用默认配置。
            for category in CATEGORIES[:6]:
                game.prizes[category] = Prize(**data["prizes"][category])
            for category in CATEGORIES[6:]:
                if category in data["prizes"]:
                    game.prizes[category] = Prize(**data["prizes"][category])
            for prize in game.prizes.values():
                prize.validate()
            game.remaining = {c: data["remaining"][c] for c in CATEGORIES[:6]}
            for category in CATEGORIES[6:]:
                game.remaining[category] = (data["remaining"][category] if category in data["prizes"]
                                            else game.prizes[category].stock)
            if any(type(n) is not int or n > game.prizes[c].stock
                   for c, n in game.remaining.items()):
                raise ValueError("库存数据无效。")
            # 已删除的插金花兼得对堂选项只在读取旧文件时忽略，后续保存不再包含。
            game.rules = Rules(**{key: value for key, value in data["rules"].items()
                                  if key != "flower_bonus"})
            if any(type(v) is not bool for v in asdict(game.rules).values()):
                raise ValueError("规则设置无效。")
            game.balances = {a: Balance(**b) for a, b in data["balances"].items()}
            for activity, balance in game.balances.items():
                if not isinstance(activity, str) or not activity.strip():
                    raise ValueError("奖品活动无效。")
                if any(type(v) is not int or v < 0 for v in asdict(balance).values()):
                    raise ValueError("奖励时间无效。")
                if balance.used_ms > balance.earned_ms:
                    raise ValueError("已用时间超过累计奖励。")
            # 旧版保存文件没有物品余额，保持原来的分钟、库存、工作与事件数据。
            game.item_balances = {a: ItemBalance(**b) for a, b in data.get("item_balances", {}).items()}
            for name, balance in game.item_balances.items():
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("物品名称无效。")
                if any(type(v) is not int or v < 0 for v in asdict(balance).values()):
                    raise ValueError("物品奖励数量无效。")
                if balance.claimed > balance.earned:
                    raise ValueError("已领取件数超过累计获得件数。")
            game.roll_count = data["roll_count"]
            if type(game.roll_count) is not int or game.roll_count < 0:
                raise ValueError("掷骰次数无效。")
            game.history = data["history"]
            if not isinstance(game.history, list) or len(game.history) > 3000:
                raise ValueError("历史记录无效。")
            for row in game.history:
                classify_dice(row["dice"])
                if (type(row["number"]) is not int or not isinstance(row["time"], str)
                        or not isinstance(row["title"], str) or not isinstance(row["explanation"], str)
                        or not isinstance(row["granted"], list) or not isinstance(row["depleted"], list)):
                    raise ValueError("历史记录格式无效。")
                for reward in row["granted"]:
                    if (reward["category"] not in CATEGORIES or not isinstance(reward["activity"], str)
                            or type(reward["minutes"]) is not int or type(reward["count"]) is not int):
                        raise ValueError("历史奖励无效。")
                if any(not isinstance(v, str) for v in row["depleted"]):
                    raise ValueError("历史库存记录无效。")
            game.session = data.get("session")
            if game.session is not None:
                session = game.session
                if (session["activity"] not in game.balances
                        or type(session["remaining_ms"]) is not int
                        or type(session["total_ms"]) is not int
                        or not 0 < session["remaining_ms"] <= session["total_ms"]
                        or session["remaining_ms"] > game.balances[session["activity"]].available_ms):
                    raise ValueError("倒计时记录无效。")
            return game
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("数据文件格式无效。") from error


class Store:
    """原子写入，防止普通保存中断造成半个 JSON 文件。"""

    def __init__(self, path: Path):
        self.path = path
        self.lock_file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if __import__("sys").platform == "win32":
            import msvcrt
            lock_path = self.path.with_suffix(".lock")
            self.lock_file = lock_path.open("a+b")
            if lock_path.stat().st_size == 0:
                self.lock_file.write(b"0")
                self.lock_file.flush()
            self.lock_file.seek(0)
            try:
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                self.lock_file.close()
                self.lock_file = None
                raise ValueError("另一窗口正在使用这份博饼数据，请使用已打开的窗口。") from error

    def load(self) -> Game:
        if not self.path.exists():
            return Game()
        try:
            return Game.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (ValueError, UnicodeError) as error:
            raise ValueError(f"无法读取保存的数据：{self.path}\n{error}\n\n"
                             "原文件不会被覆盖。请保留备份，再修复或改名此文件后启动。") from error

    def save(self, game: Game) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(game.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def close(self) -> None:
        if self.lock_file is not None:
            self.lock_file.close()
            self.lock_file = None


def format_time(milliseconds: int, ceiling: bool = False) -> str:
    seconds = math.ceil(milliseconds / 1000) if ceiling else milliseconds // 1000
    minutes, seconds = divmod(max(0, seconds), 60)
    return f"{minutes}分{seconds:02d}秒" if seconds else f"{minutes}分钟"


def timer_digits(milliseconds: int) -> str:
    seconds = max(0, math.ceil(milliseconds / 1000))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def reward_text(row: dict) -> str:
    messages = [f"{r['category']}：{r['activity']} +{r['count']}件" if r["minutes"] == 0
                else f"{r['category']}：{r['activity']} +{r['minutes']}分钟"
                + (f"（{r['count']}份）" if r["count"] > 1 else "") for r in row["granted"]]
    messages.extend(row["depleted"])
    return "；".join(messages) or "本次没有奖励"


class BobingApp:
    BG = "#f5f2ed"
    CARD = "#ffffff"
    INK = "#292823"
    MUTED = "#766e63"
    RED = "#aa4034"
    GREEN = "#276858"

    def __init__(self, root: tk.Tk, store: Store, game: Game):
        self.root, self.store, self.game = root, store, game
        self.task_store = TaskStore(store.path.parent / "bobing_tasks.json")
        self.catalog = self.task_store.load(game.task_options)
        game.task_options = self.catalog.to_list()
        if not self.task_store.path.exists():
            self.task_store.save(self.catalog)
        self.ui_scale = display_scale(root)
        self.running = False
        self.last_tick = 0
        self.last_save = 0
        self.after_id = None
        self.save_error = False
        self.closing = False
        self.work_accounted_seconds = 0
        self.credit_last_save = time.monotonic()
        self.tracker = None
        self.chance_var = tk.StringVar()
        self.credit_detail_var = tk.StringVar()
        self.progress_var = tk.StringVar()
        self.add_chances_var = tk.StringVar(value="1")
        self.manual_dice_var = tk.StringVar(value="")
        self.status = tk.StringVar(value="选择工作活动开始计时，或手动增加博饼次数。")
        self.result_title = tk.StringVar(value="等待掷骰")
        self.result_detail = tk.StringVar(value="随机掷骰会立即判定；手动输入后点击「判定并领奖」。")
        self.root.title("工作时间博饼")
        if WINDOW_ICON.is_file():
            try:
                self.root.iconbitmap(str(WINDOW_ICON))
                # 新建的设置、事件编辑等对话框也使用这个图标。
                self.root.iconbitmap(default=str(WINDOW_ICON))
            except (tk.TclError, OSError):
                pass
        width = min(self.px(1180), root.winfo_screenwidth() - self.px(80))
        height = min(self.px(960), root.winfo_screenheight() - self.px(100))
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height - self.px(48)) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(self.px(980), width), min(self.px(820), height))
        self.root.configure(background=self.BG)
        self._style()
        self._build()
        self._load_form()
        self._refresh_stock()
        self._refresh_balances()
        self._refresh_items()
        self._refresh_history()
        if game.history:
            self._show_result(game.history[-1])
            self._draw_dice(game.history[-1]["dice"])
            self.manual_dice_var.set("".join(map(str, game.history[-1]["dice"])))
        if game.session:
            self.status.set("已恢复上次的倒计时，当前为暂停状态；关闭程序期间不扣时间。")
        self._refresh_timer()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

    def px(self, value):
        """将以 96 DPI 编写的布局尺寸转成当前屏幕原生像素。"""
        if isinstance(value, (tuple, list)):
            return tuple(self.px(item) for item in value)
        return round(value * self.ui_scale)

    def _style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("TLabel", background=self.BG, foreground=self.INK)
        style.configure("Card.TLabel", background=self.CARD, foreground=self.INK)
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED)
        style.configure("CardMuted.TLabel", background=self.CARD, foreground=self.MUTED)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"), foreground=self.RED)
        style.configure("Section.TLabel", background=self.CARD, font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Result.TLabel", background=self.CARD, foreground=self.RED,
                        font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Timer.TLabel", background=self.CARD, foreground=self.GREEN,
                        font=("Consolas", 28, "bold"))
        style.configure("Credit.TLabel", background=self.CARD, foreground=self.GREEN,
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Activity.TButton", font=("Microsoft YaHei UI", 11), padding=self.px((8, 9)))
        style.configure("SelectedActivity.TButton", font=("Microsoft YaHei UI", 11), padding=self.px((8, 9)),
                        background=self.GREEN, foreground="white")
        style.map("SelectedActivity.TButton", background=[("active", "#1c5143")],
                  foreground=[("active", "white")])
        style.configure("TButton", padding=self.px((12, 7)), font=("Microsoft YaHei UI", 10))
        style.configure("Accent.TButton", background=self.RED, foreground="white", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#8d3026"), ("disabled", "#d4b9b2")])
        style.configure("Green.TButton", background=self.GREEN, foreground="white", borderwidth=0)
        style.map("Green.TButton", background=[("active", "#1c5143"), ("disabled", "#a9bdb5")])
        style.configure("TEntry", padding=self.px(5))
        style.configure("TSpinbox", padding=self.px(5))
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        tab_padding = self.px((20, 9))
        style.configure("TNotebook.Tab", padding=tab_padding, font=("Microsoft YaHei UI", 10))
        # clam 的 selected 状态默认使用很小的 padding，会覆盖高 DPI 布局。
        # 两种状态都显式使用相同间距，选中时只改变颜色。
        style.map("TNotebook.Tab", padding=[("selected", tab_padding)],
                  background=[("selected", self.CARD)], foreground=[("selected", self.RED)])
        reward_padding = self.px((12, 6))
        style.configure("Rewards.TNotebook", background=self.CARD)
        style.configure("Rewards.TNotebook.Tab", padding=reward_padding)
        style.map("Rewards.TNotebook.Tab", padding=[("selected", reward_padding)])
        style.configure("Treeview", rowheight=self.px(30), background=self.CARD,
                        fieldbackground=self.CARD, foreground=self.INK, borderwidth=0)
        style.configure("Treeview.Heading", background="#ede8df", font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#e5efe9")], foreground=[("selected", self.INK)])
        style.configure("TCheckbutton", background=self.BG, foreground=self.INK)
        style.configure("TCheckbutton", indicatorsize=self.px(12),
                        indicatormargin=self.px((2, 2, 6, 2)))
        style.configure("TScrollbar", arrowsize=self.px(14), width=self.px(14))
        style.configure("TSpinbox", arrowsize=self.px(14))
        style.configure("TCombobox", arrowsize=self.px(14), padding=self.px(5))
        style.configure("TLabelframe", background=self.BG)
        style.configure("TLabelframe.Label", background=self.BG, foreground=self.INK)

    def _card(self, parent, **kwargs):
        return ttk.Frame(parent, style="Card.TFrame", padding=self.px(16), **kwargs)

    def _build(self) -> None:
        main = ttk.Frame(self.root, padding=self.px((22, 16)))
        main.pack(fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(fill="x", pady=self.px((0, 12)))
        ttk.Label(header, text="闽南博饼", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="好运连连，高潮不断", style="Muted.TLabel").pack(side="left", padx=self.px(18), pady=self.px((8, 0)))
        self.exit_button = ttk.Button(header, text="导出日历保存数据并退出", command=self.close)
        self.exit_button.pack(side="right")
        ttk.Button(header, text="重新开始博饼", command=self.restart_dialog).pack(
            side="right", padx=self.px((0, 10)))
        chances = ttk.Frame(main)
        chances.pack(fill="x", pady=self.px((0, 12)))
        ttk.Label(chances, textvariable=self.chance_var, foreground=self.GREEN,
                  font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        add = ttk.Frame(chances)
        add.pack(side="right")
        ttk.Label(add, text="增加").pack(side="left")
        ttk.Spinbox(add, from_=1, to=100000, width=6, textvariable=self.add_chances_var).pack(
            side="left", padx=self.px(8))
        ttk.Button(add, text="手动增加次数", command=self.add_chances).pack(side="left")
        self.tabs = ttk.Notebook(main)
        self.tabs.pack(fill="both", expand=True)
        self.play_tab = ttk.Frame(self.tabs, padding=self.px((0, 14, 0, 0)))
        self.work_tab = ttk.Frame(self.tabs, padding=self.px((0, 14, 0, 0)))
        self.config_tab = ttk.Frame(self.tabs, padding=self.px(20))
        self.history_tab = ttk.Frame(self.tabs, padding=self.px(14))
        self.rules_tab = ttk.Frame(self.tabs, padding=self.px(20))
        for tab, title in ((self.work_tab, "工作计时"), (self.play_tab, "博饼与奖励"), (self.config_tab, "奖品设置"),
                           (self.history_tab, "博饼记录"), (self.rules_tab, "规则与说明")):
            self.tabs.add(tab, text=title)
        self._build_play()
        self._build_config()
        self._build_history()
        self._build_rules()
        self.tracker = ActivityTrackerApp(self.root, self.work_tab, self, self.store.path.parent)
        self._refresh_chances()
        self.tabs.select(self.work_tab)
        ttk.Label(main, textvariable=self.status, style="Muted.TLabel", wraplength=self.px(1080)).pack(
            side="bottom", before=self.tabs, fill="x", pady=self.px((12, 0)))

    def _build_play(self) -> None:
        self.play_tab.columnconfigure(0, weight=1)
        self.play_tab.rowconfigure(2, weight=1)
        roll = self._card(self.play_tab)
        roll.grid(row=0, column=0, sticky="ew")
        top = ttk.Frame(roll, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="01  掷出六颗骰子", style="Section.TLabel").pack(side="left")
        self.roll_count_var = tk.StringVar()
        ttk.Label(top, textvariable=self.roll_count_var, style="CardMuted.TLabel").pack(side="right")
        dice_row = ttk.Frame(roll, style="Card.TFrame")
        dice_row.pack(fill="x", pady=self.px((12, 4)))
        self.dice_canvases = []
        for _ in range(6):
            box = ttk.Frame(dice_row, style="Card.TFrame")
            box.pack(side="left", padx=self.px((0, 12)))
            canvas = tk.Canvas(box, width=self.px(62), height=self.px(62), bg=self.CARD, highlightthickness=0)
            canvas.pack()
            self.dice_canvases.append(canvas)
        self._draw_dice([None] * 6)
        actions = ttk.Frame(dice_row, style="Card.TFrame")
        actions.pack(side="left", padx=self.px((14, 0)), fill="y")
        self.random_button = ttk.Button(actions, text="随机掷骰并领奖", style="Accent.TButton", command=self.random_roll)
        self.random_button.pack(fill="x", pady=self.px((4, 7)))
        ttk.Label(actions, text="随机结果会立即判定并领奖", style="CardMuted.TLabel").pack(pady=self.px((7, 0)))
        manual = ttk.Frame(roll, style="Card.TFrame")
        manual.pack(fill="x", pady=self.px((12, 0)))
        ttk.Label(manual, text="手动输入", style="Card.TLabel").pack(side="left")
        self.dice_input = ttk.Entry(manual, width=18, textvariable=self.manual_dice_var,
                                    justify="center", font=("Microsoft YaHei UI", 12))
        self.dice_input.pack(side="left", padx=self.px((10, 12)))
        self.dice_input.bind("<Return>", self.manual_roll)
        self.dice_input.bind("<KP_Enter>", self.manual_roll)
        self.manual_button = ttk.Button(manual, text="判定并领奖", command=self.manual_roll)
        self.manual_button.pack(side="left")
        ttk.Label(manual, text="输入六位数字，如 111665；按回车或点击判定。",
                  style="CardMuted.TLabel", wraplength=self.px(480)).pack(side="left", padx=self.px((14, 0)))
        result = self._card(self.play_tab)
        result.grid(row=1, column=0, sticky="ew", pady=self.px(12))
        ttk.Label(result, textvariable=self.result_title, style="Result.TLabel").pack(anchor="w")
        self.detail_label = ttk.Label(result, textvariable=self.result_detail, style="CardMuted.TLabel", wraplength=self.px(1040))
        self.detail_label.pack(anchor="w", fill="x", pady=self.px((5, 0)))
        result.bind("<Configure>", lambda event: self.detail_label.configure(
            wraplength=max(self.px(400), event.width - self.px(40))))
        lower = ttk.Frame(self.play_tab)
        lower.grid(row=2, column=0, sticky="nsew")
        lower.columnconfigure(0, weight=2)
        lower.columnconfigure(1, weight=5)
        lower.rowconfigure(0, weight=1)
        stock = self._card(lower)
        stock.grid(row=0, column=0, sticky="nsew", padx=self.px((0, 12)))
        ttk.Label(stock, text="02  本局奖品库存", style="Section.TLabel").pack(anchor="w", pady=self.px((0, 12)))
        self.stock_tree = self._tree(stock, ("category", "remaining", "prize"),
                                     ("奖项", "剩余 / 初始", "每次奖励"), (65, 100, 190), height=len(CATEGORIES))
        self.stock_tree.tag_configure("negative", foreground=self.RED)
        ttk.Button(stock, text="编辑奖品与份数", command=lambda: self.tabs.select(self.config_tab)).pack(anchor="w", pady=self.px((10, 0)))
        rewards = self._card(lower)
        rewards.grid(row=0, column=1, sticky="nsew")
        ttk.Label(rewards, text="03  我的奖励", style="Section.TLabel").pack(anchor="w", pady=self.px((0, 10)))
        self.reward_tabs = ttk.Notebook(rewards, style="Rewards.TNotebook")
        self.reward_tabs.pack(fill="both", expand=True)
        self.time_rewards_tab = ttk.Frame(self.reward_tabs, style="Card.TFrame", padding=self.px((0, 8)))
        self.item_rewards_tab = ttk.Frame(self.reward_tabs, style="Card.TFrame", padding=self.px((0, 8)))
        self.reward_tabs.add(self.time_rewards_tab, text="时间倒计时")
        self.reward_tabs.add(self.item_rewards_tab, text="直接领取")
        self.balance_tree = self._tree(self.time_rewards_tab, ("activity", "available"),
                                       ("奖励", "剩余时间"), (260, 230), height=4)
        self.balance_tree.bind("<<TreeviewSelect>>", self._select_reward)
        controls = ttk.Frame(self.time_rewards_tab, style="Card.TFrame")
        controls.pack(fill="x", pady=self.px((12, 0)))
        ttk.Label(controls, text="本次使用", style="Card.TLabel").pack(side="left")
        self.use_minutes = tk.StringVar(value="2")
        ttk.Spinbox(controls, from_=1, to=100000, width=6, textvariable=self.use_minutes).pack(side="left", padx=self.px(8))
        ttk.Label(controls, text="分钟", style="Card.TLabel").pack(side="left")
        self.start_button = ttk.Button(controls, text="开始使用", style="Green.TButton", command=self.start_timer)
        self.start_button.pack(side="left", padx=self.px((12, 6)))
        self.all_button = ttk.Button(controls, text="用完所选奖励", command=lambda: self.start_timer(use_all=True))
        self.all_button.pack(side="left")
        timer_row = ttk.Frame(self.time_rewards_tab, style="Card.TFrame")
        timer_row.pack(fill="x", pady=self.px((10, 0)))
        self.timer_label = tk.StringVar(value="00:00:00")
        ttk.Label(timer_row, textvariable=self.timer_label, style="Timer.TLabel").pack(side="left")
        self.pause_button = ttk.Button(timer_row, text="暂停", command=self.toggle_pause)
        self.pause_button.pack(side="left", padx=self.px((16, 6)))
        self.stop_button = ttk.Button(timer_row, text="结束使用", command=self.stop_timer)
        self.stop_button.pack(side="left")
        self.timer_hint = tk.StringVar(value="先在上方列表中选择奖品，再开始倒计时。")
        ttk.Label(self.time_rewards_tab, textvariable=self.timer_hint, style="CardMuted.TLabel", wraplength=self.px(610)).pack(anchor="w", pady=self.px((4, 0)))
        self.item_tree = self._tree(self.item_rewards_tab, ("name", "available"),
                                    ("奖励", "剩余"), (260, 230), height=4)
        self.item_tree.bind("<<TreeviewSelect>>", self._select_item)
        item_controls = ttk.Frame(self.item_rewards_tab, style="Card.TFrame")
        item_controls.pack(fill="x", pady=self.px((12, 0)))
        ttk.Label(item_controls, text="本次领取", style="Card.TLabel").pack(side="left")
        self.claim_count = tk.StringVar(value="1")
        ttk.Spinbox(item_controls, from_=1, to=100000, width=6,
                    textvariable=self.claim_count).pack(side="left", padx=self.px(8))
        ttk.Label(item_controls, text="件", style="Card.TLabel").pack(side="left")
        self.claim_button = ttk.Button(item_controls, text="已领取奖励", style="Green.TButton",
                                       command=self.claim_items)
        self.claim_button.pack(side="left", padx=self.px((12, 0)))
        self.item_hint = tk.StringVar()
        ttk.Label(self.item_rewards_tab, textvariable=self.item_hint, style="CardMuted.TLabel",
                  wraplength=self.px(610)).pack(anchor="w", fill="x", pady=self.px((10, 0)))
        ttk.Label(self.item_rewards_tab, text="物品按件领取，时间奖励一件等于一分钟。\n点击按钮立即扣除，已自行使用的时间无需再倒计时。",
                  style="CardMuted.TLabel", wraplength=self.px(610)).pack(anchor="w", fill="x", pady=self.px((10, 0)))
        self.reward_tabs.select(self.item_rewards_tab)

    def _tree(self, parent, columns, headings, widths, height):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=height)
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            tree.column(column, width=self.px(width), minwidth=self.px(45), anchor="w", stretch=True)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    def _build_config(self) -> None:
        self.config_tab.columnconfigure(0, weight=1)
        self.config_tab.rowconfigure(0, weight=1)
        self.config_canvas = tk.Canvas(self.config_tab, background=self.BG, highlightthickness=0,
                                       yscrollincrement=self.px(20))
        scrollbar = ttk.Scrollbar(self.config_tab, orient="vertical", command=self.config_canvas.yview)
        self.config_canvas.configure(yscrollcommand=scrollbar.set)
        self.config_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        content = ttk.Frame(self.config_canvas)
        self.config_content = content
        window = self.config_canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: self.config_canvas.configure(
            scrollregion=self.config_canvas.bbox("all")))
        self.config_canvas.bind("<Configure>", lambda event: self.config_canvas.itemconfigure(
            window, width=event.width))
        ttk.Label(content, text="给每个奖项设置时间奖励或物品奖励", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(content, text="分钟为 0：每中奖一份获得一件物品；分钟大于 0：获得对应时长。库存均减 1，同名同类奖励会自动累加。",
                  style="Muted.TLabel").pack(anchor="w", pady=self.px((8, 18)))
        table = self._card(content)
        table.pack(fill="x")
        headings = ("奖项", "奖品内容（文字）", "分钟（0 为物品）", "初始份数（整数）", "当前剩余")
        for index, heading in enumerate(headings):
            ttk.Label(table, text=heading, style="Section.TLabel").grid(row=0, column=index, sticky="w", padx=self.px(10), pady=self.px((0, 10)))
        table.columnconfigure(1, weight=1)
        self.config_vars = {}
        self.config_remaining = {}
        for row, category in enumerate(CATEGORIES, 1):
            ttk.Label(table, text=category, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=self.px(10), pady=self.px(8))
            variables = tuple(tk.StringVar() for _ in range(3))
            self.config_vars[category] = variables
            for column, (variable, width) in enumerate(zip(variables, (30, 12, 12)), 1):
                ttk.Entry(table, textvariable=variable, width=width).grid(row=row, column=column, sticky="ew", padx=self.px(10), pady=self.px(8))
            self.config_remaining[category] = tk.StringVar()
            ttk.Label(table, textvariable=self.config_remaining[category], style="CardMuted.TLabel").grid(row=row, column=4, sticky="w", padx=self.px(10))
        options = ttk.LabelFrame(content, text="奖品发放规则", padding=self.px(14))
        options.pack(fill="x", pady=self.px(16))
        self.rule_vars = {key: tk.BooleanVar() for key in asdict(self.game.rules)}
        for key, text in (("four_bonus", "四进带一个 / 两个四点：兼得一秀 / 二举"),
                          ("five_bonus", "五子带一个四点：兼得一秀"),
                          ("consolation_enabled", "启用安慰奖（未博中其他奖项时获得一份安慰奖）"),
                          ("allow_negative_stock", "允许超出库存领奖（库存为 0 或负数时照常发奖，库存继续减为负数）")):
            ttk.Checkbutton(options, text=text, variable=self.rule_vars[key]).pack(anchor="w", pady=self.px(4))
        buttons = ttk.Frame(content)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="保存设置", style="Accent.TButton", command=self.save_config).pack(side="left")
        ttk.Button(buttons, text="撤销未保存的编辑", command=self._load_form).pack(side="left", padx=self.px(10))
        ttk.Label(content, text="保存后保留本局已领取份数；增加初始份数会增加剩余库存。插金花类共用「插金花」库存。\n"
                  "改名或切换奖励类型只影响今后的奖励，已获得的时间和物品不变。要重新补满库存，请点击右上角「重新开始博饼」。",
                  style="Muted.TLabel", wraplength=self.px(1000)).pack(anchor="w", pady=self.px(14))

        def scroll(event):
            if event.delta:
                units = max(1, int(abs(event.delta) / 120))
                self.config_canvas.yview_scroll(-units if event.delta > 0 else units, "units")
            return "break"

        def bind_scroll(widget):
            widget.bind("<MouseWheel>", scroll, add="+")
            for child in widget.winfo_children():
                bind_scroll(child)

        bind_scroll(self.config_canvas)

    def _build_history(self) -> None:
        ttk.Label(self.history_tab, text="本局博饼记录（最多保留最近 3000 次）", style="Muted.TLabel").pack(anchor="w", pady=self.px((0, 10)))
        self.history_tree = self._tree(self.history_tab, ("number", "time", "dice", "result", "reward"),
                                       ("次数", "时间", "六颗骰子", "奖品类别", "领取结果"), (55, 80, 135, 180, 550), height=16)

    def _build_rules(self) -> None:
        text = tk.Text(self.rules_tab, wrap="word", bg=self.CARD, fg=self.INK, relief="flat",
                       padx=self.px(24), pady=self.px(20), font=("Microsoft YaHei UI", 11), spacing1=self.px(3), spacing3=self.px(8))
        scrollbar = ttk.Scrollbar(self.rules_tab, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", "闽南博饼 · 本程序的判定规则\n\n"
                    "使用六颗骰子，四点是主要判定依据，输入顺序不影响奖项。\n"
                    "一秀：一个四点。  二举：两个四点。  三红：三个四点。\n"
                    "四进：四颗非四点的骰子同点。  对堂：一到六点各一个。\n"
                    "状元：四个四点（四红）、五个四点（五红）、五颗非四点同点（五子），或六颗同点。\n"
                    "插金花：四个四点和两个一点。六个四点为六勃红，六个一点为遍地锦，其他六同为六勃黑。\n"
                    "插金花、六勃红、遍地锦、六勃黑在状元奖励之外，额外得到一份「插金花」奖项的奖励。"
                    "四种组合共用「插金花」奖项的库存，初始为 1 份，可在奖品设置中修改内容、分钟及份数。"
                    "状元和插金花分别扣库存，其中一项库存不足时，另一项仍可正常获得。\n\n"
                    "判定先处理六同、状元和对堂，再处理其他奖项。对堂中的四点不会另算一秀。\n"
                    "四进带一秀 / 二举以及五子带一秀默认开启，可在奖品设置中开关。\n"
                    "安慰奖位于奖品列表末尾，默认库存 999 份，可修改内容、分钟和份数。"
                    "默认关闭；在奖品设置中勾选「启用安慰奖」并保存后，未博中其他奖项时获得一份安慰奖。"
                    "已有奖项库存不足时不改发安慰奖；安慰奖同样按库存规则发放，分钟为 0 时按件计入物品奖励。\n"
                    "兼得的每个奖项分别扣库存；其中一个领完，不影响另一个领取。默认限制库存，为 0 时显示类别，但不增加时间或物品奖励。\n\n"
                    "可勾选「允许超出库存领奖」，保存后库存为 0 或负数仍照常发奖。"
                    "例如一秀库存为 0，再博到一秀会获得对应时间或一件物品，库存减为 -1；再次获得则为 -2。"
                    "所有奖项与兼得奖项都适用。关闭后保留已有负库存，但库存不大于 0 的奖项停止发奖。"
                    "初始份数为 0 的奖项，开启此选项后也可以领奖。重新开始会恢复为设定的初始份数。\n\n"
                    "单人时间与物品奖励玩法\n"
                    "本程序中奖后立即发放奖励，不进行多人状元比大小、追夺奖品或六勃扫奖池。"
                    "各种状元组合共用「状元」库存；每次六同领取一份状元和一份插金花。不同地方的传统玩法可能不同。\n\n"
                    "配置与使用\n"
                    "每个奖项的三个输入框依次为：奖品内容、每次奖励分钟、初始份数。分钟大于 0 时表示时间奖励，分钟为 0 时表示物品奖励，每中奖一份获得一件。份数是本局可发放次数。\n"
                    "例如一秀设置为「看电视 / 2 / 32」，博到两次后剩 30 份，累计看电视 4 分钟。"
                    "二举也设为看电视时，奖励自动累加到同一个活动。\n"
                    "例如一秀设置为「饼干 / 0 / 32」，博到两次后剩 30 份，累计获得饼干 2 件。"
                    "同名物品按件累加，同名的时间奖励和物品奖励分别统计。"
                    "在博饼页「我的奖励 → 直接领取」中选择物品或时间奖励，填写领取件数，再点击「已领取奖励」。时间奖励一件等于一分钟。"
                    "点击按钮会立即扣除剩余奖励，不再扣库存；已自行使用的时间无需再倒计时。两个奖励列表只展示剩余奖励。\n"
                    "手动掷骰只需一个输入框：连续输入六个 1～6 的数字（例如 111665），再点击「判定并领奖」或按回车。"
                    "也支持空格或逗号分隔。输入时不会自动领奖，判定后会选中全部内容，方便直接输入下一轮。\n"
                    "选择奖励后输入本次使用分钟，或点击「用完所选奖励」。只扣实际经过的时间，余额显示到秒。"
                    "暂停停止扣时；继续恢复倒计时；结束使用保留未使用的余额。倒计时归零会弹出提示。\n"
                    "重新开始只重置本局库存、掷骰结果和博饼记录，保留已获得的全部时间和物品奖励。奖励可通过填写领取份数使用。"
                    "重新开始不会打断奖励倒计时或工作计时，也不会重置累计工作时长和博饼机会。\n\n"
                    "工作计时与博饼机会\n"
                    "工作计时页保留原工作计时软件的活动选择、清空选择、离席修正、当前/上一段时长、工作时长与日历导出。"
                    "点击「编辑任务列表」可修改任务名称、日历名称、工作属性、日历导出属性和顺序。任务保存在数据文件中，新目录默认只有工作和休闲。\n"
                    "默认每工作 25 分钟得到一次机会，可在工作计时页修改 X。未满 X 的零散时间与未用次数跨会话保存。"
                    "每次有效掷骰扣一次机会，包括未中奖或库存不足。无效输入不扣次数，兼得多个奖项仍只扣一次。"
                    "顶部可手动增加次数；重新开始博饼不清空工作时长和剩余机会。\n"
                    "工作兑换区域的「重设」按钮会清零兑换工作时长、手动增加、累计使用次数及次数欠额，保留兑换分钟设置。"
                    "活动记录、奖品和当前计时不受影响；从重设时刻重新累计工作，旧活动不会重新计入机会。\n"
                    "修改 X 会按累计工作时间重新计算应得次数。离席修正会相应增加或扣回工作机会；"
                    "如已经用掉被扣回的机会，会显示欠额，之后工作或手动添加先补回。已有奖品不追扣。\n"
                    "活动事件显示中文与英文对应，支持编辑活动及起止时间、拆分为两段和恢复为未分配时间。"
                    "可以多次修改同一事件；两个事件之间、开始与结束处的空档会自动显示为未分配时间，双击即可补记。"
                    "未分配时间不计入工作，也不导出日历。所有修改会同步更新工作时长与博饼机会。\n"
                    "退出时保留最后一个实际活动，其余活动导出并清理。下次打开后，最后活动结束到当前的时间显示为未分配。累计工作和博饼机会保留。"
                    "日历使用任务编辑器中的日历名称。选择多个时间相邻的事件可点击合并；同一任务自动合并，不同任务会要求选择合并后的任务。非相邻事件不能合并。"
                    "点击「导出日历保存数据并退出」会结束当前计时、保存数据并导出除最后一个活动之外的事件，文件按退出时间命名。少于五分钟的事件也会导出。"
                    "机会及累计工作秒数与博饼数据一起保存，每五秒保存一次、获得机会时立即保存；其他关键操作也立即保存。\n"
                    "点击窗口 × 会最小化且继续计时；右上角「导出日历保存数据并退出」会结算并关闭。"
                    "--install-startup 可安装开机启动快捷方式，不生成 BAT 文件。\n\n"
                    "数据与退出\n"
                    "奖品设置、库存、奖励余额和记录自动保存在程序旁的 bobing_data.json，倒计时每 2 秒保存一次。"
                    "点击「导出日历保存数据并退出」先结算已用时间并暂停；重新打开后可点击继续。程序关闭期间不扣时间。"
                    "异常终止最多可能丢失最近约 2 秒的扣时。请勿在程序运行时修改数据文件。\n"
                    "同一份数据在 Windows 上只允许一个窗口使用；备份请同时保留程序和 JSON 文件。\n"
                    "仅复制程序到空文件夹，会使用默认设置；不会从其他文件夹自动导入博饼存档。"
                    "复制数据文件则会恢复对应进度。工作记录也保存在同目录的 bobing_current_session.json 中，任务列表保存在 bobing_tasks.json 和主存档中。\n\n"
                    "显示清晰度\n"
                    "Windows 启动前启用原生高 DPI 绘制，并按屏幕缩放设置调整字体、控件、表格和骰子尺寸。"
                    "更新程序后请关闭旧窗口，再重新启动以应用显示改进。\n\n"
                    f"规则参考：{RULE_SOURCE}\n保存位置：{self.store.path}\n")
        text.configure(state="disabled")

    def _draw_dice(self, dice) -> None:
        dots = {1: ((1, 1),), 2: ((0, 0), (2, 2)), 3: ((0, 0), (1, 1), (2, 2)),
                4: ((0, 0), (2, 0), (0, 2), (2, 2)),
                5: ((0, 0), (2, 0), (1, 1), (0, 2), (2, 2)),
                6: ((0, 0), (2, 0), (0, 1), (2, 1), (0, 2), (2, 2))}
        for canvas, number in zip(self.dice_canvases, dice):
            canvas.delete("all")
            canvas.create_rectangle(2, 2, 60, 60, fill="#fffdf8", outline="#e0d8cc", width=self.px(2))
            if number is None:
                canvas.create_text(31, 30, text="?", fill="#b9b1a5", font=("Consolas", 24))
            else:
                color = self.RED if number in (1, 4) else self.INK
                for column, row in dots[number]:
                    x, y = 16 + column * 15, 16 + row * 15
                    canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline=color)
            canvas.scale("all", 0, 0, self.ui_scale, self.ui_scale)

    def _load_form(self) -> None:
        for category, variables in self.config_vars.items():
            prize = self.game.prizes[category]
            for variable, value in zip(variables, (prize.activity, prize.minutes, prize.stock)):
                variable.set(str(value))
        for key, variable in self.rule_vars.items():
            variable.set(getattr(self.game.rules, key))

    def _refresh_stock(self) -> None:
        for category in CATEGORIES:
            prize = self.game.prizes[category]
            values = (category, f"{self.game.remaining[category]} / {prize.stock}",
                      f"{prize.activity} · 1件" if prize.minutes == 0 else f"{prize.activity} {prize.minutes}分钟")
            if self.stock_tree.exists(category):
                self.stock_tree.item(category, values=values, tags=("negative",) if self.game.remaining[category] < 0 else ())
            else:
                self.stock_tree.insert("", "end", iid=category, values=values,
                                       tags=("negative",) if self.game.remaining[category] < 0 else ())
            left = self.game.remaining[category]
            self.config_remaining[category].set(f"{left} 份" + (f"（超出 {-left}）" if left < 0 else ""))
        self.roll_count_var.set(f"本局已掷 {self.game.roll_count} 次")

    def _refresh_balances(self) -> None:
        selected = self.selected_activity()
        for iid in self.balance_tree.get_children():
            activity = self.balance_tree.item(iid, "values")[0]
            if activity not in self.game.balances or self.game.balances[activity].available_ms <= 0:
                self.balance_tree.delete(iid)
        existing = {self.balance_tree.item(iid, "values")[0]: iid for iid in self.balance_tree.get_children()}
        for activity, balance in self.game.balances.items():
            if balance.available_ms <= 0:
                continue
            values = (activity, format_time(balance.available_ms, ceiling=True))
            if activity in existing:
                self.balance_tree.item(existing[activity], values=values)
            else:
                existing[activity] = self.balance_tree.insert("", "end", values=values)
        if selected in existing:
            self.balance_tree.selection_set(existing[selected])
        elif existing and not self.balance_tree.selection():
            self.balance_tree.selection_set(next(iter(existing.values())))

    def _refresh_items(self) -> None:
        selected = self.selected_claim_reward()
        rows = []
        for name, balance in self.game.item_balances.items():
            if balance.available > 0:
                rows.append(("item:" + name, (name + "（物品）", f"{balance.available}件")))
        for name, balance in self.game.balances.items():
            if balance.available_ms > 0:
                minutes = balance.available_ms // 60000
                rows.append(("time:" + name, (name + "（时间）",
                             f"{minutes}件（{format_time(balance.available_ms, ceiling=True)}）")))
        existing = {identifier for identifier, _values in rows}
        for identifier in self.item_tree.get_children():
            if identifier not in existing:
                self.item_tree.delete(identifier)
        for index, (identifier, values) in enumerate(rows):
            if self.item_tree.exists(identifier):
                self.item_tree.item(identifier, values=values)
                self.item_tree.move(identifier, "", index)
            else:
                self.item_tree.insert("", index, iid=identifier, values=values)
        if selected:
            identifier = selected[0] + ":" + selected[1]
            if identifier in existing:
                self.item_tree.selection_set(identifier)
        if rows and not self.item_tree.selection():
            self.item_tree.selection_set(rows[0][0])
        self._select_item()

    def selected_claim_reward(self):
        selection = self.item_tree.selection()
        if not selection:
            return None
        kind, _separator, name = selection[0].partition(":")
        return kind, name

    def selected_item(self) -> str | None:
        reward = self.selected_claim_reward()
        return reward[1] if reward else None

    def _select_item(self, _event=None) -> None:
        reward = self.selected_claim_reward()
        available = 0
        if reward:
            kind, name = reward
            available = (self.game.item_balances[name].available if kind == "item"
                         else self.game.balances[name].available_ms // 60000)
        self.claim_button.configure(state="normal" if available > 0 else "disabled")
        self.item_hint.set(f"已选择：{reward[1]} · 可直接领取 {available} 件" if reward
                           else "先获得奖励，再选择要直接领取的物品或时间。")

    def claim_items(self) -> None:
        reward = self.selected_claim_reward()
        if not reward:
            messagebox.showinfo("先选择奖励", "请在列表中选择要领取的奖励。", parent=self.root)
            return
        try:
            try:
                count = int(self.claim_count.get().strip())
            except ValueError:
                raise ValueError("领取件数请填写大于零的整数。") from None
            self._settle_timer(notify=False)
            kind, name = reward
            if kind == "item":
                self.game.claim_item(name, count)
            else:
                self.game.withdraw_time(name, count)
        except ValueError as error:
            messagebox.showwarning("无法领取奖励", str(error), parent=self.root)
            return
        if self.game.session is None:
            self.running = False
            self._cancel_tick()
        self._refresh_balances()
        self._refresh_items()
        self._refresh_timer()
        self.status.set(f"已直接领取：{name} {count} {'件' if kind == 'item' else '分钟'}。")
        self._save()


    def _refresh_history(self) -> None:
        self.history_tree.delete(*self.history_tree.get_children())
        for row in reversed(self.game.history):
            self.history_tree.insert("", "end", values=(row["number"], row["time"], " ".join(map(str, row["dice"])),
                                                        row["title"], reward_text(row)))

    def selected_activity(self) -> str | None:
        selection = self.balance_tree.selection()
        return self.balance_tree.item(selection[0], "values")[0] if selection else None

    def _select_reward(self, _event=None) -> None:
        activity = self.selected_activity()
        if activity and not self.game.session:
            available = self.game.balances[activity].available_ms
            self.timer_hint.set(f"已选择：{activity} · 可用 {format_time(available, ceiling=True)}")

    def _show_result(self, row: dict) -> None:
        self.result_title.set(row["title"])
        self.result_detail.set(row["explanation"] + "  " + reward_text(row))

    def random_roll(self) -> None:
        dice = [random.randint(1, 6) for _ in range(6)]
        if self._roll(dice):
            self.manual_dice_var.set("".join(map(str, dice)))

    def manual_roll(self, _event=None) -> str:
        try:
            dice = parse_dice_input(self.manual_dice_var.get())
        except ValueError as error:
            messagebox.showwarning("输入有误", str(error), parent=self.root)
            return "break"
        self.manual_dice_var.set("".join(map(str, dice)))
        self._roll(dice)
        self.dice_input.focus_set()
        self.dice_input.selection_range(0, "end")
        self.dice_input.icursor("end")
        return "break"

    def _roll(self, dice) -> bool:
        if self.tracker:
            self.tracker.refresh_time_displays()
        classify_dice(dice, self.game.rules)
        if self.game.work.available <= 0:
            messagebox.showinfo("暂无博饼次数", "先在工作计时页累积工作时间，或使用顶部按钮手动增加次数。", parent=self.root)
            return False
        row = self.game.award(dice)
        self.game.work.consume_roll()
        self._refresh_chances()
        self._draw_dice(dice)
        self._show_result(row)
        self._refresh_stock()
        self._refresh_balances()
        self._refresh_items()
        if row["granted"] and not self.game.session:
            self.reward_tabs.select(self.item_rewards_tab)
        self.history_tree.insert("", 0, values=(row["number"], row["time"], " ".join(map(str, dice)),
                                               row["title"], reward_text(row)))
        rows = self.history_tree.get_children()
        if len(rows) > 3000:
            self.history_tree.delete(rows[-1])
        self.status.set(f"第 {row['number']} 次：{reward_text(row)}")
        self._save()
        return True

    def _refresh_chances(self) -> None:
        credits = self.game.work
        self.chance_var.set(f"可用博饼：{credits.available} 次" if credits.available >= 0
                            else f"博饼次数待补回：{-credits.available} 次")
        self.credit_detail_var.set(
            f"累计工作：{ActivityTrackerApp.format_activity_duration(credits.work_seconds)}\n"
            f"工作获得 {credits.earned} 次 · 手动增加 {credits.manual_added} 次\n"
            f"累计使用 {credits.spent} 次（跨博饼局统计）")
        self.progress_var.set(
            f"再工作 {format_time(credits.seconds_to_next * 1000)}，获得 1 次机会。"
            + (f"当前还需补回 {-credits.available} 次。" if credits.available < 0 else ""))
        state = "normal" if credits.available > 0 else "disabled"
        self.random_button.configure(state=state)
        self.manual_button.configure(state=state)
        if self.tracker:
            self.tracker.credit_progress.configure(
                value=100 * (1 - credits.seconds_to_next / (credits.minutes_per_roll * 60)))

    def _work_changed(self, current_seconds: int) -> None:
        before = self.game.work.available
        delta = current_seconds - self.work_accounted_seconds
        self.game.work.add_work(delta)
        self.work_accounted_seconds = current_seconds
        self._refresh_chances()
        now = time.monotonic()
        if delta and (self.game.work.available != before or now - self.credit_last_save >= 5):
            self._save()
            self.credit_last_save = now

    def add_chances(self) -> None:
        if self.tracker:
            self.tracker.refresh_time_displays()
        try:
            count = int(self.add_chances_var.get().strip())
            self.game.work.add_manual(count)
        except ValueError as error:
            messagebox.showwarning("次数未增加", f"{error}\n请输入正整数。", parent=self.root)
            return
        self._refresh_chances()
        self.status.set(f"已手动增加 {count} 次博饼机会。")
        self._save()

    def reset_work_credits(self) -> None:
        if not messagebox.askyesno("重设工作兑换统计",
                "将累计兑换工作时长、手动增加次数、累计使用次数和次数欠额清零。\n"
                "保留兑换分钟设置、活动记录、奖品和当前计时。\n"
                "从重设时刻开始重新累计工作，确定重设？", parent=self.root):
            return
        old_work = WorkCredits(**asdict(self.game.work))
        old_accounted = self.work_accounted_seconds
        old_timeline = self.game.activity_timeline
        self.game.work.reset(utc_now())
        self.work_accounted_seconds = 0
        self.tracker.refresh_time_displays()
        if not self._save():
            self.game.work = old_work
            self.work_accounted_seconds = old_accounted
            self.game.activity_timeline = old_timeline
            self.tracker.refresh_time_displays()
            self.status.set("重设未保存，原工作兑换统计已保留。")
            return
        self.tracker._save_session()
        self.credit_last_save = time.monotonic()
        self.status.set("工作兑换统计已重设；之后的工作时间从现在重新累计。")

    def save_work_config(self) -> None:
        self.tracker.refresh_time_displays()
        try:
            value = int(self.tracker.quota_var.get().strip())
            if not 1 <= value <= 100000:
                raise ValueError("每次博饼所需工作分钟必须是 1～100000 的整数。")
        except ValueError as error:
            messagebox.showwarning("设置未保存", f"{error}\n请输入正整数。", parent=self.root)
            return
        self.game.work.minutes_per_roll = value
        self._refresh_chances()
        self.status.set(f"已设置每工作 {value} 分钟获得 1 次博饼机会，并按累计工作时长重新计算。")
        self._save()

    def save_config(self) -> None:
        try:
            prizes = {category: Prize(v[0].get().strip(), int(v[1].get().strip()), int(v[2].get().strip()))
                      for category, v in self.config_vars.items()}
            self.game.configure(prizes, Rules(**{key: variable.get() for key, variable in self.rule_vars.items()}))
        except ValueError as error:
            messagebox.showwarning("设置未保存", f"{error}\n\n分钟和份数必须填写整数。", parent=self.root)
            return
        self._refresh_stock()
        self._load_form()
        self.status.set("奖品设置已保存。已有时间和物品奖励保持原值；之后中奖使用新设置。")
        self._save()

    def restart_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("重新开始博饼")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(bg=self.BG)
        frame = ttk.Frame(dialog, padding=self.px(24))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="重置本局库存、骰子结果和博饼记录，保留奖品设置。\n已获得的时间和物品奖励全部保留。\n工作计时、奖励倒计时和剩余博饼次数继续保持。", justify="left").pack(anchor="w")
        ttk.Label(frame, text="需要扣减奖励时，在「直接领取」中填写份数领取即可。",
                  style="Muted.TLabel").pack(anchor="w", pady=self.px((12, 0)))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=self.px((18, 0)))

        def confirm():
            self.game.restart()
            self.result_title.set("等待掷骰")
            self.result_detail.set("新的一局开始了。所有奖项库存已恢复为设定的初始份数。")
            self.manual_dice_var.set("")
            self._draw_dice([None] * 6)
            self._refresh_stock()
            self._refresh_balances()
            self._refresh_items()
            self.claim_count.set("1")
            self._refresh_history()
            self._refresh_timer()
            self.status.set("新一局已开始。已保留全部奖励，工作计时和博饼次数不受影响。")
            self._save()
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="确认重新开始", style="Accent.TButton", command=confirm).pack(side="right", padx=self.px(10))
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()

    def _refresh_timer(self) -> None:
        session = self.game.session
        self.timer_label.set(timer_digits(session["remaining_ms"]) if session else "00:00:00")
        self.start_button.configure(state="disabled" if session else "normal")
        self.all_button.configure(state="disabled" if session else "normal")
        self.pause_button.configure(state="normal" if session else "disabled", text="暂停" if self.running else "继续")
        self.stop_button.configure(state="normal" if session else "disabled")
        if session:
            self.timer_hint.set(f"{'正在使用' if self.running else '已暂停'}：{session['activity']} · 按实际时间扣除")
        else:
            self.timer_hint.set("选择奖品后开始倒计时；暂停和关闭程序期间不扣时间。")
            self._select_reward()

    def start_timer(self, use_all: bool = False) -> None:
        activity = self.selected_activity()
        if not activity:
            messagebox.showinfo("先选择奖品", "先博到奖励，再在奖励列表中点击要使用的活动。", parent=self.root)
            return
        try:
            if use_all:
                duration = self.game.balances[activity].available_ms
            else:
                minutes = int(self.use_minutes.get().strip())
                if not 1 <= minutes <= 100000:
                    raise ValueError("本次使用分钟必须是 1～100000 的整数。")
                duration = minutes * 60000
            self.game.start_timer(activity, duration)
        except ValueError as error:
            messagebox.showwarning("不能开始倒计时", str(error), parent=self.root)
            return
        self.running = True
        self.last_tick = time.monotonic_ns() // 1000000
        self.last_save = self.last_tick
        self._refresh_timer()
        self._save()
        self._schedule_tick()

    def _schedule_tick(self) -> None:
        if self.running and self.game.session and self.after_id is None:
            self.after_id = self.root.after(200, self._tick)

    def _cancel_tick(self) -> None:
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    def _settle_timer(self, notify: bool = True) -> bool:
        if not self.running or self.game.session is None:
            return False
        activity = self.game.session["activity"]
        now = time.monotonic_ns() // 1000000
        finished = self.game.consume(now - self.last_tick)
        self.last_tick = now
        self._refresh_balances()
        self._refresh_items()
        if finished:
            self.running = False
            self._cancel_tick()
            self._refresh_timer()
            self._save()
            self.status.set(f"{activity}的本次使用时间已结束。")
            if notify:
                self.root.bell()
                messagebox.showinfo("时间到啦", f"「{activity}」的本次倒计时已结束！\n"
                                    "本次使用的时间已从奖励余额中扣除。", parent=self.root)
        return finished

    def _tick(self) -> None:
        self.after_id = None
        if self._settle_timer():
            return
        self._refresh_timer()
        if self.last_tick - self.last_save >= 2000:
            self._save()
            self.last_save = self.last_tick
        self._schedule_tick()

    def toggle_pause(self) -> None:
        if not self.game.session:
            return
        if self.running:
            if self._settle_timer():
                return
            self.running = False
            self._cancel_tick()
        else:
            self.running = True
            self.last_tick = time.monotonic_ns() // 1000000
            self.last_save = self.last_tick
            self._schedule_tick()
        self._refresh_timer()
        self._save()

    def stop_timer(self) -> None:
        if not self.game.session:
            return
        self._settle_timer()
        self.running = False
        self._cancel_tick()
        self.game.session = None
        self._refresh_timer()
        self.status.set("本次使用已结束，未使用的奖励时间仍保留在余额中。")
        self._save()

    def _save(self) -> bool:
        try:
            if self.tracker:
                now = utc_now()
                seconds = self.tracker.timeline.work_seconds(now, self.game.work.reset_at)
                self.game.work.add_work(seconds - self.work_accounted_seconds)
                self.work_accounted_seconds = seconds
                self.game.activity_timeline = self.tracker.timeline.to_dict(now, self.game.work.reset_at)
                self.game.task_options = self.catalog.to_list()
                self._refresh_chances()
            self.store.save(self.game)
        except OSError as error:
            self.status.set(f"自动保存失败：{error}")
            if not self.save_error:
                messagebox.showwarning("自动保存失败", f"{error}\n请检查数据文件夹的写入权限或磁盘空间。", parent=self.root)
            self.save_error = True
            return False
        self.save_error = False
        return True

    def hide_window(self) -> None:
        self.root.iconify()

    def close(self) -> bool:
        if self.closing:
            return False
        self.closing = True
        exit_time = utc_now()
        if self.tracker:
            self.tracker.timeline.finalize(exit_time)
            self.tracker.selected_var.set("未选择活动")
            self.tracker.refresh_time_displays()
        self._settle_timer(notify=False)
        self.running = False
        self._cancel_tick()
        self._refresh_timer()
        # 先保存完整事件；导出或精简保存失败时仍有完整存档可恢复。
        if not self._save():
            self.closing = False
            return False
        if self.tracker:
            tracker = self.tracker
            full = tracker.timeline.to_dict(exit_time, self.game.work.reset_at)
            original_accounted = self.work_accounted_seconds
            try:
                tracker.export_to_ics(events=tracker.events[:-1], exit_time=exit_time)
            except OSError as error:
                self.closing = False
                self.status.set("日历导出失败，完整活动记录已保留；修复后可再次退出。")
                messagebox.showerror("日历导出失败，程序尚未退出", str(error), parent=self.root)
                return False
            last = [dict(tracker.events[-1])] if tracker.events else []
            tracker.timeline.events = last
            tracker.timeline.started_utc = last[0]["start_utc"] if last else exit_time.isoformat()
            # 已导出事件的工作时长留在累计账户中；不能因删掉明细而扣回博饼机会。
            self.work_accounted_seconds = tracker.timeline.work_seconds(exit_time, self.game.work.reset_at)
            if not self._save() or not tracker._save_session():
                tracker.timeline = ActivityTimeline.from_dict(full, self.catalog)
                self.work_accounted_seconds = original_accounted
                self.game.activity_timeline = full
                self._save()
                tracker._save_session()
                self.closing = False
                self.status.set("退出保存失败，已恢复完整事件列表；请修复后再次退出。")
                tracker.refresh_time_displays()
                return False
            tracker.cancel_tick()
        self.store.close()
        self.root.destroy()
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="博饼与工作计时 GUI")
    parser.add_argument("--data", type=Path, default=None,
                        help="指定博饼数据文件；工作会话与日历保存在同一文件夹")
    parser.add_argument("--install-startup", action="store_true", help="安装 Windows 开机启动快捷方式")
    args = parser.parse_args()
    enable_high_dpi()
    root = tk.Tk()
    root.withdraw()
    if args.install_startup:
        try:
            path = install_startup()
            messagebox.showinfo("开机启动已安装", str(path), parent=root)
        except (ValueError, OSError, RuntimeError) as error:
            messagebox.showerror("安装失败", str(error), parent=root)
        finally:
            root.destroy()
        return
    data_path = args.data or Path(__file__).resolve().with_name("bobing_data.json")
    store = Store(data_path)
    try:
        store.acquire()
        # 只读取当前数据位置的存档；空文件夹从默认配置开始。
        game = store.load()
    except (ValueError, OSError) as error:
        messagebox.showerror("无法启动博饼", str(error), parent=root)
        store.close()
        root.destroy()
        return
    try:
        app = BobingApp(root, store, game)
        root.deiconify()
        root.mainloop()
    except (ValueError, OSError) as error:
        messagebox.showerror("无法载入工作记录", str(error), parent=root)
        root.destroy()
    finally:
        store.close()


if __name__ == "__main__":
    main()
