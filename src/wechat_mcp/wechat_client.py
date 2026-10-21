"""对 pyweixin 的薄封装。

设计要点：

1. **延迟导入**：``pyweixin`` 只在真正调用时导入。这样即使当前机器不是
   Windows、或还没有安装依赖，MCP 服务器也能正常启动并返回可读的错误提示，
   而不是在启动阶段直接崩溃。
2. **串行化**：微信是单实例 GUI，UI 自动化不能并发。所有操作共用一把可重入锁。
3. **stdout 保护**：pyweixin 内部存在 ``print()``，在 stdio 传输下会污染
   JSON-RPC 通道，因此调用期间把 ``sys.stdout`` 临时指向 stderr。
4. **可测试**：``framework`` 参数支持注入桩对象，无需真实微信即可验证逻辑。
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import logging
import os
import re
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional, Sequence

from .config import ClientConfig
from .dates import (
    DateFilterResult,
    filter_records_by_date,
    normalize_records,
    parse_target_date,
)

__all__ = [
    "WeChatClient",
    "WeChatMCPError",
    "WeChatUnavailable",
    "WeChatOperationFailed",
    "default_output_dir",
    "load_pyweixin",
    "jsonable",
]

log = logging.getLogger(__name__)

_INSTALL_HINT = (
    "请在 Windows 上安装依赖后重试: pip install \"pywechat127>=1.9.8\"。"
    "pywechat127 内含 pyweixin(微信 4.1.6+ 自动化) 与 pywechat(微信 3.9)。"
)


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #


class WeChatMCPError(RuntimeError):
    """本项目的业务异常基类，可安全回传给模型。"""

    error_type = "WeChatMCPError"

    def __init__(self, message: str, *, hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "error",
            "error_type": self.error_type,
            "message": self.message,
        }
        if self.hint:
            payload["hint"] = self.hint
        return payload


class WeChatUnavailable(WeChatMCPError):
    """运行环境不满足要求（非 Windows、缺少依赖、微信未登录等）。"""

    error_type = "WeChatUnavailable"


class WeChatOperationFailed(WeChatMCPError):
    """微信 UI 自动化执行失败。"""

    error_type = "WeChatOperationFailed"


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


def jsonable(value: Any) -> Any:
    """把任意对象转换成可 JSON 序列化的结构。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    return str(value)


def load_pyweixin() -> SimpleNamespace:
    """导入 pyweixin 并返回其常用入口。

    Raises:
        WeChatUnavailable: 依赖缺失或平台不支持。
    """
    if sys.platform != "win32":
        raise WeChatUnavailable(
            f"当前平台 {sys.platform} 不受支持，pyweixin 只能驱动 Windows 桌面版微信。",
            hint="请在 Windows 10/11 上运行本 MCP 服务器。",
        )
    try:
        import pyweixin  # noqa: F401  仅用于触发导入校验
        from pyweixin import Contacts, Files, Messages, Moments, Tools
        from pyweixin.Config import GlobalConfig
    except Exception as exc:  # pragma: no cover - 依赖缺失时才触发
        raise WeChatUnavailable(
            f"无法导入 pyweixin: {exc}",
            hint=_INSTALL_HINT,
        ) from exc

    _install_chat_history_title_compat(Tools)

    return SimpleNamespace(
        module=pyweixin,
        Contacts=Contacts,
        Files=Files,
        Messages=Messages,
        Moments=Moments,
        Tools=Tools,
        GlobalConfig=GlobalConfig,
    )


_COMPAT_FLAG = "_wechat_mcp_title_compat"


