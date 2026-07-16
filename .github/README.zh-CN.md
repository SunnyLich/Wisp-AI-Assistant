<div align="center">

<img src="../assets/doll/idle.png" width="112" alt="Wisp 图标" />

# Wisp

**许多任务更适合由 AI 协助完成，而不是完全交给 AI 代办。Wisp 让这种协作更高效、更易用、更可定制，是一个开源协作平台。**

Wisp 为您提供快捷键驱动的 AI，能够读取您的选中内容、剪贴板、应用程序、浏览器、文档或屏幕截图，同时您无需离开当前工作环境。按下快捷键，选择操作，答案将以流式方式显示在小悬浮窗中，或直接出现在输入光标处。它完全开源、跨平台、可扩展、采用宽松许可证，并且 100% 使用 Python 编写，因此始终易于折腾和改造；这种开放性，即使是 Microsoft Copilot 这样的十亿美元级产品也仍未实现。

[![平台](https://img.shields.io/badge/平台-Windows%20%7C%20macOS%20%7C%20Linux-333333?style=flat-square)](#平台状态)
[![Python](https://img.shields.io/badge/python-3.12-3572A5?style=flat-square)](#快速开始)
[![本地优先](https://img.shields.io/badge/本地优先-上下文与记忆-4B8F8C?style=flat-square)](#隐私与控制)
[![许可证](https://img.shields.io/badge/许可证-MIT-7C3AED?style=flat-square)](#许可证)

**语言：** [English](README.md) | 简体中文 | [繁體中文](README.zh-TW.md) | [Français](README.fr.md) | [Español](README.es.md)

**网站：** [Wisp 文档](https://sunnylich.github.io/Wisp-AI-Assistant/)

[快速开始](#快速开始) | [功能介绍](#wisp-的功能) | [演示](#演示) | [配置](#配置) | [免费 API](#免费模型-api-来源) | [隐私](#隐私与控制)

![Wisp Ctrl+Q 演示](readme-assets/readme-1st-demo.gif)

**悬浮窗查询：** 按下快捷键，选择操作，无需离开当前应用即可获得流式回答。
</div>

---

## 已知问题

[已知问题](https://sunnylich.github.io/Wisp-AI-Assistant/#known-issues)

## Wisp 的功能

Wisp 专为那些打开聊天应用会打断工作流的时刻而设计。

选中文本，按下常规快捷键，点击一个操作键，Wisp 就会使用您启用的上下文来源向您配置的模型提问。回复将以流式方式显示在浮动图标旁边的紧凑气泡中。如果启用了 TTS，答案会在到达时同步朗读。

| 以前您需要... | 现在 Wisp 让您... |
| --- | --- |
| 将文本复制到单独的聊天窗口 | 直接从您正在使用的应用中提问 |
| 每次都为重复任务重新输入说明 | 保存可复用操作，并选择需要的上下文来源 |
| 把每个想法都变成打出来的提示词 | 按住语音快捷键，说出请求，并发送转写后的内容 |
| 读一大段又一大段文字读到疲惫 | 在悬浮窗中流式查看回复，或用 TTS 收听 |
| 手动解释屏幕上的内容 | 捕获选中内容、剪贴板、文档、浏览器页面和屏幕截图 |
| 将提示词、上下文和记忆托付给封闭的助手平台 | 将数据保存在本机，只把您选择的信息和请求发送给模型提供商 |

## 亮点

- **悬浮窗优先** — 浮动图标、操作选择器和回复气泡始终置顶，不占用桌面。
- **实时 ChatGPT/Codex 与 Claude 智能体** — 在“设置”顶部选择 Wisp、ChatGPT 或 Claude Agent，再决定由 Wisp 保持对话连续性，还是把连续性转交给所选智能体。Codex CLI app-server 和 Claude Agent SDK 会在 Wisp 后端运行，并提供实时推理摘要、回复、工具进度、审批和可选的可恢复会话。转录记录的拉取、推送和导出仍可作为离线备用方案。
- **默认隐私保护** — Wisp 没有托管存储层；数据保留在您的机器上，隐私模式可在敏感上下文发送前发出警告或进行脱敏处理。
- **高度可定制** — 每个快捷键、操作键、提示词、上下文来源、粘贴行为、模型路由、语音设置和气泡尺寸均可修改。
- **友好的图形界面** — 设置、检查、隐私报告、记忆工具和模型警告清晰说明正在发生的事情，无需阅读代码。
- **上下文捕获** — Wisp 可以读取选中文本、剪贴板文本、聚焦 UI、打开的文档、浏览器内容、最近文件和可选截图。
- **语音输入输出** — 通过 faster-whisper 实现本地语音识别，外加在本机运行的神经 TTS（Kokoro 以及 GPT-SoVITS 语音克隆），或云端/兼容语音（Cartesia、ElevenLabs、OpenAI 以及任何 OpenAI 兼容服务器），默认禁用 TTS。
- **视觉截图** — 使用 `Ctrl+Alt+Q` 绘制区域并将截图发送给视觉模型。
- **重写并粘贴** — 使用重写快捷键重写选中文本并将结果粘贴回活动字段。
- **自带提供商** — 支持 Groq、Anthropic、OpenAI、Google、DeepSeek、OpenRouter、Mistral、XAI、Together、Cerebras、自定义 OpenAI 兼容服务器、GitHub Copilot 等。
- **本地记忆** — 可选的短期和长期记忆存储在本地，支持查看器编辑或删除记录。
- **插件** — 通过钩子、托盘操作、设置、模型可调用工具、可配置操作和快捷键扩展 Wisp。
- **代理任务** — 用于需要分解、审查和产出物的长期任务的沙盒任务框架。

## 演示

![Wisp Ctrl+Alt+Q 屏幕截图演示](readme-assets/readme-2nd-demo.gif)

**视觉截图：** 截图流程适用于视觉上下文重要的场景。`Ctrl+Alt+Q` 允许您绘制区域，将该截图发送给视觉模型，并将答案保留在悬浮窗中而不需要切换应用。

![Wisp 上下文感知重写演示](readme-assets/readme-3rd-demo.gif)

**上下文感知重写：** Wisp 可以在不截图的情况下收集有用的应用上下文，让模型了解您正在做什么。然后重写快捷键只重写选中文本，并把回贴目标指向按下快捷键时捕获的原始字段。

![Wisp 多代理任务演示](readme-assets/readme-4th-demo.gif)

**沙盒代理运行：** 代理任务流程适用于较长的工作空间任务。Wisp 可以将任务分配给协调者、构建者和审查者角色，检查项目文件，进行有针对性的更改，运行检查，并为该次运行留下最终报告和产出物。

## 工作流程

| 您这边 | Wisp 会做什么 |
| --- | --- |
| 选中文本、选择上下文或绘制截图 | 只捕获您选中或启用的上下文 |
| 按下调用快捷键并选择操作或自定义提示词 | 根据您的提示词和所选上下文构建模型请求 |
| 发送请求 | 直接发送到您配置的模型提供商 |
| 等待答案 | 将回复流式显示到气泡中，并可选择自动 TTS 朗读 |
| 保存之后可能有用的信息 | 仅在启用记忆时将记忆保存在本地 |

示例流程：

| 您想做什么 | Wisp 会做什么 |
| --- | --- |
| 想解释选中的文本 | 在您按下通用快捷键并选择 `W`（这是什么？）或 `A`（简单解释）后读取选区，并在悬浮窗中解释 |
| 想重写一句话 | 读取选中的句子，应用您选择的重写操作，并可将结果贴回原处 |
| 想提出自己的问题 | 使用该调用者启用的上下文发送您的自定义提示词 |
| UI 元素或图像令人困惑 | 将 `Ctrl+Alt+Q` 截图发送给视觉模型 |
| 想用语音询问模型 | 转写您的 `F9` 语音请求，并作为模型查询发送 |
| 想在另一个应用中听写 | 将您的 `F8` 语音直接转写到当前聚焦的文本框中 |
## 快速开始

Wisp 有两种支持的启动方式。

### 选项 1：打包应用程序

如果您希望使用应用程序而无需克隆仓库或管理 Python 依赖项，请使用此选项。

1. 从 [GitHub Releases](https://github.com/SunnyLich/Python-AI-assistant-overlay/releases) 下载适用于您平台的最新资源。
2. 解压存档并启动打包应用程序。
3. 打开设置以添加您的模型提供商密钥、语音设置和首选快捷键。

| 操作系统 | 发布文件 | 启动方式 |
| --- | --- | --- |
| Windows | `Wisp-<tag>-windows-x64.zip` | `Wisp.exe` |
| macOS | `Wisp-<tag>-macos-<arch>.zip` | `Wisp.app` |
| Linux | `Wisp-<tag>-linux-x64.tar.gz` | `Wisp` |

### 选项 2：仓库启动器

如果您希望从源代码运行、开发 Wisp 或测试最新检出版本，请使用此选项。

克隆仓库：

```bash
git clone https://github.com/SunnyLich/Python-AI-assistant-overlay.git
cd Python-AI-assistant-overlay
```

然后使用适合您平台的仓库启动器启动 Wisp：

| 操作系统 | 启动方式 | 依赖来源 |
| --- | --- | --- |
| Windows | `Start Wisp.bat` | `requirements/requirements-windows.lock` |
| macOS | `Start Wisp.command` | `requirements/requirements-macos.lock` |
| Linux | `Start Wisp.sh` | `requirements/requirements-linux.lock` |

首次启动将配置 Python 环境并安装依赖项。后续启动将直接进入应用程序。

要构建自己的打包副本，请参阅 [构建 EXE](../docs/BUILDING_EXE.md) 了解本地构建命令和标记发布工作流。

要求：

- Python `3.12`，固定在 `.python-version`
- Windows 10/11、macOS 13+ 或支持 X11 的 Linux（用于完整的快捷键/截图路径）
- 至少配置一个 LLM 提供商密钥或本地兼容服务器

要查看完整运行时日志，请使用对应的调试启动器：

```text
Start Wisp Debug.bat
Start Wisp Debug.command
Start Wisp Debug.sh
```

## 配置

使用设置窗口进行常规设置。它可以存储提供商密钥、选择模型路由、配置语音、运行设置检查、解释缺失的可选功能，并显示不支持的模型功能的警告。提供方密钥和 OAuth token 会存储在**操作系统密钥链**中：Windows 凭据管理器、macOS 钥匙串，或 Linux 上的 Secret Service/KWallet，**而不是明文配置文件**。

### ChatGPT / Codex 与 Claude CLI

“应用”中的第一个设置**使用以下引擎运行对话**会同时决定悬浮查询和完整聊天窗口所用的引擎：

| 引擎 | 行为 |
| --- | --- |
| **Wisp** | 使用 Wisp 中配置的 LLM 提供商和模型。 |
| **ChatGPT** | 以 app-server 模式运行已安装的 Codex CLI，并使用您的 ChatGPT/Codex 账户。 |
| **Claude Agent** | 通过 Claude Code CLI 身份验证运行 Claude Agent SDK，并使用您的 Claude 账户。 |

选择 ChatGPT 或 Claude Agent 后，可以查看登录状态以及**登录**、**退出登录**和**刷新**操作。ChatGPT 模式需要 Codex CLI；Wisp 会把 Codex 登录状态和可恢复会话保存在隔离的本地配置档中，因此不会出现在您的个人 Codex 历史记录里。Claude Agent 会在可用时使用内置 SDK，并通过 Claude Code CLI 进行身份验证。

**对话发送至**控制会话连续性。选择 **Wisp** 时，每次请求都会发送完整的本地 Wisp 历史记录，但不会保留提供商的连续会话链接。选择 **ChatGPT** 或 **Claude Agent** 时，Wisp 只传输一次历史记录，保存返回的会话 ID，并在后续提示中恢复该提供商会话。Wisp 始终保留本地显示副本，并分别保存 Wisp、ChatGPT/Codex 和 Claude 的历史记录，因此切换引擎不会把消息追加到错误的对话中。

智能体工作时，Wisp 会流式显示回复，以及提供商公开的所有可见推理摘要、计划、工具启动、命令或文件状态和审批请求。私密的隐藏思维链不可用。悬浮图标下方的提供商徽章可打开下一轮的实时控制：模型、项目、快速模式、推理投入、可见摘要，以及三种权限模式之一——请求权限、允许在项目内更改或仅规划的只读模式。

项目可以明确选择，也可以根据已恢复会话、附件和文件上下文推断；最后会回退到 Wisp 的当前目录。更改项目会启动全新的提供商会话。智能体的写入权限仅限该项目；Codex 在其工作区沙盒中运行时也无法访问网络。

实时智能体会话是受支持的正常路径。实验性的转录记录拉取、推送和导出是本地兼容备用方案：拉取会读取 Codex 和 Claude 的 JSONL 历史记录，但不会联系提供商；推送需要确认，会创建完整备份，并只追加 Wisp 独有的轮次；导出需要确认，会创建新的转录记录，而不会覆盖现有提供商历史。请参阅[完整的实时智能体指南](../Wisp%20Website/Wisp%20Docs.html#live-agents)。

对于源代码构建和高级设置，`.env.example` 记录了可用的配置键。通常不需要手动编辑这些内容。

如需零成本和免费层模型选项，请参阅 [免费模型 API 来源](#免费模型-api-来源)。

## 默认快捷键

| 快捷键 | 操作 |
| --- | --- |
| Windows：`Ctrl+Q`；macOS/Linux：`Ctrl+Alt+Space` | 打开常规操作选择器 |
| Windows：`Ctrl+Shift+Q`；macOS/Linux：`Ctrl+Alt+Shift+Space` | 打开重写/粘贴操作选择器 |
| `Ctrl+Alt+Q` | 绘制屏幕截图用于视觉分析 |
| `Alt+Q` | 将当前选中内容添加到上下文缓冲区 |
| `Alt+W` | 清除上下文缓冲区 |
| `F9` 长按 | 录音、转录并查询 |
| `F8` 长按 | 直接口述到聚焦的文本字段 |
| `F7` | 朗读选中的文本 |
| `W` / `A` / `D` | 触发内置操作行 |
| `S` | 自定义提示词模式 |
| `Esc` | 取消选择器 |

每个调用者、快捷键、标签、提示词、上下文来源、粘贴回设置和 UI 尺寸均可在设置中配置。

## 插件

高度可扩展的 Wisp 会随着插件而变化：新功能、新工作流、新可能性。每个插件在 `addons/` 下的独立文件夹中，带有 `addon.toml` 清单文件，并在自己的**隔离 Python 宿主进程**中运行，因此一个插件的崩溃、慢速钩子或错误依赖项不会影响脑部工作器或其他插件。**功能是可选加入的**：插件只获取其清单声明的内容，缺少权限的请求会被拒绝。需要第三方包的插件会获得一个专用虚拟环境，在运行前需要您的批准。

插件可以在多个点接入 Wisp：

- **上下文** — 在查询发送前读取或重写提示词和上下文。
- **工具** — 注册模型可在回答过程中调用的模型可调用工具。
- **响应** — 观察已完成的响应以进行记录、保存或转发。
- **操作和快捷键** — 添加自己的操作行和带自定义提示词的全局快捷键。
- **UI** — 贡献托盘操作、设置字段和通知。
- **LLM 操作** — 从钩子或快捷键运行自己的受限模型调用。

**插件能做什么：** 因为插件可以注入上下文、暴露工具并对响应做出反应，功能范围很广。以下是一些示例及其使用的钩子：

| 您想要... | 钩子 | 清单需要 |
| --- | --- | --- |
| 自动将 git diff、日历或开放工单拉入提示词 | 上下文 (`before_query`) | `query = "modify"` |
| 给模型一个工具来搜索内部 wiki、查询数据库、调用天气或股票 API、或切换智能家居设备 | 工具 (`get_tools`) | `tools = true`（加上 `[dependencies]` 用于任何包） |
| 在合规要求下对出站敏感上下文进行脱敏或标记 | 上下文 (`before_query`) | `query = "modify"` |
| 将每个答案追加到日记或推送到 Notion 或 Slack | 响应 (`after_response`) | `response = "read"` |
| 添加一个带有自己提示词的"用我们的风格重写"操作 | 操作和快捷键 | `[[intents]]` / `[[hotkeys]]`，`hotkeys = true` |

只要您能用 Python 编写它并且它适合上述某个钩子点，就可以将其连接到您已经使用的同一个快捷键驱动悬浮窗中。

## MCP 客户端与服务器

### MCP 客户端：在 Wisp 中使用外部服务器

Wisp 内置了一个充当 MCP 客户端的 **MCP 桥接** 插件（`addons/mcp_bridge`）：在它的 `servers.json` 中列出任意 [Model Context Protocol](https://modelcontextprotocol.io) 服务器，Wisp 就会将这些服务器的整套工具作为 Wisp 工具暴露给模型。这样悬浮窗无需离开桌面工作流即可使用外部 MCP 能力。请参阅 [插件指南](../addons/README.md) 了解完整的清单和钩子合约，或 [Wisp 文档网站](../Wisp%20Website/Wisp%20Docs.html) 中的**插件**页面。

### MCP 服务器：Wisp 上下文服务器

Wisp 还内置了一个名为 **Wisp Context Server** 的本地 **MCP stdio 服务器**。受信任的 MCP 客户端（如 Claude Desktop、Cursor 和 Codex）可以启动它以读取实时桌面上下文；Wisp 应用本身无需保持运行。

它提供五个只读工具：

- `get_selected_text`：当前在桌面上选中的文本。
- `get_clipboard`：剪贴板文本。
- `get_active_window`：活动应用、窗口标题，以及可用时的浏览器 URL。
- `read_browser_page`：可见浏览器页面的文本。
- `take_screen_snip`：主显示器的截图。

### 连接客户端

启动一次 Wisp，然后将 `addons/mcp_bridge/claude_config_snippet.json` 中的 `mcpServers` 条目复制到 MCP 客户端配置中。Wisp 会使用其自身 Python 解释器和 `addons/mcp_bridge/context_server.py` 的正确本地路径生成此配置片段；请勿替换为系统 Python。有关平台说明和故障排除，请参阅 [MCP Bridge 服务器设置指南](../addons/mcp_bridge/README.md)。

只应向受信任的客户端注册该服务器：工具结果可能包含所选文本、剪贴板内容、浏览器内容和桌面截图。

## 隐私与控制

Wisp 被设计为本地桌面助手。**存储保留在您的机器上**，请求直接发送到您配置的模型提供商或本地服务器。

- **本地数据保持本地**：设置、聊天记录、记忆、隐私报告和配置存储在您的机器上。
- **密钥存放在操作系统密钥链**：提供方密钥和 OAuth token 保存在 Windows、macOS 或 Linux 桌面内置的安全密码存储区中。
- **请求直达**：模型请求直接从您的机器发送到您配置的提供商或本地服务器。
- **发送内容由您决定**：您配置的模型提供商只接收您发送的提示词和为该调用者选择或启用的上下文来源。
- **预览仅在本地进行**：Wisp 可能会在本地检查可用上下文以显示 token 估算、可用性和隐私脱敏计数，然后再发送。预览来源不会将其发送到模型提供商或保存为聊天/记忆。
- **外部聊天同步保留在本地**：拉取操作为只读，且绝不会联系提供商。实验性的推送和导出操作需要确认；推送会创建备份并追加到现有转录记录，导出则会创建新转录记录，而不会覆盖提供商历史。
- **上下文按快捷键配置文件控制**：环境应用上下文、剪贴板、文档、浏览器页面、GitHub 上下文、记忆、工具和截图均可按需启用、禁用或路由。
- **隐私模式**：保持隐私优先的设置检查和警告行为，包括在发送敏感上下文之前的脱敏状态。
- **默认关闭**：可选的语音、文档阅读、浏览器内容、截图、GitHub Copilot 和插件在配置之前保持不活跃。
- **不会意外联网**：只有在您配置和使用这些功能时，才会联系云端 TTS、模型提供商、兼容服务器或 GitHub Copilot。
- **插件隔离运行**：插件在隔离的 Python 宿主进程中运行，必须声明它们需要的功能。
- **轻量设置检查**：除非该功能已启用，否则不会导入繁重的提供商、音频或 STT 堆栈。

### 高级隐私模式

在**设置 → 应用 → 隐私模式**中选择三个互斥模式之一：**关闭**、**内置**（默认）或**高级**。内置模式使用本地模式匹配来检测凭据、令牌、付款信息及其他结构化机密。高级模式会保留这些规则，并加入可选的 [OpenAI Privacy Filter](https://openai.com/index/introducing-openai-privacy-filter/)；该模型完全在您的电脑上运行，可结合上下文检测姓名、地址、电子邮件地址、电话号码、私人 URL 和日期、账号及机密。

高级模型为可选下载，大小约 2.8 GB，此外还会安装其专用本地运行环境。Wisp 启动时或您启用高级模式后，会在后台将模型加载到内存并进行预热。在 CPU 上，预热可能需要数十秒。如果您在预热完成前发送请求，该请求会等待；后续扫描会复用已加载的模型，因此速度更快。Wisp 会将检测到的片段替换为 `[PERSON_1]` 等稳定占位符，可在发送前显示审查界面，并再次检查脱敏后的文本。如果高级模型不可用、检测失败或仍有敏感文本残留，Wisp 会阻止向云端发送。

隐私过滤可以降低意外泄露的风险，但不能保证匿名化或符合监管要求。

## 平台状态

| 平台 | 状态 |
| --- | --- |
| Windows 10+ | 支持 |
| macOS 13+ | 支持* |
| Linux X11 | 支持 |
| Linux Wayland | 开发中 - 正在推进 Wayland 支持 |

*此应用只在主要开发的两周期间于 macOS 上测试过，之后由于硬件访问受限，我无法继续测试。如果你在 macOS 上发现 bug，请在此仓库创建 issue，我会尽力修复。更好的是，如果你能提供解决方案，请创建 pull request。

## 反馈与平台帮助

欢迎提交错误报告，特别是依赖操作系统权限、窗口管理器、音频设备或显示服务器的桌面行为。如果您遇到崩溃、缺少权限、快捷键失效、捕获问题、粘贴失败或看起来有问题的设置检查警告，请提交一个包含您的操作系统版本、启动器、日志和触发该操作的问题报告。

日志可在 `build_logs/` 文件夹中找到。

我们目前正在推进 Linux Wayland 支持，特别需要帮助测试或改进它。也欢迎测试 macOS 支持；这些平台有最多的本地集成边缘情况，因此来自不同机器、桌面环境和权限状态的真实报告能让 Wisp 对所有人都更好。

如果您想支持这个项目和更广泛的使命，可以直接参与开发，或在[这里](https://buymeacoffee.com/sunnylich)捐助。

<details>
<summary>贡献者文档</summary>

- [开发者 README](../docs/DEVELOPER_README.md) — 设置、运行时入口点、检查和调试说明。
- [代码概览](../docs/OVERVIEW.md) — 子系统所有权和运行时边界。
- [插件指南](../addons/README.md) — 插件清单、权限、钩子、工具、快捷键和打包。
- [构建 EXE](../docs/BUILDING_EXE.md) — Windows 打包说明。

</details>



## 免费模型 API 来源

Wisp 是免费的，您也可以将模型费用保持在零。多个提供商提供真正的免费层、每月免费积分或无费用的限速访问。Wisp 通过其 OpenAI 兼容客户端访问其中大多数——少数有专用的 `LLM_PROVIDER` 值，其余通过 `custom` 端点工作，只需将 `CUSTOM_BASE_URL` 指向提供商的 OpenAI 兼容 URL。在**设置 → LLM** 中添加密钥。

| 提供商 | 免费内容 | 适合场景 |
| --- | --- | --- |
| OpenRouter | `:free` 模型——无积分时每分钟约 20 次、每天 50 次请求，一次性充值 $10 后每天 1,000 次；另有 `openrouter/free` 路由 | 最简单的"一个 API，多种模型"选项 |
| Google AI Studio | 支持地区的 Gemini API 免费层，有限速 | 多模态和长上下文工作，包括视觉 |
| Mistral | La Plateforme 上的免费实验层，有限速 | 欧洲 GDPR 友好模型和函数调用 |
| NVIDIA | 通过 NVIDIA API Catalog 免费访问许多开放模型 | 在快速托管端点上尝试多种开放权重模型 |
| GroqCloud | 有限速的免费层 | 对 Llama 和 Qwen 等开放模型的极快推理 |
| Cerebras Inference | Cerebras 托管模型的免费 API 层 | 极快的文本推理和原型设计 |
| GitHub Models | 每个 GitHub 账户的限速免费访问 | 原型设计、实验、GitHub 集成工作流 |
| Hugging Face Inference Providers | 每月免费积分（当前免费用户约 $0.10/月） | 通过一个生态系统尝试大量开放模型 |
| Cloudflare Workers AI | Workers 免费计划带每日免费配额 | 已在 Cloudflare 上的应用；无服务器 AI 端点 |
| Vercel AI Gateway | 免费层，符合条件的模型每月 $5 网关积分 | Next.js/Vercel 项目；统一的 OpenAI 兼容访问 |
| SambaNova Cloud | $5 免费 API 积分，无需信用卡 | 快速托管开放模型推理 |
| Puter.js | 前端 JS 访问多种模型，无需自己的 API 密钥 | 浏览器应用和演示；不是 Wisp 后端提供商 |
| [OmniRoute](https://github.com/diegosouzapw/OmniRoute)（本地网关） | 本地运行的开源路由器；把多个提供方账户和免费层聚合到一个 OpenAI 兼容端点后面，支持路由、故障转移和可选压缩 | 通过 Wisp 的自定义端点连接 OmniRoute：`LLM_PROVIDER=custom`、`CUSTOM_BASE_URL=http://localhost:20128/v1`，模型可用 `auto`，API 密钥来自 OmniRoute 仪表板 |
| 本地 — Ollama / LM Studio / vLLM | 自行运行模型时免费 | 隐私保护、无 token 计费、OpenAI 兼容本地端点 |

免费层有限速且经常变化，因此请至少添加一个备用路由，并避免将敏感上下文发送给可能用您的提示词进行训练的提供商（Wisp 的脱敏功能仍然适用）。完整的连接指南和注意事项，请参阅 [Wisp 文档网站](../Wisp%20Website/Wisp%20Docs.html) 中的**免费 API 来源**页面。

## 许可证

MIT
