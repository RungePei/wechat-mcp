"""微信时间文本解析与按日期过滤。

pyweixin 的 ``Messages.dump_chat_history`` 返回的 ``消息发送时间`` 字段是微信
界面上直接读到的原始文本，同一个会话内可能出现以下几种形态：

* ``2026年3月22日 14:30``  —— 往年的消息
* ``3月22日 14:30``        —— 今年的消息
* ``昨天 14:30``           —— 昨天的消息
* ``星期三 14:30``         —— 最近一周内的消息
* ``14:30``                —— 今天的消息
* ``2026-03-22 14:30``     —— 英文版微信

本模块只做两件事：把这些文本还原成 ``datetime``，以及按目标日期筛选记录。
保持纯粹（无 pyweixin 依赖、无 I/O），因此可以独立做单元测试。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional, Sequence

__all__ = [
    "DateFilterResult",
    "filter_records_by_date",
    "normalize_records",
    "parse_target_date",
    "parse_wechat_datetime",
    "timestamp_of",
]


# --------------------------------------------------------------------------- #
# 正则
# --------------------------------------------------------------------------- #

# 2026年3月22日 14:30 / 2026-03-22 14:30 / 2026/3/22 14:30 / 2026.3.22 14:30
_RE_FULL = re.compile(
    r"(?P<y>\d{4})\s*[-/年.]\s*(?P<m>\d{1,2})\s*[-/月.]\s*(?P<d>\d{1,2})\s*日?"
    r"\s*(?P<H>\d{1,2})\s*[:：]\s*(?P<M>\d{2})"
)

# 3月22日 14:30
_RE_MONTH_DAY = re.compile(
    r"(?<![\d年])(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日?"
    r"\s*(?P<H>\d{1,2})\s*[:：]\s*(?P<M>\d{2})"
)

# 14:30
_RE_HOUR_MINUTE = re.compile(r"(?<!\d)(?P<H>\d{1,2})\s*[:：]\s*(?P<M>\d{2})(?!\d)")

_RE_YESTERDAY = re.compile(r"昨天|昨日|yesterday", re.IGNORECASE)
_RE_DAY_BEFORE_YESTERDAY = re.compile(r"前天")
_RE_WEEKDAY = re.compile(r"(?:星期|周)[一二三四五六日天]")
_RE_MERIDIEM = re.compile(r"(凌晨|早上|上午|中午|下午|傍晚|晚上|am|pm)", re.IGNORECASE)

_WEEKDAY_MAP = {
    "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3,
    "星期五": 4, "星期六": 5, "星期日": 6, "星期天": 6,
    "周一": 0, "周二": 1, "周三": 2, "周四": 3,
    "周五": 4, "周六": 5, "周日": 6, "周天": 6,
}

# 微信在无法取到时间戳时（红包/转账）会写入的占位文本
_UNKNOWN_TIMESTAMP_MARKERS = ("无法获取时间戳", "红包或转账")

# 全角数字与分隔符 -> 半角（"３／２２" 这类输入也能识别）
_FULLWIDTH_TRANS = str.maketrans(
    "０１２３４５６７８９／－．：　", "0123456789/-.: "
)


def _apply_meridiem(hour: int, text: str) -> int:
    """处理 上午/下午/AM/PM 等 12 小时制标记。"""
    match = _RE_MERIDIEM.search(text or "")
    if not match:
        return hour
    marker = match.group(1).lower()
    if marker in {"下午", "傍晚", "晚上", "pm"} and hour < 12:
        return hour + 12
    if marker in {"凌晨", "早上", "上午", "am"} and hour == 12:
        return 0
    return hour


def _safe_datetime(year: int, month: int, day: int, hour: int, minute: int) -> Optional[datetime]:
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def parse_wechat_datetime(value: Any, now: Optional[datetime] = None) -> Optional[datetime]:
    """把微信聊天记录里的时间文本还原成 ``datetime``，失败返回 ``None``。

    Args:
        value: 原始时间文本，例如 ``"3月22日 14:30"``。
        now: 参照时间，默认取当前时间。测试时可注入以避免依赖真实时钟。

    Returns:
        还原后的时间；无法解析（包括红包/转账占位文本）时返回 ``None``。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for marker in _UNKNOWN_TIMESTAMP_MARKERS:
        if marker in text:
            return None

    now = now or datetime.now()
    today = now.date()

    # 1) 完整日期 + 时间
    match = _RE_FULL.search(text)
    if match:
        hour = _apply_meridiem(int(match.group("H")), text)
        return _safe_datetime(
            int(match.group("y")), int(match.group("m")), int(match.group("d")),
            hour, int(match.group("M")),
        )

    # 2) 月日 + 时间（默认今年；若算出来比今天还晚，说明是去年的消息）
    match = _RE_MONTH_DAY.search(text)
    if match:
        hour = _apply_meridiem(int(match.group("H")), text)
        parsed = _safe_datetime(
            today.year, int(match.group("m")), int(match.group("d")),
            hour, int(match.group("M")),
        )
        if parsed and parsed.date() > today:
            parsed = _safe_datetime(
                today.year - 1, int(match.group("m")), int(match.group("d")),
                hour, int(match.group("M")),
            )
        return parsed

    # 3) 相对日期 + 时间：今天 / 昨天 / 前天 / 星期X
    hour_minute = _RE_HOUR_MINUTE.search(text)
    hour = int(hour_minute.group("H")) if hour_minute else None
    minute = int(hour_minute.group("M")) if hour_minute else None

    base_day: Optional[date] = None
    if _RE_DAY_BEFORE_YESTERDAY.search(text):
        base_day = today - timedelta(days=2)
    elif _RE_YESTERDAY.search(text):
        base_day = today - timedelta(days=1)
    else:
        weekday_match = _RE_WEEKDAY.search(text)
        if weekday_match:
            target = _WEEKDAY_MAP[weekday_match.group(0)]
            days_back = (now.weekday() - target) % 7
            # 微信对今天的消息只显示时间，出现"星期X"一定是上周的
            base_day = today - timedelta(days=days_back or 7)

    if base_day is not None and hour is not None:
        hour = _apply_meridiem(hour, text)
        return _safe_datetime(base_day.year, base_day.month, base_day.day, hour, minute or 0)

    # 4) 只有时间（今天的消息）
    if hour is not None:
        hour = _apply_meridiem(hour, text)
        candidate = _safe_datetime(today.year, today.month, today.day, hour, minute or 0)
        # 微信对今天的消息只显示时间。只有当时间明显晚于"现在"（例如 00:05 读到
        # 23:50 的消息）才判为昨天；十几分钟的量级视为时钟漂移，不改日期。
        if candidate and candidate - now > timedelta(hours=12):
            candidate -= timedelta(days=1)
        return candidate

    return None


