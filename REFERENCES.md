# 公开参考

这些是实现思路与计量规则的参考来源，不是运行依赖。本工具没有直接调用下列项目的服务，也不是它们的官方发行版。

## 原采集方案参考

- ccusage：https://github.com/ccusage/ccusage
- Codex 数据源说明：https://ccusage.com/guide/codex/
- ccusage Codex 适配器说明：https://github.com/ccusage/ccusage/blob/main/rust/adapters/codex/src/README.md
- ccusage Codex parser：https://github.com/ccusage/ccusage/blob/main/rust/adapters/codex/src/parser.rs
- CodexBar：https://github.com/steipete/CodexBar
- CodexMonitor：https://github.com/Dimillian/CodexMonitor
- OpenAI Codex protocol：https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs

主要参考本地日志、累计/单次用量、重复风险、子代理回放边界及配额与实际 Token 分层展示。本项目保留缺失字段/模型/档位的未知状态，不拿其他模型价格无提示兜底。

## 设计背景

- OpenDesign：https://github.com/nexu-io/open-design
- Taste Skill：https://github.com/Leonxlnx/taste-skill

本版按用户选中的第三张概念图实施浅色分栏设计，使用自行编写的 HTML/CSS/JS；没有声称在运行时使用上述仓库，也没有复制它们的品牌素材或打包字体文件。

## 官方计量与费率参考

- 推理 Token：https://developers.openai.com/api/docs/guides/reasoning
- Credits 定价：https://learn.chatgpt.com/docs/pricing
- Fast 档位：https://learn.chatgpt.com/docs/agent-configuration/speed

内置 `rates.json` 的费率在 2026-09-08 依据官方定价与 Speed 页面核对。它是指定时点的本地估算表，不是历史账单数据库；历史费率变化、模型别名、服务档位未知时，估算会存在偏差或无法覆盖。Credits 不是美元，不与 API Priority 美元费率混用。

本版取消 MCP，未接入 OTLP，也不要求用户改动 Codex。公开日志格式可能继续演进，兼容修复应只修改本工具并用合成样本补测试。
