"""测试用 MCP 服务器：注入 fake pyweixin，通过真实 stdio 协议对外服务。

用法（由 test_protocol.py 以子进程方式拉起）：:

    python tests/fake_server.py
"""

import sys
from datetime import datetime
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"
for candidate in (str(TESTS_DIR), str(SRC_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from fake_wechat import FakeFramework, build_history  # noqa: E402

from wechat_mcp.config import ClientConfig  # noqa: E402
from wechat_mcp.server import build_server  # noqa: E402
from wechat_mcp.wechat_client import WeChatClient  # noqa: E402


def main() -> None:
    framework = FakeFramework()
    framework.Messages.history = build_history(days=6, per_day=3, now=datetime.now())
    client = WeChatClient(ClientConfig(), framework=framework)
    server = build_server(client=client)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