def parse_target_date(value: Any, today: Optional[date] = None) -> date:
    """解析工具入参 ``target_date``，返回目标日期。

    支持的写法（大小写不敏感）：

    * ``25/3/22``、``25-3-22``、``25.3.22``  —— 两位年份，与原 mcp-server-wechat 一致
    * ``2025-03-22``、``2025/3/22``、``20250322``
    * ``3/22``、``3-22``                      —— 省略年份，按今年处理
    * ``今天`` / ``昨天`` / ``前天`` / ``today`` / ``yesterday``

    Raises:
        ValueError: 格式无法识别或日期不存在（如 2 月 30 日）。
    """
    if value is None:
        raise ValueError("target_date 不能为空，应形如 25/3/22 或 2025-03-22")
    text = str(value).strip().translate(_FULLWIDTH_TRANS)
    if not text:
        raise ValueError("target_date 不能为空，应形如 25/3/22 或 2025-03-22")

    today = today or date.today()
    lowered = text.lower()

    if "前天" in text:
        return today - timedelta(days=2)
    if _RE_YESTERDAY.search(text):
        return today - timedelta(days=1)
    if "今天" in text or "今日" in text or lowered in {"today"}:
        return today

    # 2025-03-22 / 2025/3/22 / 2025.3.22
    match = re.fullmatch(r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})", text)
    if match:
        return _build_date(int(match.group(1)), int(match.group(2)), int(match.group(3)), value)

    # 20250322
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if match:
        return _build_date(int(match.group(1)), int(match.group(2)), int(match.group(3)), value)

    # 25/3/22 —— 两位年份，与旧版 mcp-server-wechat 的 YY/M/D 保持一致
    match = re.fullmatch(r"(\d{2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})", text)
    if match:
        return _build_date(2000 + int(match.group(1)), int(match.group(2)), int(match.group(3)), value)

    # 3/22 —— 省略年份
    match = re.fullmatch(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})", text)
    if match:
        return _build_date(today.year, int(match.group(1)), int(match.group(2)), value)

    raise ValueError(
        f"无法识别的日期格式: {value!r}。支持的写法: 25/3/22、2025-03-22、20250322、3/22、今天/昨天/前天"
    )


