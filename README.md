# Codex Token Lens · 0.2.0

**独立运行、只读本地日志的 Codex 用量面板。**采用第三版浅色分栏设计，默认进入 Threads；数字使用中文数量级。无需 MCP、API Key、模型调用或修改 Codex 配置。

> 此发行包为 Python 源码版，不是签名安装程序。运行软件后读取本地日志。

## 启动与停止

需要 Python **3.10+**。运行时只用 Python 标准库和系统浏览器，无需 pip、Node、npm、联网下载资源。

解压到独立目录，例如 `~/Applications/codex-token-lens`，**不得放在 `~/.codex`、`CODEX_HOME` 或任何采集目录里面**。在项目目录执行：

```bash
# macOS / Linux：启动独立后台进程并打开浏览器
python3 -B service.py start
python3 -B service.py status
python3 -B service.py open
python3 -B service.py stop
```

Windows 对应使用 `py -3 -B service.py start`（其他动作相同），或双击 `start-windows.bat`。macOS 可运行 `bash start-macos.command`，Linux 可运行 `bash start-linux.sh`；macOS Finder 双击前可能需要给本工具启动文件执行权限，不要关闭系统安全防护。

`start` 创建独立后台进程，部署终端关闭后仍可运行；`stop` 只向已认证的 Lens 实例发送停止请求，不结束 Codex。不会安装开机自启、系统服务或修改 shell 配置。操作系统关机后需重新启动。若部署宿主/沙箱会强制回收其创建的全部进程，改为在系统普通终端执行本工具的 start；不要为此修改 Codex 沙箱或开启系统服务。

默认仅监听 `127.0.0.1:8765`。必须通过启动时显示的带密钥地址访问；后续用 `service.py open` 重新打开。端口冲突可使用 `--port 8766`。**授权地址、runtime.json、service.log 不要分享**。

本地 Codex 部署提示词见 **`INSTALL_WITH_CODEX.md`**：这是部署说明，不是要求用 Codex 查询用量。

## 三条核心交互

| 页面 | 功能 |
|---|---|
| 每日按时 | 日期切换、24 个小时的输入/输出柱图，悬浮显示分项；点击小时筛选对应 Threads |
| 周期 | 自然周、自然月、自定义起止日期；每日趋势、热力图；点击日期下钻到小时 |
| Threads | 搜索、项目/模型/来源筛选、原始数值排序、分页、主子线程合并；右侧详情、事件账本与导出 |

顶部是今日用量、所选周期用量、可估算的 Credits 与缓存率。右侧详情按“输入包含缓存、输出包含推理”的层级展示，避免重复相加。未知分项显示 `—`，不伪装成零；部分统计明确标出覆盖不足。

Thread 别名及显示偏好只保存到**当前浏览器本地存储**，不修改 Codex 会话。真实日志没有标题时显示项目与短 ID，不读取提示词来生成标题。演示中的描述性标题是模拟数据。清除浏览器站点数据会清除别名；更换浏览器或端口不会自动同步别名。

### 中文数字显示

| 原始数值 | 默认显示 | 可选“万/亿”显示 |
|---:|---:|---:|
| 1,234 | 1.23千 | 1.23千 |
| 12,345 | 1.23万 | 1.23万 |
| 124,532 | 1.25十万 | 12.45万 |
| 1,862,450 | 1.86百万 | 186.25万 |

还支持千万、亿、万亿。鼠标悬浮或打开精确数值对话框可查看整数；CSV / JSON 始终保留原始精度。缩写只影响显示，不影响排序、累计及费用计算。

## 数据来源与写入边界

```text
Codex 原始 sessions / archived_sessions（只读）
                      ↓
             独立 Python Lens 进程
                      ↓
    ~/.codex-token-lens/usage.sqlite3（工具自己的数据库）
                      ↓
             127.0.0.1 本地浏览器
```

默认读取环境变量 `CODEX_HOME`；没有设置时使用 `~/.codex`。支持显式指定多个来源：

```bash
python3 -B service.py start --home "$HOME/.codex" --home "/path/to/other/codex-home"
```

修改参数前先停止已有 Lens 实例；已有实例运行时 `start` 只复用它，不会自动重配。

也可复制本工具的 `config.example.json`，删掉不需要的示例路径后使用 `--config /path/to/lens-roots.json`。这份 JSON 是 Lens 自己的来源设置，**不是 Codex 的 config.toml**。在 WSL 与 Windows 混合使用时，应指定实际可访问的日志路径；不跨操作系统猜测或修改 Codex 配置。

