"""MCP 协议层测试：真实 stdio 子进程 + 官方 SDK 客户端。

覆盖三件事：
1. 服务器能通过 stdio 正常握手，且 stdout 没有被日志/print 污染；
2. 工具清单与参数 schema 符合预期（含与原 mcp-server-wechat 同名的 4 个核心工具）；
3. 工具调用返回结构化 JSON，错误参数不会导致服务器崩溃。
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

mcp_client = pytest.importorskip("mcp.client.session")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent
FAKE_SERVER = TESTS_DIR / "fake_server.py"

CORE_TOOLS = {
    "wechat_get_chat_history",
    "wechat_send_message",
    "wechat_send_multiple_messages",
    "wechat_send_to_multiple_friends",
}

EXTRA_TOOLS = {
    "wechat_send_files",
    "wechat_get_contacts",
    "wechat_get_group_members",
    "wechat_search_chat_history",
    "wechat_get_recent_sessions",
    "wechat_check_new_messages",
    "wechat_get_moments",
    "wechat_post_moments",
    "wechat_export_chat_files",
    "wechat_get_weixin_info",
}


def _params() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=[str(FAKE_SERVER)])


def _attr(obj, *names):
    """兼容 MCP SDK v1(camelCase) 与 v2(snake_case) 的字段命名。"""
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"{obj!r} 缺少字段: {names}")


def _input_schema(tool) -> dict:
    return _attr(tool, "inputSchema", "input_schema")


def _is_error(result) -> bool:
    return bool(_attr(result, "isError", "is_error"))


async def _with_session(action):
    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await action(session)


def test_tools_are_registered_with_expected_schema():
    async def action(session):
        return await session.list_tools()

    result = asyncio.run(_with_session(action))
    tools = {tool.name: tool for tool in result.tools}

    assert set(tools) == CORE_TOOLS | EXTRA_TOOLS

    history = _input_schema(tools["wechat_get_chat_history"])
    assert set(history["required"]) == {"to_user", "target_date"}
    assert "target_date" in history["properties"]
    assert "YY/M/D" in history["properties"]["target_date"]["description"]

    per_friend = _input_schema(tools["wechat_send_to_multiple_friends"])
    assert set(per_friend["required"]) == {"to_user", "message"}

    multi = _input_schema(tools["wechat_send_multiple_messages"])
    assert set(multi["required"]) == {"to_user", "messages"}


def test_get_chat_history_over_protocol():
    async def action(session):
        return await session.call_tool(
            "wechat_get_chat_history",
            {"to_user": "张三", "target_date": "昨天"},
        )

    result = asyncio.run(_with_session(action))
    assert not _is_error(result)
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "success"
    assert payload["count"] == 3
    assert all(record["时间"].startswith("昨天") for record in payload["records"])


def test_send_multiple_messages_over_protocol():
    async def action(session):
        return await session.call_tool(
            "wechat_send_multiple_messages",
            {"to_user": "张三", "messages": ["第一条", "第二条"]},
        )

    result = asyncio.run(_with_session(action))
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "success"
    assert payload["sent_count"] == 2


def test_invalid_arguments_return_error_without_crashing():
    async def action(session):
        bad = await session.call_tool("wechat_get_chat_history", {"to_user": "张三"})
        good = await session.call_tool("wechat_get_weixin_info", {})
        return bad, good

    bad, good = asyncio.run(_with_session(action))
    assert _is_error(bad)  # 缺少 target_date，由 SDK 参数校验拦截
    payload = json.loads(good.content[0].text)
    assert payload["running"] is True


def test_resource_exposes_server_info():
    async def action(session):
        return await session.read_resource("wechat://server/info")

    result = asyncio.run(_with_session(action))
    text = result.contents[0].text
    payload = json.loads(text)
    assert payload["name"] == "wechat-mcp"
    assert "version" in payload


def test_stdout_carries_only_json_rpc():
    """stdio 传输下 stdout 只能是 JSON-RPC；pyweixin 的 print 必须被重定向到 stderr。"""

    async def action(session):
        return await session.call_tool("wechat_send_message", {"to_user": "张三", "message": "hello"})

    # 只要能完整跑完并拿到合法结果，就说明协议通道未被污染
    result = asyncio.run(_with_session(action))
    assert json.loads(result.content[0].text)["status"] == "success"