def _build_date(year: int, month: int, day: int, raw: Any) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"日期不存在: {raw!r} ({year}-{month}-{day})") from exc


def normalize_records(raw: Any) -> list[dict]:
    """把 pyweixin 的返回值统一成 ``list[dict]``。

    ``Messages.dump_chat_history`` 在聊天记录为空时会返回 ``([], [], [])``
    （三元组而非空列表），这里一并兼容，避免上层出现 ``AttributeError``。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, (list, tuple)):
        return []

    records: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            records.append(dict(item))
        elif isinstance(item, (list, tuple)) and len(item) >= 3 and all(
            isinstance(part, str) for part in item[:3]
        ):
            # 兼容 (发送人, 内容, 类型, 时间) 这类元组返回
            record = {"消息发送人": item[0], "消息内容": item[1], "消息类型": item[2]}
            if len(item) > 3:
                record["消息发送时间"] = item[3]
            records.append(record)
    return records


def timestamp_of(record: dict) -> Any:
    """取出记录中的时间字段（不同版本字段名可能是 ``消息发送时间`` / ``时间``）。"""
    for key in ("消息发送时间", "时间", "timestamp", "time"):
        if key in record:
            return record.get(key)
    return None


@dataclass
class DateFilterResult:
    """按日期过滤的结果。

    Attributes:
        records: 命中目标日期的记录，已按时间正序排列。
        total: 本次输入记录总数。
        oldest: 输入记录中最早的可解析日期。
        newest: 输入记录中最晚的可解析日期。
        reached: 输入记录是否已回溯到目标日期或更早（用于判断"是否还需要继续往前翻"）。
        parse_failures: 时间无法解析的记录数。
    """

    records: list[dict] = field(default_factory=list)
    total: int = 0
    oldest: Optional[date] = None
    newest: Optional[date] = None
    reached: bool = False
    parse_failures: int = 0

    @property
    def matched(self) -> int:
        return len(self.records)


def filter_records_by_date(
    records: Sequence[dict],
    target: date,
    now: Optional[datetime] = None,
) -> DateFilterResult:
    """在 ``records`` 中筛选出 ``target`` 当天的消息。

    pyweixin 返回的记录顺序取决于微信界面（通常是新→旧），本函数会先根据
    时间戳判断实际方向并统一成正序，再按日期过滤。
    """
    now = now or datetime.now()
    items = list(records or [])
    if not items:
        return DateFilterResult(total=0, reached=False)

    stamps = [parse_wechat_datetime(timestamp_of(record), now) for record in items]
    known = [stamp for stamp in stamps if stamp is not None]

    result = DateFilterResult(
        total=len(items),
        parse_failures=len(items) - len(known),
    )
    if known:
        result.oldest = min(stamp.date() for stamp in known)
        result.newest = max(stamp.date() for stamp in known)
        result.reached = result.oldest <= target

    # 判断界面顺序：取第一对/最后一对可解析时间戳比较
    descending = _is_descending(stamps)
    ordered_items = list(reversed(items)) if descending else items

    matched: list[dict] = []
    for record in ordered_items:
        stamp = parse_wechat_datetime(timestamp_of(record), now)
        if stamp is not None and stamp.date() == target:
            enriched = dict(record)
            enriched["_parsed"] = stamp
            matched.append(enriched)

    # 无法解析时间的记录（红包/转账）不会进入 matched，但也不应打乱顺序；
    # 这里按解析时间做稳定排序，时间相同的保持原界面顺序。
    matched.sort(key=lambda record: record["_parsed"])

    for record in matched:
        record["时间ISO"] = record.pop("_parsed").isoformat(sep=" ", timespec="minutes")

    result.records = matched
    return result


def _is_descending(stamps: Sequence[Optional[datetime]]) -> bool:
    """根据时间戳序列判断界面是否按"新→旧"排列。"""
    known = [(index, stamp) for index, stamp in enumerate(stamps) if stamp is not None]
    if len(known) < 2:
        return False
    first = known[0][1]
    last = known[-1][1]
    if first != last:
        return first > last
    # 全部同一天时，比较相邻两条
    for (_, prev), (_, nxt) in zip(known, known[1:]):
        if prev != nxt:
            return prev > nxt
    return False