默认工具状态目录为 `~/.codex-token-lens/`：SQLite 及其辅助文件、`runtime.json`、`service.log`。浏览器导出写入你选择的下载位置；命令行导出必须指定 Codex 目录以外的位置。完整边界见 **`SECURITY.md`**。

工具不调用 Codex CLI，不修改 `config.toml`、`auth.json`、原始 JSONL、skills、hooks、AGENTS.md，也不启用 MCP 或遥测。新增数据库/导出目标经过路径检查；受保护目录、危险链接目标会被拒绝。**本次部署无需任何 Codex 文件改动。**

## 统计口径与限制

- 总 Token = 输入 + 输出；缓存包含在输入中，推理包含在输出中。计数不代表能读取隐藏思考链正文。
- 累计快照转增量，过滤重复通知与可识别归档/多代理回放；无法可靠归属的记录进入诊断，不默默加入“精确总量”。
- 周期是统计区间，不是官方订阅配额。Credits 由内置模型/档位费率估算；**不是美元、真实扣账或账户剩余额度**。未知模型/档位/字段影响估算覆盖率，不编造价格。
- 配额只显示日志已记录的带时间快照，可能过期；不是主动查询账户后台。
- 仅覆盖已扫描的本地日志。远程设备、云端任务、未落盘/缺失的 usage 不能自动补齐。`codex exec --json` 聚合输出不与标准 rollout 混算，进入诊断。
- 浏览器可选本地时区、北京时间、纽约时间、UTC。自然周为周一至周日。小时按事件落盘时间归属；夏令时回拨时，同一民用小时的两个时段汇总到同一小时桶，并非逐秒计算模型工作时长。

## 性能和离线运行

默认每 15 秒检查日志，读取新增字节并写入自己的 SQLite；没有文件变化时复用报表，前端用 ETag 跳过未变数据；页面隐藏时暂停前端刷新。**仍有目录枚举与文件状态检查，不是操作系统原生文件监听。**首次扫描和大量活跃历史会增加 CPU、磁盘及内存占用，本包没有给出你的设备实测值。

程序自身不发起模型/API 请求，也不下载价格。它只通过本机回环地址提供面板及管理自身进程；静态界面无 CDN、远程字体或统计脚本。费率需人工核对并更新本项目的 `rates.json`。

## 其他运行方式

```bash
# 前台运行，Ctrl+C 退出
python3 -B lens.py

# 扫描一次并导出交互 HTML；导出路径不能在 Codex 目录里
python3 -B lens.py --once --export "$HOME/Desktop/lens-report.html"

# 指定更低检查频率
python3 -B service.py start --interval 15
```

后端时区 `local` 和 `UTC` 不依赖第三方时区包；其他 IANA 时区需要系统可用的时区数据库。无需为了界面北京时间/纽约显示安装 Python 时区包，前端使用浏览器时区支持。

升级前停止**已确认属于 Lens**的旧进程。0.2.0 沿用原有 SQLite 表结构；重要统计可先在旧 Lens 停止后备份工具自己的数据目录。不要删除 Codex 日志来“清理统计”。需要重建缓存时，只能在 Lens 停止后处理它自己的 SQLite 及辅助文件。

## 测试与交付

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
# 开发测试，可选，不是运行依赖：
node tests/test_format.js
python3 -B demo.py  # 先生成合成数据 preview.html
python3 -B tests/browser_smoke.py
```

本次发行的验证结果见 **`TEST_RESULTS.md`**。

`tests/browser_smoke.py` 需要开发用 Playwright 和 Chromium；可通过 `LENS_CHROMIUM` 指向浏览器，不要为日常运行安装这些开发依赖。

本工具是独立的本地计量辅助软件，不是 OpenAI 官方客户端或账单系统。

## SwiftBar 菜单栏插件

配套项目：[swiftbar-codex-usage](https://github.com/YouXH94/swiftbar-codex-usage)。今日 Token 插件通过本服务的 `/api/report` 读取统计；剩余配额插件独立运行。

按上面的默认命令启动后，状态文件位于 `~/.codex-token-lens/runtime.json`，配套插件默认读取此位置。如使用 `--state-dir` 自定义目录，请设置插件的 `TOKEN_LENS_STATE`。不要分享状态文件或带授权参数的面板地址。
