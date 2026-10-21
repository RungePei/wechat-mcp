"""MCP 服务器：把 WeChatClient 暴露为 MCP 工具。

工具命名与原 ``mcp-server-wechat`` 完全一致（``wechat_get_chat_history`` 等），
因此可以直接替换使用；额外工具覆盖 pyweixin 4.1+ 的更多能力。

同时兼容 MCP Python SDK 的两个大版本：

* ``mcp >= 2``：``mcp.server.mcpserver.MCPServer``
* ``mcp >= 1.9``：``mcp.server.fastmcp.FastMCP``
"""

import argparse
import functools
import inspect
import json
import logging
import os
import sys
from typing import Annotated, Any, Callable, Optional, Sequence, Union

from pydantic import Field

from . import __version__
from .args import as_messages, as_str_list, as_text, build_per_friend_messages
from .config import ClientConfig
from .wechat_client import WeChatClient, WeChatMCPError, jsonable

try:  # MCP Python SDK v2
    from mcp.server.mcpserver import MCPServer as _ServerClass

    _SDK_MAJOR = 2
except ImportError:  # pragma: no cover - v1 分支
    from mcp.server.fastmcp import FastMCP as _ServerClass  # type: ignore[no-redef]

    _SDK_MAJOR = 1

__all__ = ["build_server", "main", "mcp"]

log = logging.getLogger(__name__)

SERVER_NAME = "wechat-mcp"

INSTRUCTIONS = """\
本服务器通过 Windows UI 自动化(pyweixin)驱动**桌面版微信 4.1.6+**，可以读取聊天记录、
发送消息/文件、查询通讯录、查看朋友圈。

使用前提：
1. 运行在 Windows 10/11；
2. 微信桌面版已启动并登录；
3. 调用期间不要手动操作微信窗口，否则会打断自动化流程。

典型用法：
- 想知道某人某天聊了什么 -> wechat_get_chat_history(to_user, target_date)
- 发消息 -> wechat_send_message / wechat_send_multiple_messages / wechat_send_to_multiple_friends
- 找联系方式 -> wechat_get_contacts / wechat_get_group_members
- 掌握最近动态 -> wechat_check_new_messages / wechat_get_recent_sessions

日期格式支持 25/3/22、2025-03-22、昨天、前天等。
所有工具返回 JSON 文本，其中 status 为 "error" 时 message/hint 字段会说明原因。
"""


# --------------------------------------------------------------------------- #
# 参数注解：把说明写进 JSON Schema，模型才能看到每个参数的含义
# --------------------------------------------------------------------------- #

_FRIEND_DESC = "好友或群聊的备注/昵称，需与微信中显示的名称完全一致"

