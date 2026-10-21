# wechat-mcp

把 **微信 4.1.6+ 桌面版** 的聊天记录、消息发送、通讯录、朋友圈等能力，暴露为标准
[MCP](https://modelcontextprotocol.io) 工具，供 Claude Desktop、Cursor、Cherry Studio
等支持 MCP 的客户端调用。

本项目是 [`mcp-server-wechat`](https://pypi.org/project/mcp-server-wechat/) 的**功能等价替代实现**：
旧项目依赖 `pywechat`，只支持微信 3.9 系列（4.x 的 UI 树已完全重构）。本项目改用
[`pywechat` 仓库中面向 4.1+ 的 `pyweixin` 子模块](https://github.com/Hello-Mr-Crab/pywechat) 重写，
**四个核心工具的名称与参数与旧版完全一致**，可直接替换客户端配置。

> 实测环境：Windows 11 + 微信 4.1.15.56 + Python 3.13。
> 所有操作基于 Windows UI 自动化（pywinauto / UIA），**不做任何逆向 Hook**。

---

## 一、功能对照

### 核心工具（与 `mcp-server-wechat` 同名同参，可直接替换）

| 工具 | 说明 | 必填参数 |
| --- | --- | --- |
| `wechat_get_chat_history` | 获取某天的聊天记录 | `to_user`、`target_date` |
| `wechat_send_message` | 给单个好友/群聊发一条消息 | `to_user`、`message` |
| `wechat_send_multiple_messages` | 给单个好友/群聊发多条消息 | `to_user`、`messages` |
| `wechat_send_to_multiple_friends` | 给多个好友发消息（群发或分别发送） | `to_user`、`message` |

### 扩展工具（pyweixin 4.1+ 的额外能力）

| 工具 | 说明 |
| --- | --- |
| `wechat_send_files` | 发送本地文件（可附带文本） |
| `wechat_get_contacts` | 好友 / 群聊 / 公众号 / 服务号 / 企业微信联系人 |
| `wechat_get_group_members` | 群成员列表 |
| `wechat_search_chat_history` | 关键字全局搜索聊天记录 |
| `wechat_get_recent_sessions` | 最近会话列表 |
| `wechat_check_new_messages` | 检查未读消息 |
| `wechat_get_moments` | 获取最近朋友圈 |
| `wechat_post_moments` | 发布朋友圈 |
| `wechat_export_chat_files` | 导出与某人的聊天文件 |
| `wechat_get_weixin_info` | 微信版本、wxid、数据目录等排障信息 |

---

## 二、环境要求

| 项 | 要求 |
| --- | --- |
| 操作系统 | **仅 Windows 10 / 11**（详见下方「平台支持」） |
| 微信 | 桌面版 **4.1.6 及以上**，已启动并登录 |
| Python | 3.10+ |
| 依赖 | `pywechat127 >= 1.9.8`（PyPI 包名，内含 `pyweixin`）、`mcp >= 1.9.0` |

> **重要**：调用期间请勿手动操作微信窗口，否则会打断 UI 自动化流程。

### 平台支持

| 平台 | 状态 | 说明 |
| --- | --- | --- |
| Windows 10 / 11 | ✅ 支持 | 唯一受支持的平台，已在微信 4.1.15.56 上实测 |
| macOS | ❌ 不支持 | 见下方原因 |
| Linux | ❌ 不支持 | 见下方原因 |

**为什么只能跑 Windows**：自动化能力来自 `pyweixin`，其底层是 `pywinauto`
（UI Automation）加 `win32gui` / `winreg` / `win32clipboard` / `win32con` / `pycaw`
等 Win32 接口；`pywechat127` 在包元数据中也只声明了 `Platforms: Windows` 与
`Operating System :: Microsoft :: Windows`。微信在 macOS 上是原生 App，UI 树和
可访问性接口完全不同，这些 Win32 调用无法复用。

**在 macOS / Linux 上会发生什么**：MCP 层（`server.py` / `args.py` / `dates.py` /
`config.py`）是纯 Python、与平台无关，`pip install` 会因 `sys_platform` 标记自动跳过
`pywechat127`，服务器仍可启动并列出 14 个工具，但任一工具调用都会返回结构化错误，
不会崩溃：

```json
{
  "status": "error",
  "error_type": "WeChatUnavailable",
  "message": "当前平台 darwin 不受支持，pyweixin 只能驱动 Windows 桌面版微信。",
  "hint": "请在 Windows 10/11 上运行本 MCP 服务器。"
}
```

**若要移植**：无需重写工具层，只需替换 `wechat_client.py` 中与 `pyweixin` 交互的
那一层（约 10 个方法），改用目标平台的原生可访问性接口（macOS 可用 AppleScript /
`PyObjC` 的 AXUIElement，Linux 可用 AT-SPI）。工具定义、参数 schema、日期解析、
配置与测试均可直接复用：`build_server(client=...)` 已支持注入自定义客户端实现。

---

## 三、安装

```bat
git clone <本仓库>
cd wechat-mcp
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

安装完成后自带两个入口，等价：

```bat
wechat-mcp                       :: 控制台脚本
python -m wechat_mcp             :: 模块方式
```

---

## 四、配置到 MCP 客户端

### stdio（推荐）

```json
{
  "mcpServers": {
    "wechat": {
      "command": "D:\\code\\python\\wechat-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "wechat_mcp", "--folder-path", "D:\\wechat-logs"],
      "env": { "WECHAT_MCP_MAX_MESSAGES": "3000" }
    }
  }
}
```

`--folder-path` 指定聊天记录 JSON 的落盘目录；不传则不落盘（除非单次调用显式
`save_json=true`，此时默认写到 `%USERPROFILE%\wechat-mcp\chat_history`）。

### SSE / Streamable HTTP

旧版需要分别安装 `mcp_server_wechat_sse`、`mcp_server_wechat_Streamable_HTTP` 两套包，
本项目同一个包通过 `--transport` 切换：

```bat
wechat-mcp --transport sse --host 127.0.0.1 --port 3000
:: 客户端配置 "url": "http://localhost:3000/sse"

wechat-mcp --transport streamable-http --host 127.0.0.1 --port 3000
:: 客户端配置 "url": "http://localhost:3000/mcp"
```

### 命令行参数

| 参数 | 说明 |
| --- | --- |
| `--transport` | `stdio`（默认）/ `sse` / `streamable-http` |
| `--host`、`--port` | HTTP 传输监听地址与端口，默认 `127.0.0.1:8000` |
| `--folder-path` / `--output-dir` | 产物默认保存目录 |
| `--maximize` | 操作期间最大化微信窗口（界面复杂度高时更稳） |
| `--close-wechat` | 每次操作结束后关闭微信（默认不关闭） |
| `--log-level` | 日志级别，默认 `INFO`，日志走 stderr，不污染 stdio 协议 |

### 环境变量

均可通过 MCP 配置的 `env` 段传入，优先级低于命令行参数。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WECHAT_MCP_OUTPUT_DIR` | 空 | 产物保存目录 |
| `WECHAT_MCP_MAX_MESSAGES` | `2000` | 按日期取记录时最多回溯的消息条数 |
| `WECHAT_MCP_INITIAL_BATCH` | `200` | 首次回溯条数，未命中则翻倍 |
| `WECHAT_MCP_SEARCH_PAGES` | `5` | 会话列表滚动查找次数，0 = 用顶部搜索框 |
| `WECHAT_MCP_SEND_DELAY` | `0.2` | 单条消息发送间隔（秒） |
| `WECHAT_MCP_MAXIMIZE` | `false` | 操作期间最大化窗口 |
| `WECHAT_MCP_CLOSE_WECHAT` | `false` | 操作后关闭微信 |
| `WECHAT_MCP_LOG_LEVEL` | `INFO` | 日志级别 |

---

## 五、关键实现说明

### 1. 按日期取聊天记录：倍增回溯

微信本身没有"按日期查询"的接口，`pyweixin` 只能"从最新往上回溯 N 条"。因此
`wechat_get_chat_history` 采用**倍增回溯**：

1. 先取 `initial_batch`（默认 200）条；
2. 解析每条的时间文本，若最早一条仍**晚于**目标日期，说明没翻够 → 条数翻倍重取；
3. 命中目标日期的消息、或已回溯到目标日期之前、或历史已到头、或达到
   `max_messages` 上限时停止；
4. 按日期过滤，统一成**时间正序**后返回。

时间文本还原覆盖微信的全部显示形态，这也是 `dates.py` 独立成模块并可单独测试的原因：

| 界面显示 | 含义 |
| --- | --- |
| `2026年10月21日 13:47` | 含完整日期（聊天记录窗口的常见形态） |
| `10月20日 17:31` | 今年 |
| `昨天 17:31` / `前天` / `星期三 10:00` | 相对日期 |
| `13:47` | 今天 |
| `2026-10-21 13:47` | 英文版微信 |

目标日期 `target_date` 支持 `25/3/22`（与旧版 `YY/M/D` 一致）、`2025-03-22`、
`20250322`、`3/22`、`今天/昨天/前天`。

### 2. 微信 4.1.12+ 窗口标题引号兼容（关键修复）

PyPI 上的 `pywechat127 1.9.8` 把"聊天记录"窗口标题硬编码成**中文弯引号**
`与“好友”`，而微信 **4.1.12+** 的窗口标题改用了**直引号** `与"好友"`。结果是
`move_window_to_center` 找不到窗口，抛出：

```
ElementNotFoundError: found_index is specified as 0, but 0 window/s found
```

上游已在 git 主干按版本号做过修复，但尚未发布到 PyPI。本项目在 `wechat_client.py`
里加了**最小侵入**的兼容补丁 `_install_chat_history_title_compat`：先按原条件查找，
仅在失败且判定为引号问题时，换另一种引号重试一次。因此无论安装的是 PyPI 版还是
git 版都能正常工作，且修复上线后补丁会自动失效（首次即命中，不触发重试）。

### 3. stdio 通道保护

`pyweixin` 内部存在 `print()`，在 stdio 传输下会污染 JSON-RPC 通道导致客户端解析失败。
本项目在所有 pyweixin 调用期间把 `sys.stdout` 临时指向 `stderr`，日志同样只写 stderr。

### 4. 串行化

微信是单实例 GUI，UI 自动化不能并发。客户端内部使用一把可重入锁，所有工具调用串行执行。

### 5. 双 SDK 版本兼容

同时适配 MCP Python SDK 两个大版本：`mcp >= 2`（`MCPServer`）与 `mcp >= 1.9`（`FastMCP`），
无需用户手动锁版本。

---

## 六、与旧版 `mcp-server-wechat` 的行为差异

四项核心能力等价，以下差异是有意为之：

| 项 | 旧版 | 本项目 | 原因 |
| --- | --- | --- | --- |
| 操作后关闭微信 | 默认关闭 | **默认不关闭** | 反复关闭会导致下次调用需要重新登录 |
| `messages` 字符串解析 | 按英文逗号拆分 | 只按换行/分号拆分 | 避免"你好，张三"被拆成两条消息误发 |
| 多好友分别发送数量不匹配 | 补齐/截断后照发 | 直接报错 | 防止把 A 的话发给 B |
| 返回值 | 混用纯文本与 JSON | 统一 JSON（`status`/`error_type`/`hint`） | 便于模型判断成败并自我修正 |
| 传输方式 | 三个独立包 | 一个包 + `--transport` | 部署简化 |
| 错误提示 | 异常堆栈 | 结构化错误 + 可执行建议 | 模型可据此重试 |

---

## 七、已知限制

- **仅支持 Windows**（macOS / Linux 的现状与原因见「平台支持」）；必须保持微信已登录、窗口未被锁屏。
- 受 UI 自动化固有开销影响：一次"按日期取记录"约 10~15 秒；回溯条数越多越慢。
- 红包/转账消息在界面上没有时间戳，无法参与按日期过滤（会计入 `scanned` 但不返回）。
- 群聊记录的发送人解析较慢，可把群成员列表通过 `group_members` 传入以提速。
- 语音/视频通话等操作未暴露为工具（需要额外的音频虚拟驱动），如需可基于
  `pyweixin.Messages.send_audios_to_friend` 自行扩展。

---

## 八、开发与测试

```bat
pip install -e ".[dev]"
python -m pytest
```

测试共 91 项，覆盖：

- `tests/test_dates.py`：时间文本解析、目标日期解析、日期过滤与排序（含跨年、
  星期、跨零点等边界）；
- `tests/test_args.py`：工具入参归一化（数组/JSON/换行/分隔符、误拆防护）；
- `tests/test_client.py`：倍增回溯、空记录兼容、落盘、发送校验、引号兼容补丁、
  非 Windows 平台的降级提示；
- `tests/test_protocol.py`：**真实 stdio 子进程 + 官方 SDK 客户端**，验证工具清单、
  JSON Schema、端到端调用、错误参数处理、资源读取与 stdout 纯净性。

测试使用 `tests/fake_wechat.py` 提供的 `pyweixin` 替身，**无需真实微信即可运行**。

目录结构：

```
src/wechat_mcp/
├── server.py          # MCP 服务器：工具定义、参数 schema、CLI
├── wechat_client.py   # pyweixin 封装：串行化、stdout 保护、兼容补丁
├── dates.py           # 微信时间文本解析与按日期过滤（纯函数）
├── args.py            # 入参归一化
└── config.py          # 运行配置（环境变量）
```

---

## 九、免责声明

本项目仅用于个人效率场景（如让 AI 帮你整理自己的聊天记录）。请遵守《微信软件许可及
服务协议》，不要用于群发营销、骚扰他人或任何违法用途；由此产生的一切后果由使用者自行承担。

依赖的上游项目：[Hello-Mr-Crab/pywechat](https://github.com/Hello-Mr-Crab/pywechat)（LGPL）。

## License

MIT
