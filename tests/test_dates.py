"""日期解析与按日期过滤的单元测试。"""

from datetime import date, datetime

import pytest

from wechat_mcp.dates import (
    filter_records_by_date,
    normalize_records,
    parse_target_date,
    parse_wechat_datetime,
)

NOW = datetime(2026, 10, 21, 15, 0)  # 2026-10-21 是星期三


class TestParseWechatDatetime:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("14:30", datetime(2026, 10, 21, 14, 30)),
            ("09:05", datetime(2026, 10, 21, 9, 5)),
            ("2026年3月22日 14:30", datetime(2026, 3, 22, 14, 30)),
            ("3月22日 14:30", datetime(2026, 3, 22, 14, 30)),
            ("2026-03-22 14:30", datetime(2026, 3, 22, 14, 30)),
            ("昨天 22:00", datetime(2026, 10, 20, 22, 0)),
            ("前天 08:00", datetime(2026, 10, 19, 8, 0)),
            ("星期一 10:00", datetime(2026, 10, 19, 10, 0)),
            ("13:20", datetime(2026, 10, 21, 13, 20)),
        ],
    )
    def test_known_formats(self, text, expected):
        assert parse_wechat_datetime(text, NOW) == expected

    def test_weekday_means_last_week_when_it_is_today(self):
        # 微信对今天的消息只显示时间，出现"星期三"必然是上周三
        assert parse_wechat_datetime("星期三 10:00", NOW) == datetime(2026, 10, 14, 10, 0)

    def test_month_day_falls_back_to_previous_year(self):
        now = datetime(2026, 1, 2, 10, 0)
        assert parse_wechat_datetime("12月30日 09:05", now) == datetime(2025, 12, 30, 9, 5)

    def test_meridiem_is_applied(self):
        assert parse_wechat_datetime("下午 3:20", NOW) == datetime(2026, 10, 21, 15, 20)

    @pytest.mark.parametrize(
        "text",
        ["", None, "红包或转账(无法获取时间戳)", "不是时间", "[]"],
    )
    def test_unparseable_returns_none(self, text):
        assert parse_wechat_datetime(text, NOW) is None

    def test_time_in_future_is_treated_as_yesterday(self):
        now = datetime(2026, 10, 21, 0, 5)
        assert parse_wechat_datetime("23:50", now) == datetime(2026, 10, 20, 23, 50)


class TestParseTargetDate:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("25/3/22", date(2025, 3, 22)),          # 与原 mcp-server-wechat 的 YY/M/D 一致
            ("25-3-22", date(2025, 3, 22)),
            ("2025-03-22", date(2025, 3, 22)),
            ("2025/3/22", date(2025, 3, 22)),
            ("20250322", date(2025, 3, 22)),
            ("３／２２", date(2026, 3, 22)),          # 全角字符
            ("3/22", date(2026, 3, 22)),             # 省略年份
            ("今天", date(2026, 10, 21)),
            ("昨天", date(2026, 10, 20)),
            ("前天", date(2026, 10, 19)),
        ],
    )
    def test_supported_formats(self, value, expected):
        assert parse_target_date(value, today=date(2026, 10, 21)) == expected

    @pytest.mark.parametrize("value", ["", None, "abc", "2025-02-30", "13/40/1"])
    def test_invalid_raises(self, value):
        with pytest.raises(ValueError):
            parse_target_date(value, today=date(2026, 10, 21))


class TestNormalizeRecords:
    def test_empty_triple_tuple_from_pyweixin(self):
        # pyweixin 在聊天记录为空时返回 ([], [], [])，而不是空列表
        assert normalize_records(([], [], [])) == []

    def test_json_string(self):
        assert normalize_records('[{"消息内容": "hi"}]') == [{"消息内容": "hi"}]

    def test_keeps_dicts(self):
        record = {"消息发送人": "张三", "消息内容": "hi", "消息类型": "文本"}
        assert normalize_records([record]) == [record]


class TestFilterRecordsByDate:
    def test_descending_ui_order_becomes_ascending(self):
        target = date(2026, 10, 20)
        records = [
            {"消息内容": "昨天-晚", "消息发送时间": "昨天 22:00"},
            {"消息内容": "昨天-早", "消息发送时间": "昨天 08:00"},
            {"消息内容": "前天", "消息发送时间": "前天 20:00"},
        ]
        result = filter_records_by_date(records, target, now=NOW)
        assert [item["消息内容"] for item in result.records] == ["昨天-早", "昨天-晚"]
        assert result.reached is True
        assert result.oldest == date(2026, 10, 19)

    def test_marks_not_reached_when_history_is_too_recent(self):
        result = filter_records_by_date(
            [
                {"消息内容": "a", "消息发送时间": "10:00"},
                {"消息内容": "b", "消息发送时间": "09:00"},
            ],
            date(2026, 10, 1),
            now=NOW,
        )
        assert result.matched == 0
        assert result.reached is False

    def test_unparseable_timestamps_are_counted_but_not_matched(self):
        result = filter_records_by_date(
            [
                {"消息内容": "转账", "消息发送时间": "红包或转账(无法获取时间戳)"},
                {"消息内容": "今天", "消息发送时间": "14:00"},
            ],
            date(2026, 10, 21),
            now=NOW,
        )
        assert result.matched == 1
        assert result.parse_failures == 1
        assert result.records[0]["消息内容"] == "今天"
        assert result.records[0]["时间ISO"] == "2026-10-21 14:00"

    def test_empty_input(self):
        result = filter_records_by_date([], date(2026, 10, 21), now=NOW)
        assert result.matched == 0
        assert result.reached is False
