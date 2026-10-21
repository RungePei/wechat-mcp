"""WeChatClient 的行为测试（使用 fake pyweixin，不依赖真实微信）。"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from wechat_mcp.args import build_per_friend_messages
from wechat_mcp.config import ClientConfig
from wechat_mcp.wechat_client import (
    WeChatClient,
    WeChatUnavailable,
    _install_chat_history_title_compat,
    jsonable,
    load_pyweixin,
)
from fake_wechat import FakeFramework, wechat_label


def make_client(tmp_path: Path | None = None, **config_kwargs) -> tuple[WeChatClient, FakeFramework]:
    framework = FakeFramework()
    config = ClientConfig.from_env(output_dir=tmp_path, **config_kwargs)
    return WeChatClient(config, framework=framework), framework


def fixed_history() -> tuple[list[dict], "datetime.date"]:
    """每天 10 条、共 4 天的记录，返回 (记录, 目标日期=前天)。"""
    now = datetime.now()
    records: list[dict] = []
    for index in range(40):
        day_offset = index // 10
        stamp = now - timedelta(days=day_offset)
        if day_offset == 0:
            stamp = now.replace(second=0, microsecond=0)
        label = wechat_label(stamp, now)
        records.append(
            {
                "消息发送人": "张三" if index % 2 else "我",
                "消息内容": f"第{day_offset}天-{index % 10}",
                "消息类型": "文本",
                "消息发送时间": label,
            }
        )
    return records, (now - timedelta(days=2)).date()


class TestGetChatHistory:
    def test_doubles_batch_until_target_date_is_covered(self):
        client, framework = make_client()
        records, target = fixed_history()
        framework.Messages.history = records

        payload = client.get_chat_history("张三", target.isoformat(), batch_size=10, max_messages=2000)

        # 10 -> 20 -> 40：只有回溯 40 条才能覆盖到前天
        assert [call["number"] for call in framework.Messages.calls] == [10, 20, 40]
        assert payload["count"] == 10
        assert all(record["时间ISO"].startswith(target.isoformat()) for record in payload["records"])
        # 结果按时间正序
        assert [record["index"] for record in payload["records"]] == list(range(10))
        # 循环内部不应关闭微信，否则下一轮倍增需要重新登录
        assert all(call["close_weixin"] is False for call in framework.Messages.calls)

    def test_returns_note_when_day_has_no_messages(self):
        client, framework = make_client()
        records, target = fixed_history()
        framework.Messages.history = records
        empty_day = (datetime.now() - timedelta(days=2)).replace(year=2020).date()

        payload = client.get_chat_history("张三", empty_day.isoformat(), batch_size=10)

        assert payload["count"] == 0
        assert "没有聊天记录" in payload["note"] or "仍未到达" in payload["note"]

    def test_empty_history_is_handled(self):
        client, framework = make_client()
        framework.Messages.empty_fallback = True

        payload = client.get_chat_history("张三", "昨天")

        assert payload["status"] == "success"
        assert payload["count"] == 0
        assert "未能" in payload["note"]

    def test_respects_max_messages(self):
        client, framework = make_client()
        records, target = fixed_history()
        framework.Messages.history = records
        ancient = (datetime.now() - timedelta(days=400)).date()

        payload = client.get_chat_history("张三", ancient.isoformat(), batch_size=10, max_messages=20)

        assert [call["number"] for call in framework.Messages.calls] == [10, 20]
        assert "max_messages" in payload["note"]

    def test_stops_when_history_is_shorter_than_requested(self):
        client, framework = make_client()
        records, _ = fixed_history()
        framework.Messages.history = records[:12]  # 只有 12 条历史

        payload = client.get_chat_history("张三", "2020-01-01", batch_size=50)

        # 一次就取到了全部 12 条（少于请求的 50 条），不该再翻倍重试
        assert [call["number"] for call in framework.Messages.calls] == [50]
        assert payload["count"] == 0

    def test_saves_json_when_requested(self, tmp_path):
        client, framework = make_client(tmp_path)
        records, target = fixed_history()
        framework.Messages.history = records

        payload = client.get_chat_history("张三/客户", target.isoformat(), batch_size=40, save_json=True)

        saved = Path(payload["saved_path"])
        assert saved.exists()
        assert saved.parent == tmp_path.resolve()
        assert "/" not in saved.name  # 文件名已清洗
        data = json.loads(saved.read_text(encoding="utf-8"))
        assert len(data) == payload["count"]

    def test_save_json_falls_back_to_default_dir(self, tmp_path, monkeypatch):
        client, framework = make_client()
        records, target = fixed_history()
        framework.Messages.history = records
        monkeypatch.setattr("wechat_mcp.wechat_client.default_output_dir", lambda: tmp_path)

        payload = client.get_chat_history("张三", target.isoformat(), batch_size=40, save_json=True)

        assert Path(payload["saved_path"]).parent == tmp_path

    def test_uses_configured_output_dir_without_flag(self, tmp_path):
        client, framework = make_client(tmp_path)
        records, target = fixed_history()
        framework.Messages.history = records

        payload = client.get_chat_history("张三", target.isoformat(), batch_size=40)

        # 配置了输出目录时会自动落盘，与旧版 --folder-path 的行为一致
        assert Path(payload["saved_path"]).exists()


class TestSendMessages:
    def test_send_single_message(self):
        client, framework = make_client()
        payload = client.send_messages("张三", ["你好"])

        assert payload["sent_count"] == 1
        call = framework.Messages.sent[0]
        assert call["friend"] == "张三"
        assert call["messages"] == ["你好"]
        assert call["close_weixin"] is False

    def test_long_message_produces_warning(self):
        client, _ = make_client()
        payload = client.send_messages("张三", ["字" * 2001])
        assert payload["warnings"] and "2000" in payload["warnings"][0]

    def test_empty_message_raises(self):
        client, _ = make_client()
        with pytest.raises(ValueError):
            client.send_messages("张三", ["   "])

    def test_send_to_multiple_friends(self):
        client, framework = make_client()
        friends = ["张三", "李四"]
        per_friend = build_per_friend_messages(friends, "统一通知")

        payload = client.send_to_friends(friends, per_friend)

        assert payload["sent_count"] == 2
        assert framework.Messages.batch_sent[0]["friends"] == friends
        assert framework.Messages.batch_sent[0]["messages"] == [["统一通知"], ["统一通知"]]


class TestSendFiles:
    def test_rejects_missing_file(self, tmp_path):
        client, _ = make_client()
        with pytest.raises(ValueError) as exc:
            client.send_files("张三", [str(tmp_path / "不存在.txt")])
        assert "不存在" in str(exc.value)

    def test_sends_existing_file(self, tmp_path):
        path = tmp_path / "报告.txt"
        path.write_text("hi", encoding="utf-8")
        client, framework = make_client()

        payload = client.send_files("张三", [str(path)], messages=["文件来了"])

        assert payload["file_count"] == 1
        call = framework.Files.sent[0]
        assert call["with_messages"] is True
        assert call["messages"] == ["文件来了"]


class TestEnvironmentGuards:
    def test_weixin_not_running_raises(self):
        client, framework = make_client()
        framework.Tools.running = False
        with pytest.raises(WeChatUnavailable) as exc:
            client.send_messages("张三", ["你好"])
        assert "Weixin.exe" in str(exc.value)

    def test_stdout_is_restored_after_call(self):
        client, _ = make_client()
        before = sys.stdout
        client.send_messages("张三", ["你好"])
        assert sys.stdout is before


class TestMiscTools:
    def test_contacts_all(self):
        client, framework = make_client()
        payload = client.get_contacts("all")
        assert payload["counts"]["friends"] == 2
        assert payload["counts"]["groups"] == 1

    def test_contacts_detail_for_friends(self):
        client, _ = make_client()
        payload = client.get_contacts("friends", detail=True)
        assert payload["data"][0]["备注"] == "客户张三"

    def test_group_members(self):
        client, _ = make_client()
        payload = client.get_group_members("项目群")
        assert payload["count"] == 3

    def test_recent_sessions_mapping(self):
        client, _ = make_client()
        payload = client.get_recent_sessions("今天")
        assert payload["recent"] == "Today"
        assert payload["sessions"][0] == {
            "name": "张三",
            "last_time": "10:30",
            "last_message": "在吗",
        }

    def test_invalid_recent_raises(self):
        client, _ = make_client()
        with pytest.raises(ValueError):
            client.get_recent_sessions("去年")

    def test_weixin_info(self):
        client, _ = make_client()
        payload = client.get_weixin_info()
        assert payload["running"] is True
        assert payload["about"]["版本"] == "4.1.9.30"


class TestPlatformGuard:
    """pyweixin 依赖 Win32 API，非 Windows 平台必须给出明确错误而不是崩溃。"""

    @pytest.mark.parametrize("platform", ["darwin", "linux"])
    def test_non_windows_raises_clear_error(self, monkeypatch, platform):
        monkeypatch.setattr(sys, "platform", platform)
        with pytest.raises(WeChatUnavailable) as exc:
            load_pyweixin()
        assert platform in str(exc.value)
        assert "Windows" in (exc.value.hint or "")


class TestJsonable:
    def test_datetime_and_tuple(self):
        assert jsonable({"a": datetime(2026, 1, 2, 3, 4), "b": (1, 2)}) == {
            "a": "2026-01-02T03:04:00",
            "b": [1, 2],
        }


class TestTitleQuoteCompat:
    """pyweixin 1.9.8 用弯引号匹配聊天记录窗口标题，4.1.12+ 需要直引号。"""

    def _make_tools(self):
        calls: list[str] = []

        class Tools:
            @staticmethod
            def move_window_to_center(Window=None, Window_handle=0):
                calls.append(Window["title_re"])
                if '"' in Window["title_re"]:
                    return "found"
                raise RuntimeError("found_index is specified as 0, but 0 window/s found")

        return Tools, calls

    def test_retries_with_straight_quotes(self):
        Tools, calls = self._make_tools()
        _install_chat_history_title_compat(Tools)

        assert Tools.move_window_to_center({"title_re": "与“张三”"}) == "found"
        assert calls == ["与“张三”", '与"张三"']

    def test_is_idempotent(self):
        Tools, calls = self._make_tools()
        _install_chat_history_title_compat(Tools)
        _install_chat_history_title_compat(Tools)

        Tools.move_window_to_center({"title_re": "与“张三”"})
        assert len(calls) == 2  # 只多了一次重试，没有被重复包装

    def test_other_exceptions_are_not_swallowed(self):
        class Tools:
            @staticmethod
            def move_window_to_center(Window=None, Window_handle=0):
                raise ValueError("别的错误")

        _install_chat_history_title_compat(Tools)
        with pytest.raises(ValueError):
            Tools.move_window_to_center({"title_re": "与“张三”"})
