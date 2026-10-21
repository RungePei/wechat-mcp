"""工具入参归一化的单元测试。"""

import pytest

from wechat_mcp.args import (
    as_messages,
    as_str_list,
    as_text,
    build_per_friend_messages,
)


class TestAsMessages:
    def test_list_passthrough(self):
        assert as_messages(["你好", "再见"]) == ["你好", "再见"]

    def test_json_array_string(self):
        assert as_messages('["你好", "再见"]') == ["你好", "再见"]

    def test_newline_separated(self):
        assert as_messages("你好\n再见\n") == ["你好", "再见"]

    def test_semicolon_separated(self):
        assert as_messages("你好；再见") == ["你好", "再见"]

    def test_single_message_keeps_inner_commas(self):
        # 关键回归点：正文里的逗号不能被当成消息分隔符
        assert as_messages("你好，张三，明天见") == ["你好，张三，明天见"]

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            as_messages("   ")


class TestAsStrList:
    def test_comma_separated(self):
        assert as_str_list("张三,李四,王五", "to_user") == ["张三", "李四", "王五"]

    def test_chinese_separators_and_dedup(self):
        assert as_str_list("张三、李四；张三", "to_user") == ["张三", "李四"]

    def test_list_input(self):
        assert as_str_list(["张三 ", " 李四"], "to_user") == ["张三", "李四"]

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            as_str_list("  ", "to_user")

    def test_as_text_strips(self):
        assert as_text("  张三  ", "to_user") == "张三"


class TestBuildPerFriendMessages:
    def test_broadcast_single_message(self):
        assert build_per_friend_messages(["张三", "李四"], "大家好") == [["大家好"], ["大家好"]]

    def test_per_friend_list(self):
        result = build_per_friend_messages(["张三", "李四"], ["你好张三", "你好李四"])
        assert result == [["你好张三"], ["你好李四"]]

    def test_per_friend_newlines(self):
        result = build_per_friend_messages(["张三", "李四"], "你好张三\n你好李四")
        assert result == [["你好张三"], ["你好李四"]]

    def test_single_length_list_broadcasts(self):
        assert build_per_friend_messages(["张三", "李四"], ["统一通知"]) == [
            ["统一通知"],
            ["统一通知"],
        ]

    def test_mismatched_length_raises(self):
        with pytest.raises(ValueError) as exc:
            build_per_friend_messages(["张三", "李四"], ["一", "二", "三"])
        assert "不一致" in str(exc.value)

    def test_json_array_string(self):
        result = build_per_friend_messages(["张三", "李四"], '["给张三", "给李四"]')
        assert result == [["给张三"], ["给李四"]]
