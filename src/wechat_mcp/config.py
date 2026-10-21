"""运行期配置。

所有配置项都可以通过环境变量覆盖，便于在 MCP 客户端里用 ``env`` 段传参，
不需要改代码。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional

__all__ = ["ClientConfig", "ENV_PREFIX"]

ENV_PREFIX = "WECHAT_MCP_"


def _env_str(name: str) -> Optional[str]:
    value = os.environ.get(ENV_PREFIX + name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on", "是", "开启"}


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class ClientConfig:
    """微信自动化运行参数。

    Attributes:
        output_dir: 聊天记录等产物的默认输出目录；为空时不落盘。
        close_wechat: 每次操作结束后是否关闭微信。默认 ``False``，
            保持微信常驻，避免频繁登录和后续调用失败。
        maximize: 操作期间是否最大化微信窗口。窗口越大，UI 遍历越稳定。
        search_pages: 在会话列表中滚动查找好友的次数（0 表示直接用顶部搜索框）。
        send_delay: 单条消息发送间隔（秒）。
        max_messages: 单次按日期取记录时，最多回溯的消息条数上限。
        initial_batch: 首次回溯的条数，未命中则按倍数递增。
        ui_retry: UI 操作失败后的重试次数（仅用于幂等的读取类操作）。
    """

    output_dir: Optional[Path] = None
    close_wechat: bool = False
    maximize: bool = False
    search_pages: int = 5
    send_delay: float = 0.2
    max_messages: int = 2000
    initial_batch: int = 200
    ui_retry: int = 1

    def __post_init__(self) -> None:
        if self.output_dir is not None and not isinstance(self.output_dir, Path):
            self.output_dir = Path(self.output_dir).expanduser()
        if self.search_pages < 0:
            self.search_pages = 0
        if self.send_delay <= 0:
            self.send_delay = 0.2
        if self.max_messages < 1:
            self.max_messages = 1
        if self.initial_batch < 1:
            self.initial_batch = 1
        if self.initial_batch > self.max_messages:
            self.initial_batch = self.max_messages

    @classmethod
    def from_env(cls, **overrides: Any) -> "ClientConfig":
        """从环境变量构造配置，``overrides`` 中的非空值优先。"""
        raw_dir = _env_str("OUTPUT_DIR") or _env_str("FOLDER_PATH")
        config = cls(
            output_dir=Path(raw_dir) if raw_dir else None,
            close_wechat=_env_bool("CLOSE_WECHAT", False),
            maximize=_env_bool("MAXIMIZE", False),
            search_pages=_env_int("SEARCH_PAGES", 5),
            send_delay=_env_float("SEND_DELAY", 0.2),
            max_messages=_env_int("MAX_MESSAGES", 2000),
            initial_batch=_env_int("INITIAL_BATCH", 200),
            ui_retry=_env_int("UI_RETRY", 1),
        )
        for key, value in overrides.items():
            if value is None:
                continue
            if key not in {item.name for item in fields(cls)}:
                raise TypeError(f"未知配置项: {key}")
            setattr(config, key, value)
        # 重新执行一次校验与归一化
        config.__post_init__()
        return config

    def ensure_output_dir(self) -> Optional[Path]:
        """确保输出目录存在，返回其绝对路径。"""
        if self.output_dir is None:
            return None
        path = self.output_dir.expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