ToUser = Annotated[str, Field(description=_FRIEND_DESC)]
ToUserList = Annotated[
    Union[str, list[str]],
    Field(description="好友/群聊名称列表；可传数组，也可传逗号、顿号或换行分隔的字符串"),
]
TargetDate = Annotated[
    str,
    Field(
        description=(
            "目标日期。支持 YY/M/D（如 25/3/22，与旧版一致），"
            "也支持 2025-03-22、20250322、3/22、今天/昨天/前天"
        )
    ),
]
MaxMessages = Annotated[
    Optional[int],
    Field(description="最多向上回溯的消息条数，默认 2000；目标日期越久远需要调大"),
]
SaveJson = Annotated[bool, Field(description="是否把结果保存为 JSON 文件")]
FolderPath = Annotated[str, Field(description="保存目录；留空则使用服务器配置的输出目录")]
SearchPages = Annotated[
    Optional[int],
    Field(description="在会话列表中滚动查找好友的次数；0 表示直接使用顶部搜索框"),
]
GroupMembers = Annotated[
    Optional[list[str]],
    Field(description="群成员昵称列表；传入可显著加快群聊记录中发送人的解析"),
]
Message = Annotated[str, Field(description="要发送的文本消息；超过 2000 字时微信会转成 txt 文件发送")]
MessagesArg = Annotated[
    Union[str, list[str]],
    Field(
        description=(
            "消息列表。可传数组，也可传用换行或分号分隔的文本；"
            "消息内部的逗号会被保留，不会被拆分"
        )
    ),
]
MessageForMany = Annotated[
    Union[str, list[str]],
    Field(
        description=(
            "要发送的内容。传字符串=同一条消息发给所有人；"
            "传数组且长度与 to_user 相同时=分别发送；"
            "传换行文本且行数与好友数相同时=按行分别发送"
        )
    ),
]
Delay = Annotated[Optional[float], Field(description="发送间隔（秒），默认 0.2")]
FilesArg = Annotated[
    Union[str, list[str]],
    Field(description="本地文件的绝对路径列表，也支持换行分隔的字符串；单个文件需小于 1GB"),
]
OptionalMessages = Annotated[
    Optional[Union[str, list[str]]],
    Field(description="随文件一起发送的文本消息"),
]
MessagesFirst = Annotated[bool, Field(description="为 True 时先发消息再发文件；默认先发文件")]
ContactsKind = Annotated[
    str,
    Field(
        description=(
            "联系人类别：friends(好友) | groups(群聊) | official_accounts(公众号) | "
            "service_accounts(服务号) | wecom_friends(企业微信) | all(全部)"
        )
    ),
]
ContactsDetail = Annotated[bool, Field(description="是否获取详细信息（昵称、备注等）；群聊暂不支持详情")]
Limit = Annotated[Optional[int], Field(description="每个分区最多返回多少条，默认不限")]
Group = Annotated[str, Field(description="群聊名称")]
Keyword = Annotated[str, Field(description="搜索关键字")]
NumberLimit = Annotated[Optional[int], Field(description="最多获取多少条，默认不限")]
WithContent = Annotated[bool, Field(description="是否返回具体聊天内容；默认只返回命中数量")]
RecentSessions = Annotated[
    str,
    Field(description="Today | Yesterday | Week | Month | Year，也支持“今天”“本周”等中文"),
]
ChatOnly = Annotated[bool, Field(description="是否只返回有消息的会话")]
RecentMoments = Annotated[str, Field(description="Today | Yesterday | Week | Month")]
WithName = Annotated[bool, Field(description="是否同时获取发布者昵称（较慢）")]
MomentsText = Annotated[str, Field(description="朋友圈文案")]
MomentsMedias = Annotated[
    Optional[Union[str, list[str]]],
    Field(description="图片/视频的本地绝对路径列表，支持 jpg/png/gif/bmp/mp4/mov"),
]
ExportNumber = Annotated[int, Field(description="导出的文件数量上限，默认 20")]
TargetFolder = Annotated[str, Field(description="保存目录；留空则使用配置的输出目录")]


# --------------------------------------------------------------------------- #
# 结果封装
# --------------------------------------------------------------------------- #


def _ok(payload: dict[str, Any]) -> str:
    payload.setdefault("status", "success")
    return json.dumps(jsonable(payload), ensure_ascii=False, indent=2)