def _install_chat_history_title_compat(Tools: Any) -> None:
    """修正"聊天记录窗口标题引号"不匹配的问题（幂等、仅在失败时触发）。

    背景：PyPI 上的 ``pywechat127 1.9.8`` 在 ``Navigator.open_chat_history`` 中把
    聊天记录窗口标题硬编码成中文弯引号 ``与“好友”``，而微信 **4.1.12+** 的窗口
    标题改用了直引号 ``与"好友"``，导致 ``move_window_to_center`` 找不到窗口并
    抛出 ``found_index is specified as 0, but 0 window/s found``。

    上游已在 git 主干修复（按版本号选择引号），但尚未发布到 PyPI。这里采用最小
    侵入方式：先按原条件查找，失败且判定为引号问题时换另一种引号重试一次。
    这样无论用户装的是 PyPI 版还是 git 版都能工作。
    """
    original = getattr(Tools, "move_window_to_center", None)
    if original is None or getattr(original, _COMPAT_FLAG, False):
        return

    def _swap_quotes(text: str) -> str:
        return text.replace("“", '"').replace("”", '"')

    @functools.wraps(original)
    def move_window_to_center(Window: Any = None, Window_handle: int = 0) -> Any:
        try:
            return original(Window, Window_handle)
        except Exception:
            if not (isinstance(Window, dict) and isinstance(Window.get("title_re"), str)):
                raise
            retry = dict(Window)
            retry["title_re"] = _swap_quotes(Window["title_re"])
            if retry["title_re"] == Window["title_re"]:
                raise
            log.info(
                "聊天记录窗口标题引号不匹配，改用 %r 重试（pyweixin 1.9.8 已知问题）",
                retry["title_re"],
            )
            return original(retry, Window_handle)

    setattr(move_window_to_center, _COMPAT_FLAG, True)
    Tools.move_window_to_center = staticmethod(move_window_to_center)


class _NullWriter:
    def write(self, *_args: Any, **_kwargs: Any) -> int:
        return 0

    def flush(self) -> None:
        pass


@contextlib.contextmanager
def _quiet_stdout() -> Iterator[None]:
    """调用期间把 stdout 指向 stderr，防止 pyweixin 的 print 破坏 stdio 协议。"""
    original = sys.stdout
    target = sys.stderr if sys.stderr is not None else _NullWriter()
    try:
        sys.stdout = target
    except Exception:  # pragma: no cover - 极端环境下跳过
        yield
        return
    try:
        yield
    finally:
        sys.stdout = original


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name)).strip(" .")
    return cleaned or "chat"


def default_output_dir() -> Path:
    """未配置输出目录时，聊天记录的默认落盘位置。"""
    return Path.home() / "wechat-mcp" / "chat_history"


def _contacts_extract_name(item: Any) -> str:
    return str(item).strip()


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #


