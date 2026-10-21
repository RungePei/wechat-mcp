"""工具入参归一化。

MCP 客户端的模型经常把数组参数写成字符串（``"张三,李四"``），也会把多段
消息写成换行文本。本模块负责把这些宽松输入收敛成确定的结构，同时避免
"消息里带逗号就被拆成两条" 这类误伤。
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

__all__ = [
    "as_text",
    "as_str_list",
    "as_messages",
    "build_per_friend_messages",
]

# 用于拆分"好友列表"的分隔符：备注/昵称里几乎不可能出现这些字符
_NAME_SEPARATORS = (",", "，", ";", "；", "、", "\n", "|")
# 用于拆分"多条消息"的分隔符：逗号不参与，避免误伤正文里的逗号
_MESSAGE_SEPARATORS = ("\n", ";", "；")


def as_text(value: Any, field: str) -> str:
    """校验必填文本参数。"""
    if value is None:
        raise ValueError(f"缺少必要参数: {field}")
    text = str(value).strip()
    if not text:
        raise ValueError(f"参数 {field} 不能为空")
    return text


def _flatten(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for element in value:
            if element is None:
                continue
            if isinstance(element, (list, tuple)):
                items.extend(str(part) for part in element if part is not None)
            else:
                items.append(str(element))
        return items
    return [str(value)]


def _try_json_array(value: str) -> Optional[list[str]]:
    stripped = value.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, list):
        return [str(item) for item in parsed if item is not None]
    return None


def as_str_list(value: Any, field: str, *, separators: Sequence[str] = _NAME_SEPARATORS) -> list[str]:
    """把 ``list[str] | str`` 归一化成去空白的字符串列表。

    字符串输入支持 JSON 数组、逗号/分号/顿号/换行分隔。
    """
    if value is None:
        raise ValueError(f"缺少必要参数: {field}")

    if isinstance(value, str):
        parsed = _try_json_array(value)
        raw_items = parsed if parsed is not None else _split_by(value, separators)
    else:
        raw_items = _flatten(value)

    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)

    if not items:
        raise ValueError(f"参数 {field} 不能为空")
    return items


def _split_by(value: str, separators: Sequence[str]) -> list[str]:
    parts = [value]
    for separator in separators:
        next_parts: list[str] = []
        for part in parts:
            next_parts.extend(part.split(separator))
        parts = next_parts
    return parts


def as_messages(value: Any, field: str = "messages") -> list[str]:
    """把消息参数归一化成 ``list[str]``，保留消息内部的逗号。

    支持：数组 / JSON 数组字符串 / 换行或分号分隔的文本 / 单条消息。
    """
    if value is None:
        raise ValueError(f"缺少必要参数: {field}")

    if isinstance(value, str):
        parsed = _try_json_array(value)
        raw_items = parsed if parsed is not None else _split_by(value, _MESSAGE_SEPARATORS)
    else:
        raw_items = _flatten(value)

    messages = [str(item).strip() for item in raw_items]
    messages = [message for message in messages if message]
    if not messages:
        raise ValueError(f"参数 {field} 不能为空")
    return messages


def build_per_friend_messages(friends: Sequence[str], message: Any) -> list[list[str]]:
    """构造 ``Messages.send_messages_to_friends`` 需要的 ``list[list[str]]``。

    规则（与原 mcp-server-wechat 的语义保持一致，但更严格）：

    * ``message`` 为字符串：同一条消息发给所有好友；
    * ``message`` 为换行文本且行数正好等于好友数：按行分发给对应好友；
    * ``message`` 为数组：长度需等于好友数（长度为 1 时视为群发）。

    Raises:
        ValueError: 数组长度与好友数量不一致。
    """
    if not friends:
        raise ValueError("好友列表不能为空")

    if isinstance(message, str):
        parsed = _try_json_array(message)
        if parsed is not None:
            message = parsed
        else:
            lines = [line.strip() for line in message.splitlines() if line.strip()]
            if len(friends) > 1 and len(lines) == len(friends):
                return [[line] for line in lines]
            text = message.strip()
            if not text:
                raise ValueError("参数 message 不能为空")
            return [[text] for _ in friends]

    if isinstance(message, (list, tuple)):
        items = [str(item).strip() for item in message]
        if not items or not any(items):
            raise ValueError("参数 message 不能为空")
        if len(items) == 1:
            return [[items[0]] for _ in friends]
        if len(items) != len(friends):
            raise ValueError(
                f"message 数量({len(items)})与 to_user 数量({len(friends)})不一致。"
                f"若要给所有人发同一条消息，请传单个字符串；若要分别发送，请保证数量一致。"
            )
        return [[item] for item in items]

    raise ValueError("参数 message 只支持字符串或字符串数组")