def _error(exc: BaseException) -> str:
    if isinstance(exc, WeChatMCPError):
        payload = exc.to_dict()
        log.warning("工具执行失败: %s", exc.message)
    else:
        payload = {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
        }
        log.exception("工具执行异常")
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _guard(func: Callable[..., dict[str, Any]]) -> Callable[..., str]:
    """捕获所有异常并转成结构化 JSON，避免把堆栈直接抛给模型。

    注意：``functools.wraps`` 会把内层函数的返回注解(``dict``)带到包装函数上，
    MCP SDK 会据此生成 outputSchema 并用它校验**字符串**返回值，从而抛
    ValidationError。因此这里显式把返回注解改写为 ``str``。
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return _ok(func(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 - 统一出口
            return _error(exc)

    wrapper.__annotations__ = {**getattr(func, "__annotations__", {}), "return": str}
    try:
        wrapper.__signature__ = inspect.signature(func).replace(return_annotation=str)
    except (TypeError, ValueError):  # pragma: no cover - 防御性
        pass
    return wrapper


def _positive_or_none(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# --------------------------------------------------------------------------- #
# 服务器构建
# --------------------------------------------------------------------------- #


def build_server(
    config: Optional[ClientConfig] = None,
    *,
    client: Optional[WeChatClient] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> Any:
    """构造并返回 MCP 服务器实例。

    Args:
        config: 运行配置，默认从环境变量读取。
        client: 可注入自定义客户端（测试用）。
        host: HTTP 传输(``sse`` / ``streamable-http``)监听地址。
        port: HTTP 传输监听端口。
    """
    resolved_config = config or ClientConfig.from_env()
    wechat = client or WeChatClient(resolved_config)

    server_kwargs: dict[str, Any] = {"name": SERVER_NAME, "instructions": INSTRUCTIONS}
    if _SDK_MAJOR >= 2:
        server_kwargs["version"] = __version__
    else:  # v1 的 host/port 只能通过构造参数传入
        server_kwargs.update(host=host, port=port)

    mcp = _ServerClass(**server_kwargs)

    # ---------------- 核心工具：与原 mcp-server-wechat 完全同名 ---------------- #

    @mcp.tool(name="wechat_get_chat_history")
    @_guard
    def wechat_get_chat_history(
        to_user: ToUser,
        target_date: TargetDate,
        max_messages: MaxMessages = None,
        save_json: SaveJson = False,
        folder_path: FolderPath = "",
        search_pages: SearchPages = None,
        group_members: GroupMembers = None,
    ) -> dict[str, Any]:
        """获取指定好友或群聊在某一天的聊天记录。

        自动向上回溯历史消息直到覆盖目标日期，再按日期过滤，返回按时间正序排列的记录。
        目标日期较久远时可调大 max_messages。
        """
        return wechat.get_chat_history(
            friend=as_text(to_user, "to_user"),
            target_date=target_date,
            max_messages=_positive_or_none(max_messages),
            save_json=bool(save_json),
            folder_path=folder_path or None,
            search_pages=search_pages,
            group_members=group_members,
        )

    @mcp.tool(name="wechat_send_message")
    @_guard
    def wechat_send_message(
        to_user: ToUser,
        message: Message,
        delay: Delay = None,
        search_pages: SearchPages = None,
    ) -> dict[str, Any]:
        """向单个微信好友或群聊发送一条消息。"""
        return wechat.send_messages(
            as_text(to_user, "to_user"),
            [as_text(message, "message")],
            delay=delay,
            search_pages=search_pages,
        )

    @mcp.tool(name="wechat_send_multiple_messages")
    @_guard
    def wechat_send_multiple_messages(
        to_user: ToUser,
        messages: MessagesArg,
        delay: Delay = None,
        search_pages: SearchPages = None,
    ) -> dict[str, Any]:
        """向单个微信好友或群聊连续发送多条消息。"""
        return wechat.send_messages(
            as_text(to_user, "to_user"),
            as_messages(messages, "messages"),
            delay=delay,
            search_pages=search_pages,
        )

    @mcp.tool(name="wechat_send_to_multiple_friends")
    @_guard
    def wechat_send_to_multiple_friends(
        to_user: ToUserList,
        message: MessageForMany,
        delay: Delay = None,
    ) -> dict[str, Any]:
        """向多个微信好友或群聊发送消息（支持统一群发或分别发送）。"""
        friends = as_str_list(to_user, "to_user")
        per_friend = build_per_friend_messages(friends, message)
        return wechat.send_to_friends(friends, per_friend, delay=delay)

    # ---------------- 扩展工具：pyweixin 4.1+ 的更多能力 ---------------- #

    @mcp.tool(name="wechat_send_files")
    @_guard
    def wechat_send_files(
        to_user: ToUser,
        files: FilesArg,
        messages: OptionalMessages = None,
        messages_first: MessagesFirst = False,
        delay: Delay = None,
    ) -> dict[str, Any]:
        """向单个好友或群聊发送本地文件（单个文件需小于 1GB）。"""
        paths = as_str_list(files, "files")
        texts = as_messages(messages, "messages") if messages else None
        return wechat.send_files(
            as_text(to_user, "to_user"),
            paths,
            messages=texts,
            messages_first=bool(messages_first),
            delay=delay,
        )

    @mcp.tool(name="wechat_get_contacts")
    @_guard
    def wechat_get_contacts(
        kind: ContactsKind = "friends",
        detail: ContactsDetail = False,
        limit: Limit = None,
    ) -> dict[str, Any]:
        """获取通讯录中的好友、群聊、公众号、服务号或企业微信联系人。"""
        return wechat.get_contacts(
            kind=kind,
            detail=bool(detail),
            limit=_positive_or_none(limit),
        )

    @mcp.tool(name="wechat_get_group_members")
    @_guard
    def wechat_get_group_members(group: Group, search_pages: SearchPages = None) -> dict[str, Any]:
        """获取指定群聊的成员昵称列表。"""
        return wechat.get_group_members(
            as_text(group, "group"),
            search_pages=search_pages,
        )

    @mcp.tool(name="wechat_search_chat_history")
    @_guard
    def wechat_search_chat_history(
        keyword: Keyword,
        number: NumberLimit = None,
        with_content: WithContent = False,
    ) -> dict[str, Any]:
        """用关键字在微信中全局搜索聊天记录。"""
        return wechat.search_chat_history(
            as_text(keyword, "keyword"),
            number=_positive_or_none(number),
            with_content=bool(with_content),
        )

    @mcp.tool(name="wechat_get_recent_sessions")
    @_guard
    def wechat_get_recent_sessions(
        recent: RecentSessions = "Today",
        chat_only: ChatOnly = False,
    ) -> dict[str, Any]:
        """获取会话列表中的最近会话（名称、最后一条消息与时间）。"""
        return wechat.get_recent_sessions(recent=recent, chat_only=bool(chat_only))

    @mcp.tool(name="wechat_check_new_messages")
    @_guard
    def wechat_check_new_messages() -> dict[str, Any]:
        """检查会话列表中的新消息，返回有未读的会话及其最新内容。"""
        return wechat.check_new_messages()

    @mcp.tool(name="wechat_get_moments")
    @_guard
    def wechat_get_moments(
        recent: RecentMoments = "Today",
        number: NumberLimit = None,
        with_name: WithName = False,
    ) -> dict[str, Any]:
        """获取最近的朋友圈内容。"""
        return wechat.get_moments(
            recent=recent,
            number=_positive_or_none(number),
            with_name=bool(with_name),
        )

    @mcp.tool(name="wechat_post_moments")
    @_guard
    def wechat_post_moments(
        text: MomentsText = "",
        medias: MomentsMedias = None,
    ) -> dict[str, Any]:
        """发布一条朋友圈（文案 + 图片/视频）。"""
        paths = as_str_list(medias, "medias") if medias else []
        return wechat.post_moments(text or "", medias=paths)

    @mcp.tool(name="wechat_export_chat_files")
    @_guard
    def wechat_export_chat_files(
        to_user: ToUser,
        number: ExportNumber = 20,
        target_folder: TargetFolder = "",
    ) -> dict[str, Any]:
        """导出与某个好友的聊天文件到本地目录（过期未下载的文件无法导出）。"""
        return wechat.export_chat_files(
            as_text(to_user, "to_user"),
            _positive_or_none(number) or 20,
            target_folder=target_folder or None,
        )

    @mcp.tool(name="wechat_get_weixin_info")
    @_guard
    def wechat_get_weixin_info() -> dict[str, Any]:
        """获取当前微信的版本、wxid、数据目录及本服务器配置，用于排障。"""
        return wechat.get_weixin_info()

    @mcp.resource("wechat://server/info")
    def wechat_server_info() -> str:
        """本服务器的版本与运行配置。"""
        return json.dumps(
            {
                "name": SERVER_NAME,
                "version": __version__,
                "sdk_major": _SDK_MAJOR,
                "python": sys.version.split()[0],
                "config": jsonable(wechat.config.__dict__),
            },
            ensure_ascii=False,
            indent=2,
        )

    return mcp


# --------------------------------------------------------------------------- #
# 命令行入口
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wechat-mcp",
        description="基于 pyweixin 的微信 4.1+ MCP 服务器",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=os.environ.get("WECHAT_MCP_TRANSPORT", "stdio"),
        help="传输方式，默认 stdio",
    )
    parser.add_argument("--host", default=os.environ.get("WECHAT_MCP_HOST", "127.0.0.1"), help="HTTP 传输监听地址")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WECHAT_MCP_PORT", "8000")),
        help="HTTP 传输监听端口",
    )
    parser.add_argument(
        "--folder-path",
        "--output-dir",
        dest="folder_path",
        default=os.environ.get("WECHAT_MCP_OUTPUT_DIR"),
        help="聊天记录等产物的默认保存目录",
    )
    parser.add_argument("--maximize", action="store_true", help="操作期间最大化微信窗口")
    parser.add_argument("--close-wechat", action="store_true", help="每次操作结束后关闭微信")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("WECHAT_MCP_LOG_LEVEL", "INFO"),
        help="日志级别，默认 INFO（日志输出到 stderr，不影响 stdio 协议）",
    )
    parser.add_argument("--version", action="version", version=f"{SERVER_NAME} {__version__}")
    return parser.parse_args(list(argv) if argv is not None else None)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _run_server(mcp: Any, *, transport: str, host: str, port: int) -> None:
    if _SDK_MAJOR >= 2:
        if transport == "stdio":
            mcp.run(transport="stdio")
        else:
            mcp.run(transport=transport, host=host, port=port)
        return

    settings = getattr(mcp, "settings", None)  # v1: host/port 来自 settings
    if settings is not None:
        settings.host = host
        settings.port = port
    mcp.run(transport=transport)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_args(argv)
    _configure_logging(args.log_level)

    config = ClientConfig.from_env(
        output_dir=args.folder_path or None,
        maximize=True if args.maximize else None,
        close_wechat=True if args.close_wechat else None,
    )
    mcp = build_server(config, host=args.host, port=args.port)

    if args.transport != "stdio":
        log.info("以 %s 传输启动: http://%s:%s", args.transport, args.host, args.port)
    _run_server(mcp, transport=args.transport, host=args.host, port=args.port)


mcp = build_server()


if __name__ == "__main__":  # pragma: no cover
    main()