class WeChatClient:
    """微信自动化客户端。

    所有公开方法都返回 ``dict``（已可 JSON 序列化），异常统一抛
    :class:`WeChatMCPError` 的子类，由服务器层转成结构化错误。
    """

    # 通讯录分区
    CONTACT_KINDS = {
        "friends": "好友",
        "groups": "群聊",
        "official_accounts": "公众号",
        "service_accounts": "服务号",
        "wecom_friends": "企业微信联系人",
    }
    _CONTACT_LIST_METHODS = {
        "friends": "get_friends_info",
        "groups": "get_groups_info",
        "official_accounts": "get_offAcc_info",
        "service_accounts": "get_serAcc_info",
        "wecom_friends": "get_wecom_friends_info",
    }
    _CONTACT_DETAIL_METHODS = {
        "friends": "get_friends_detail",
        "official_accounts": "get_offAcc_detail",
        "service_accounts": "get_serAcc_detail",
        "wecom_friends": "get_wecom_friends_detail",
    }

    def __init__(self, config: Optional[ClientConfig] = None, framework: Any = None) -> None:
        self.config = config or ClientConfig.from_env()
        self._framework = framework
        self._lock = threading.RLock()

    # -- 基础设施 ---------------------------------------------------------- #

    @property
    def framework(self) -> Any:
        if self._framework is None:
            self._framework = load_pyweixin()
        return self._framework

    @contextlib.contextmanager
    def _session(self, *, require_running: bool = True) -> Iterator[Any]:
        """获取独占的微信 UI 会话。"""
        with self._lock, _quiet_stdout():
            framework = self.framework
            if require_running:
                self._ensure_weixin_running(framework)
            yield framework

    def _ensure_weixin_running(self, framework: Any) -> None:
        try:
            running = bool(framework.Tools.is_weixin_running())
        except Exception as exc:  # pragma: no cover - 依赖异常
            raise WeChatOperationFailed(f"检测微信运行状态失败: {exc}") from exc
        if not running:
            raise WeChatUnavailable(
                "未检测到正在运行的微信桌面版进程(Weixin.exe)。",
                hint=(
                    "请先启动并登录微信 4.1.6 及以上版本。"
                    "本服务通过 UI 自动化驱动微信，运行期间请勿手动操作微信窗口。"
                ),
            )

    def _wrap_failure(self, action: str, exc: Exception) -> WeChatMCPError:
        if isinstance(exc, WeChatMCPError):
            return exc
        message = str(exc).strip() or exc.__class__.__name__
        lowered = message.lower()
        hint = None
        if "friend" in lowered or "好友" in message:
            hint = "请确认 to_user 与微信中的备注/昵称完全一致（支持群聊名称）。"
        elif "timeout" in lowered or "超时" in message:
            hint = "UI 响应超时。请确认微信窗口未被最小化，且运行期间没有手动操作微信。"
        else:
            hint = "若微信界面处于弹窗、锁屏或最小化状态，请恢复后重试。"
        return WeChatOperationFailed(f"{action}失败: {message}", hint=hint)

    # -- 聊天记录 ---------------------------------------------------------- #

    def get_chat_history(
        self,
        friend: str,
        target_date: Any,
        *,
        max_messages: Optional[int] = None,
        batch_size: Optional[int] = None,
        save_json: bool = False,
        folder_path: Optional[str] = None,
        search_pages: Optional[int] = None,
        group_members: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """获取某个好友/群聊在指定日期的聊天记录。

        pyweixin 没有"按日期取记录"的方法，只有"向上回溯 N 条"的
        ``dump_chat_history``。这里采用**倍增回溯**：先取少量，若最早一条
        仍晚于目标日期则翻倍重取，直到命中目标日期或触及上限，再按日期过滤。

        Args:
            friend: 好友或群聊的备注/昵称。
            target_date: 目标日期，支持 ``25/3/22``、``2025-03-22``、``昨天`` 等。
            max_messages: 回溯条数上限，默认取配置值（2000）。
            batch_size: 首次回溯条数，默认取配置值（200）。
            save_json: 是否把结果写成 JSON 文件。
            folder_path: 保存目录，默认取配置中的输出目录。
            search_pages: 会话列表滚动查找次数。
            group_members: 群成员列表。传入可显著加快群聊记录解析。
        """
        friend = str(friend).strip()
        if not friend:
            raise ValueError("to_user 不能为空")
        target = parse_target_date(target_date)

        cfg = self.config
        limit = int(max_messages or cfg.max_messages)
        if limit < 1:
            raise ValueError("max_messages 必须为正整数")
        batch = max(1, min(int(batch_size or cfg.initial_batch), limit))

        with self._session() as framework:
            result, fetched, attempts, exhausted = self._dump_until_date(
                framework,
                friend=friend,
                target=target,
                limit=limit,
                batch=batch,
                search_pages=search_pages,
                group_members=group_members,
            )

        records = [
            {
                "index": index,
                "发送者": record.get("消息发送人"),
                "时间": record.get("消息发送时间"),
                "消息": record.get("消息内容"),
                "类型": record.get("消息类型"),
                "时间ISO": record.get("时间ISO"),
            }
            for index, record in enumerate(result.records)
        ]

        payload: dict[str, Any] = {
            "status": "success",
            "to_user": friend,
            "target_date": target.isoformat(),
            "count": len(records),
            "scanned_messages": fetched,
            "records": records,
            "attempts": attempts,
        }

        if not records:
            if result.total == 0:
                payload["note"] = f"未能从「{friend}」读取到任何聊天记录，请确认备注是否正确。"
            elif exhausted:
                payload["note"] = (
                    f"已回溯 {fetched} 条消息仍未到达 {target.isoformat()}，"
                    f"且已达到 max_messages 上限，请调大后重试。"
                )
            elif not result.reached:
                payload["note"] = (
                    f"已回溯 {fetched} 条消息（最早 {result.oldest}），仍未到达 {target.isoformat()}。"
                    f"该会话可回溯的历史可能已到头，可调大 max_messages 后重试。"
                )
            else:
                payload["note"] = f"{target.isoformat()} 当天在「{friend}」的会话中没有聊天记录。"
        elif exhausted:
            payload["note"] = "已达到回溯上限，记录可能不完整，可调大 max_messages。"

        save_target = folder_path or (str(cfg.output_dir) if cfg.output_dir else None)
        if save_json or save_target:
            folder = Path(save_target) if save_target else default_output_dir()
            payload["saved_path"] = self._save_history(records, friend, target, folder)

        return payload

    def _dump_until_date(
        self,
        framework: Any,
        *,
        friend: str,
        target: date,
        limit: int,
        batch: int,
        search_pages: Optional[int],
        group_members: Optional[Sequence[str]],
    ) -> tuple[DateFilterResult, int, int, bool]:
        """倍增回溯直到覆盖目标日期，返回 (过滤结果, 抓取条数, 尝试次数, 是否触顶)。"""
        cfg = self.config
        pages = cfg.search_pages if search_pages is None else int(search_pages)
        members = list(group_members or [])
        attempts = 0
        fetched = 0
        current = batch
        result = DateFilterResult()

        while True:
            attempts += 1
            try:
                raw = framework.Messages.dump_chat_history(
                    friend=friend,
                    number=current,
                    groupMembers=members,
                    is_json=False,
                    save_detail=False,
                    search_pages=pages,
                    is_maximize=cfg.maximize,
                    # 循环内部不关闭微信，否则下一轮倍增需要重新登录
                    close_weixin=False,
                )
            except Exception as exc:
                raise self._wrap_failure(f"读取与「{friend}」的聊天记录", exc) from exc

            records = normalize_records(raw)
            if not records:
                return DateFilterResult(total=0), 0, attempts, False

            fetched = len(records)
            result = filter_records_by_date(records, target)

            if result.matched:
                return result, fetched, attempts, False
            # 已回溯到目标日期之前仍无命中：当天确实没有消息
            if result.reached:
                return result, fetched, attempts, False
            # 返回条数少于请求条数，说明该会话可回溯的历史已经到头，继续倍增无意义
            if len(records) < current:
                return result, fetched, attempts, False
            if current >= limit:
                return result, fetched, attempts, True

            current = min(current * 2, limit)
            log.info("未覆盖 %s，回溯条数提升到 %s 重试", target, current)

    def _save_history(
        self,
        records: list[dict],
        friend: str,
        target: date,
        folder: Path,
    ) -> str:
        folder = folder.expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"与{_sanitize_filename(friend)}的{target.isoformat()}聊天记录.json"
        path = folder / filename
        import json

        path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("聊天记录已保存: %s", path)
        return str(path)

    # -- 消息发送 ---------------------------------------------------------- #

    def send_messages(
        self,
        friend: str,
        messages: Sequence[str],
        *,
        delay: Optional[float] = None,
        search_pages: Optional[int] = None,
        at_members: Optional[Sequence[str]] = None,
        at_all: bool = False,
    ) -> dict[str, Any]:
        """向单个好友/群聊发送一条或多条消息。"""
        friend = str(friend).strip()
        if not friend:
            raise ValueError("to_user 不能为空")
        texts = [str(message).strip() for message in messages if str(message).strip()]
        if not texts:
            raise ValueError("message(s) 不能为空")

        cfg = self.config
        pages = cfg.search_pages if search_pages is None else int(search_pages)
        send_delay = float(delay) if delay is not None else cfg.send_delay
        if send_delay <= 0:
            send_delay = cfg.send_delay

        with self._session() as framework:
            try:
                framework.Messages.send_messages_to_friend(
                    friend=friend,
                    messages=texts,
                    at_members=list(at_members or []),
                    at_all=bool(at_all),
                    search_pages=pages,
                    clear=True,
                    send_delay=send_delay,
                    is_maximize=cfg.maximize,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure(f"向「{friend}」发送消息", exc) from exc

        return {
            "status": "success",
            "to_user": friend,
            "sent_count": len(texts),
            "messages": texts,
            "warnings": self._message_warnings(texts),
        }

    def send_to_friends(
        self,
        friends: Sequence[str],
        per_friend_messages: Sequence[Sequence[str]],
        *,
        delay: Optional[float] = None,
    ) -> dict[str, Any]:
        """向多个好友分别发送消息（一次打开微信，依次发送）。"""
        names = [str(name).strip() for name in friends if str(name).strip()]
        if not names:
            raise ValueError("to_user 不能为空")
        if len(names) != len(per_friend_messages):
            raise ValueError("好友数量与消息组数量不一致")

        cfg = self.config
        send_delay = float(delay) if delay is not None else cfg.send_delay
        if send_delay <= 0:
            send_delay = cfg.send_delay

        with self._session() as framework:
            try:
                framework.Messages.send_messages_to_friends(
                    friends=names,
                    messages=[list(group) for group in per_friend_messages],
                    clear=True,
                    send_delay=send_delay,
                    is_maximize=cfg.maximize,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure("群发消息", exc) from exc

        return {
            "status": "success",
            "to_user": names,
            "sent_count": sum(len(group) for group in per_friend_messages),
            "detail": [
                {"to_user": name, "messages": list(group)}
                for name, group in zip(names, per_friend_messages)
            ],
            "warnings": self._message_warnings(
                [message for group in per_friend_messages for message in group]
            ),
        }

    @staticmethod
    def _message_warnings(messages: Sequence[str]) -> list[str]:
        warnings: list[str] = []
        long_messages = [message for message in messages if len(message) > 2000]
        if long_messages:
            warnings.append(
                f"{len(long_messages)} 条消息超过微信 2000 字上限，"
                f"pyweixin 会将其自动转成 txt 文件发送。"
            )
        return warnings

    def send_files(
        self,
        friend: str,
        files: Sequence[str],
        *,
        messages: Optional[Sequence[str]] = None,
        messages_first: bool = False,
        delay: Optional[float] = None,
    ) -> dict[str, Any]:
        """向单个好友/群聊发送本地文件（可附带文本消息）。"""
        friend = str(friend).strip()
        if not friend:
            raise ValueError("to_user 不能为空")

        paths: list[str] = []
        missing: list[str] = []
        oversized: list[str] = []
        for raw in files:
            candidate = Path(str(raw)).expanduser()
            if not candidate.exists():
                missing.append(str(candidate))
                continue
            if candidate.is_dir():
                missing.append(f"{candidate} (是目录)")
                continue
            if candidate.stat().st_size >= 1024 ** 3:
                oversized.append(str(candidate))
                continue
            paths.append(str(candidate.resolve()))
        if missing:
            raise ValueError("以下文件不存在或不是文件: " + "、".join(missing))
        if oversized:
            raise ValueError("微信单文件上限 1GB，以下文件过大: " + "、".join(oversized))
        if not paths:
            raise ValueError("files 不能为空")

        cfg = self.config
        texts = [str(message).strip() for message in (messages or []) if str(message).strip()]
        send_delay = float(delay) if delay is not None else cfg.send_delay

        with self._session() as framework:
            try:
                framework.Files.send_files_to_friend(
                    friend=friend,
                    files=paths,
                    with_messages=bool(texts),
                    messages=texts,
                    messages_first=bool(messages_first),
                    send_delay=send_delay,
                    clear=True,
                    is_maximize=cfg.maximize,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure(f"向「{friend}」发送文件", exc) from exc

        return {
            "status": "success",
            "to_user": friend,
            "files": paths,
            "file_count": len(paths),
            "messages": texts,
        }

    def export_chat_files(
        self,
        friend: str,
        number: int,
        *,
        target_folder: Optional[str] = None,
    ) -> dict[str, Any]:
        """导出与某个好友的聊天文件（仅能导出未过期的文件）。"""
        friend = str(friend).strip()
        if not friend:
            raise ValueError("to_user 不能为空")
        if number < 1:
            raise ValueError("number 必须为正整数")

        cfg = self.config
        folder = target_folder or (str(cfg.output_dir) if cfg.output_dir else None)
        if folder:
            folder = str(Path(folder).expanduser().resolve())
            Path(folder).mkdir(parents=True, exist_ok=True)

        with self._session() as framework:
            try:
                framework.Files.save_chatfiles(
                    friend=friend,
                    number=int(number),
                    target_folder=folder,
                    is_maximize=cfg.maximize,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure(f"导出与「{friend}」的聊天文件", exc) from exc

        saved: list[str] = []
        if folder:
            saved = sorted(str(path) for path in Path(folder).glob("*") if path.is_file())

        return {
            "status": "success",
            "to_user": friend,
            "target_folder": folder,
            "files": saved,
        }

    # -- 通讯录 ------------------------------------------------------------ #

    def get_contacts(
        self,
        kind: str = "friends",
        *,
        detail: bool = False,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """获取通讯录中的好友、群聊、公众号等。"""
        kind = (kind or "friends").strip().lower()
        aliases = {
            "friend": "friends",
            "好友": "friends",
            "group": "groups",
            "群聊": "groups",
            "offacc": "official_accounts",
            "公众号": "official_accounts",
            "seracc": "service_accounts",
            "服务号": "service_accounts",
            "wecom": "wecom_friends",
            "企业微信": "wecom_friends",
            "all": "all",
            "全部": "all",
        }
        kind = aliases.get(kind, kind)
        if kind != "all" and kind not in self.CONTACT_KINDS:
            raise ValueError(
                f"不支持的 kind: {kind}，可选值为 "
                f"{', '.join(list(self.CONTACT_KINDS) + ['all'])}"
            )

        cfg = self.config
        targets = list(self.CONTACT_KINDS) if kind == "all" else [kind]
        payload: dict[str, Any] = {"status": "success", "kind": kind, "detail": detail}
        sections: dict[str, Any] = {}
        notes: list[str] = []

        with self._session() as framework:
            for name in targets:
                try:
                    if detail and name in self._CONTACT_DETAIL_METHODS:
                        method = getattr(framework.Contacts, self._CONTACT_DETAIL_METHODS[name])
                        data = method(is_maximize=cfg.maximize, close_weixin=False, is_json=False)
                        items = normalize_records(data) if isinstance(data, str) else list(data or [])
                    else:
                        method = getattr(framework.Contacts, self._CONTACT_LIST_METHODS[name])
                        data = method(is_maximize=cfg.maximize, close_weixin=False)
                        items = [_contacts_extract_name(item) for item in (data or [])]
                        if detail:
                            notes.append(f"{self.CONTACT_KINDS[name]}暂不支持获取详情，已返回名称列表。")
                except Exception as exc:
                    sections[name] = {"error": str(exc)}
                    continue

                items = [jsonable(item) for item in items]
                if limit is not None and limit >= 0:
                    items = items[: int(limit)]
                sections[name] = items

        payload["data"] = sections if kind == "all" else sections.get(kind, [])
        if isinstance(payload["data"], dict):
            payload["counts"] = {
                name: (len(value) if isinstance(value, list) else 0)
                for name, value in payload["data"].items()
            }
        else:
            payload["count"] = len(payload["data"])
        if notes:
            payload["notes"] = sorted(set(notes))
        return payload

    def get_group_members(
        self,
        group: str,
        *,
        search_pages: Optional[int] = None,
    ) -> dict[str, Any]:
        """获取群聊成员昵称列表。"""
        group = str(group).strip()
        if not group:
            raise ValueError("group 不能为空")

        cfg = self.config
        pages = cfg.search_pages if search_pages is None else int(search_pages)
        with self._session() as framework:
            try:
                members = framework.Contacts.get_groupMembers_info(
                    group=group,
                    is_maximize=cfg.maximize,
                    search_pages=pages,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure(f"获取群「{group}」成员", exc) from exc

        members = [str(member) for member in (members or [])]
        return {
            "status": "success",
            "group": group,
            "count": len(members),
            "members": members,
        }

    # -- 检索与会话 -------------------------------------------------------- #

    def search_chat_history(
        self,
        keyword: str,
        *,
        number: Optional[int] = None,
        with_content: bool = False,
    ) -> dict[str, Any]:
        """按关键字在微信全局搜索聊天记录。"""
        keyword = str(keyword).strip()
        if not keyword:
            raise ValueError("keyword 不能为空")

        cfg = self.config
        with self._session() as framework:
            try:
                found = framework.Messages.search_chat_history(
                    keyword=keyword,
                    number=int(number) if number else None,
                    with_content=bool(with_content),
                    is_maximize=cfg.maximize,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure(f"搜索聊天记录「{keyword}」", exc) from exc

        data = jsonable(found or {})
        return {
            "status": "success",
            "keyword": keyword,
            "with_content": bool(with_content),
            "count": len(data) if isinstance(data, dict) else 0,
            "data": data,
        }

    def get_recent_sessions(
        self,
        recent: str = "Today",
        *,
        chat_only: bool = False,
    ) -> dict[str, Any]:
        """获取会话列表中最近活跃的会话。"""
        recent = self._normalize_recent(recent, {"Today", "Yesterday", "Week", "Month", "Year"})
        cfg = self.config
        with self._session() as framework:
            try:
                sessions = framework.Messages.dump_recent_sessions(
                    recent=recent,
                    chat_only=bool(chat_only),
                    is_maximize=cfg.maximize,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure("获取最近会话", exc) from exc

        items = []
        for session in sessions or []:
            parts = list(session) if isinstance(session, (list, tuple)) else [session]
            entry = {"name": str(parts[0]).strip() if parts else ""}
            if len(parts) > 1:
                entry["last_time"] = str(parts[1]).strip()
            if len(parts) > 2:
                entry["last_message"] = str(parts[2]).strip()
            if len(parts) > 3:
                entry["extra"] = [jsonable(part) for part in parts[3:]]
            items.append(entry)

        return {
            "status": "success",
            "recent": recent,
            "count": len(items),
            "sessions": items,
        }

    def check_new_messages(self) -> dict[str, Any]:
        """遍历会话列表，返回有未读消息的会话与其最新消息。"""
        cfg = self.config
        with self._session() as framework:
            try:
                data = framework.Messages.check_new_messages(
                    is_maximize=cfg.maximize,
                    search_pages=cfg.search_pages,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure("检查新消息", exc) from exc

        data = jsonable(data or {})
        return {
            "status": "success",
            "count": len(data) if isinstance(data, dict) else 0,
            "sessions": data,
        }

    # -- 朋友圈 ------------------------------------------------------------ #

    def get_moments(
        self,
        recent: str = "Today",
        *,
        number: Optional[int] = None,
        with_name: bool = False,
    ) -> dict[str, Any]:
        """获取最近的朋友圈内容。"""
        recent = self._normalize_recent(recent, {"Today", "Yesterday", "Week", "Month"})
        cfg = self.config
        with self._session() as framework:
            try:
                posts = framework.Moments.dump_recent_posts(
                    recent=recent,
                    number=int(number) if number else None,
                    with_name=bool(with_name),
                    save_detail=False,
                    is_maximize=cfg.maximize,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure("获取朋友圈内容", exc) from exc

        posts = [jsonable(post) for post in (posts or [])]
        return {
            "status": "success",
            "recent": recent,
            "count": len(posts),
            "posts": posts,
        }

    def post_moments(
        self,
        text: str = "",
        *,
        medias: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """发布朋友圈（文本 + 图片/视频）。"""
        text = (text or "").strip()
        paths: list[str] = []
        missing: list[str] = []
        for raw in medias or []:
            candidate = Path(str(raw)).expanduser()
            if not candidate.is_file():
                missing.append(str(candidate))
                continue
            if candidate.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".mp4", ".mov"}:
                missing.append(f"{candidate} (不支持的格式)")
                continue
            paths.append(str(candidate.resolve()))
        if missing:
            raise ValueError("以下素材不可用: " + "、".join(missing))
        if not text and not paths:
            raise ValueError("text 与 medias 不能同时为空")

        cfg = self.config
        with self._session() as framework:
            try:
                framework.Moments.post_moments(
                    text=text,
                    medias=paths,
                    is_maximize=cfg.maximize,
                    close_weixin=cfg.close_wechat,
                )
            except Exception as exc:
                raise self._wrap_failure("发布朋友圈", exc) from exc

        return {
            "status": "success",
            "text": text,
            "medias": paths,
        }

    # -- 环境信息 ---------------------------------------------------------- #

    def get_weixin_info(self) -> dict[str, Any]:
        """返回微信运行环境信息（版本、wxid、数据目录等）。"""
        framework = self.framework
        info: dict[str, Any] = {"status": "success"}
        try:
            info["running"] = bool(framework.Tools.is_weixin_running())
        except Exception as exc:  # pragma: no cover
            info["running"] = None
            info["running_error"] = str(exc)
        try:
            info["about"] = jsonable(framework.Tools.about_weixin())
        except Exception as exc:
            info["about_error"] = str(exc)
        info["config"] = {
            "output_dir": str(self.config.output_dir) if self.config.output_dir else None,
            "maximize": self.config.maximize,
            "close_wechat": self.config.close_wechat,
            "search_pages": self.config.search_pages,
            "send_delay": self.config.send_delay,
            "max_messages": self.config.max_messages,
        }
        info["pid"] = os.getpid()
        return info

    # -- 内部工具 ---------------------------------------------------------- #

    @staticmethod
    def _normalize_recent(value: str, allowed: set[str]) -> str:
        mapping = {
            "today": "Today",
            "今天": "Today",
            "yesterday": "Yesterday",
            "昨天": "Yesterday",
            "week": "Week",
            "本周": "Week",
            "近一周": "Week",
            "month": "Month",
            "本月": "Month",
            "近一月": "Month",
            "year": "Year",
            "本年": "Year",
        }
        key = str(value or "").strip()
        normalized = mapping.get(key.lower(), mapping.get(key, key if key in allowed else None))
        if normalized not in allowed:
            raise ValueError(f"recent 取值必须是 {sorted(allowed)} 之一，收到: {value!r}")
        return normalized
