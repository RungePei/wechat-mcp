"""用于测试的 pyweixin 替身。

只实现本项目实际调用的方法，并记录调用参数，便于断言"是否按预期调用了底层"。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional


def wechat_label(stamp: datetime, now: datetime) -> str:
    """按微信的真实习惯生成时间文本。

    今天只显示 ``HH:MM``，昨天显示 ``昨天 HH:MM``，一周内显示 ``星期X HH:MM``，
    更早显示 ``M月D日 HH:MM``。
    """
    delta = (now.date() - stamp.date()).days
    if delta <= 0:
        return stamp.strftime("%H:%M")
    if delta == 1:
        return f"昨天 {stamp.strftime('%H:%M')}"
    if delta < 7:
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return f"{weekday_names[stamp.weekday()]} {stamp.strftime('%H:%M')}"
    return f"{stamp.month}月{stamp.day}日 {stamp.strftime('%H:%M')}"


def build_history(days: int = 10, per_day: int = 5, now: Optional[datetime] = None) -> list[dict]:
    """生成按"新→旧"排列的聊天记录，模拟微信界面顺序。"""
    now = now or datetime.now()
    records: list[dict] = []
    for day_offset in range(days):
        day = now - timedelta(days=day_offset)
        for index in range(per_day):
            if day_offset == 0:
                # 当天的消息必须严格落在"已过去"的时间，避免时钟边界导致误判
                stamp = now.replace(second=0, microsecond=0) - timedelta(minutes=5 * (index + 1))
            else:
                stamp = day.replace(hour=9 + index, minute=0, second=0, microsecond=0)
            label = wechat_label(stamp, now)
            records.append(
                {
                    "消息发送人": "对方" if index % 2 else "我",
                    "消息内容": f"第{day_offset}天第{index}条",
                    "消息类型": "文本",
                    "消息发送时间": label,
                }
            )
    return records


class FakeMessages:
    def __init__(self) -> None:
        self.history: list[dict] = []
        self.calls: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.batch_sent: list[dict[str, Any]] = []
        self.empty_fallback = False

    def dump_chat_history(self, friend, number, groupMembers=None, is_json=False,
                          save_detail=False, target_folder=None, search_pages=None,
                          is_maximize=None, close_weixin=None):
        self.calls.append({"friend": friend, "number": number, "close_weixin": close_weixin})
        if self.empty_fallback:
            # 复现 pyweixin 在聊天记录为空时的三元组返回
            return ([], [], [])
        return [dict(record) for record in self.history[:number]]

    def send_messages_to_friend(self, friend, messages, at_members=None, at_all=False,
                                search_pages=None, clear=None, send_delay=None,
                                is_maximize=None, close_weixin=None):
        self.sent.append(
            {
                "friend": friend,
                "messages": list(messages),
                "at_members": list(at_members or []),
                "at_all": at_all,
                "send_delay": send_delay,
                "close_weixin": close_weixin,
            }
        )

    def send_messages_to_friends(self, friends, messages, clear=None, send_delay=None,
                                 is_maximize=None, close_weixin=None):
        self.batch_sent.append(
            {"friends": list(friends), "messages": [list(group) for group in messages]}
        )

    def search_chat_history(self, keyword, number=None, with_content=False,
                            is_maximize=None, close_weixin=None):
        return {"张三": ["明天见", "好的"]} if keyword else {}

    def dump_recent_sessions(self, recent="Today", chat_only=False, is_maximize=None, close_weixin=None):
        return [("张三", "10:30", "在吗"), ("项目群", "昨天", "收到")]

    def check_new_messages(self, is_maximize=None, search_pages=None, close_weixin=None):
        return {"张三": [{"消息内容": "在吗", "消息类型": "文本"}]}


class FakeFiles:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.exported: list[dict[str, Any]] = []

    def send_files_to_friend(self, friend, files, with_messages=False, messages=None,
                             messages_first=False, send_delay=None, clear=None,
                             is_maximize=None, close_weixin=None):
        self.sent.append(
            {
                "friend": friend,
                "files": list(files),
                "with_messages": with_messages,
                "messages": list(messages or []),
                "messages_first": messages_first,
            }
        )

    def save_chatfiles(self, friend, number, target_folder=None, is_maximize=None, close_weixin=None):
        self.exported.append({"friend": friend, "number": number, "target_folder": target_folder})


class FakeContacts:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_friends_info(self, is_maximize=None, close_weixin=None):
        self.calls.append("get_friends_info")
        return ["张三", "李四"]

    def get_groups_info(self, is_maximize=None, close_weixin=None):
        self.calls.append("get_groups_info")
        return ["项目群"]

    def get_offAcc_info(self, is_maximize=None, close_weixin=None):
        self.calls.append("get_offAcc_info")
        return ["新华社"]

    def get_serAcc_info(self, is_maximize=None, close_weixin=None):
        self.calls.append("get_serAcc_info")
        return ["微信支付"]

    def get_wecom_friends_info(self, is_maximize=None, close_weixin=None):
        self.calls.append("get_wecom_friends_info")
        return []

    def get_friends_detail(self, is_maximize=None, close_weixin=None, is_json=False):
        self.calls.append("get_friends_detail")
        return [{"昵称": "张三", "备注": "客户张三"}]

    def get_offAcc_detail(self, is_maximize=None, close_weixin=None, is_json=False):
        return [{"名称": "新华社"}]

    def get_serAcc_detail(self, is_maximize=None, close_weixin=None, is_json=False):
        return [{"名称": "微信支付"}]

    def get_wecom_friends_detail(self, is_maximize=None, close_weixin=None, is_json=False):
        return []

    def get_groupMembers_info(self, group, is_maximize=None, search_pages=None, close_weixin=None):
        self.calls.append(f"get_groupMembers_info:{group}")
        return ["张三", "李四", "王五"]


class FakeMoments:
    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []

    def dump_recent_posts(self, recent="Today", number=None, with_name=False,
                          save_detail=False, target_folder=None, is_maximize=None, close_weixin=None):
        return [{"发布者": "张三", "内容": "今天天气不错"}]

    def post_moments(self, text="", medias=None, is_maximize=None, close_weixin=None):
        self.posted.append({"text": text, "medias": list(medias or [])})


class FakeTools:
    def __init__(self) -> None:
        self.running = True

    def is_weixin_running(self) -> bool:
        return self.running

    def about_weixin(self) -> dict:
        return {"版本": "4.1.9.30", "wxid": "wxid_test"}


class FakeFramework:
    """聚合对象，模拟 ``pyweixin`` 的模块级类。"""

    def __init__(self) -> None:
        self.Messages = FakeMessages()
        self.Files = FakeFiles()
        self.Contacts = FakeContacts()
        self.Moments = FakeMoments()
        self.Tools = FakeTools()
        self.GlobalConfig = None
