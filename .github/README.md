<div align="center">

<img src="../assets/doll/idle.png" width="112" alt="Wisp icon" />

# Wisp

**Many tasks are better handled with AI assistance than complete delegation. Wisp makes that collaboration faster, more user-friendly, and more customizable as an open-source co-working platform.**

Wisp gives you hotkey-driven AI that can read your selection, clipboard, app, browser, documents, or screen snip while you stay where you are. Press a hotkey, choose an action, and stream the answer into a small overlay or at your input cursor. It is completely open-source, cross-platform, extensible, permissively licensed, and 100% Python, so it stays easy to tinker with: the kind of openness that even billion-dollar products like Microsoft Copilot still do not offer.

[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-333333?style=flat-square)](#platform-status)
[![Python](https://img.shields.io/badge/python-3.12-3572A5?style=flat-square)](#quick-start)
[![Local first](https://img.shields.io/badge/local--first-context%20and%20memory-4B8F8C?style=flat-square)](#privacy-and-control)
[![License](https://img.shields.io/badge/license-MIT-7C3AED?style=flat-square)](#license)

**Languages:** English | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [Français](README.fr.md) | [Español](README.es.md)

**Website:** [Wisp Docs](https://sunnylich.github.io/Wisp-AI-Assistant/)

[Quick start](#quick-start) | [What it does](#what-wisp-does) | [Demos](#demos) | [Configuration](#configuration) | [Free APIs](#free-model-api-sources) | [Privacy](#privacy-and-control)

![Wisp Ctrl+Q demo](readme-assets/readme-1st-demo.gif)

**Overlay query:** Press a hotkey, choose an action, and get a streamed answer without leaving the app you are already using.
</div>

---

## Known Issues

[Known issues](https://sunnylich.github.io/Wisp-AI-Assistant/#known-issues)

## What Wisp Does

Wisp is for the moments when opening a chat app would break your flow.

Highlight text, press the general hotkey, hit one action key, and Wisp asks your configured model with only the context sources you enabled. Replies stream into a compact bubble next to the floating icon. Configure TTS for read-aloud, or enable auto-speak replies if you want answers spoken as they arrive.

| Without Wisp | With Wisp |
| --- | --- |
| Copy text into a chat app, explain the context, wait, then paste the answer back | Press a hotkey and ask from the app you are already using |
| Retyping the instructions for recurring tasks every time | Save reusable actions with the context sources you want |
| Manually describe a browser page, document, or screenshot | Capture selection, clipboard, documents, browser pages, and screen snips |
| Turning every thought into a typed prompt | Hold a voice hotkey, speak, and send the transcribed request |
| Wearing yourself out reading wall after wall of text | Stream replies in the overlay or listen with TTS |
| Give an agent broad instructions and hope it touches the right files | Run scoped agent tasks with artifacts, review, and logs |
| Trust a closed assistant platform with your prompts, context, and memory | Keep data on your machine and send only the information and requests you choose to your model provider |

## Highlights

- **Overlay first** - a floating icon, action picker, and reply bubble stay on top without taking over your desktop.
- **Full chat window** - click the floating icon to open a persistent chat that remembers past conversations, keeps the context you captured in the overlay, and can expand a quick overlay reply into a longer back-and-forth.
- **Privacy by default** - Wisp has no hosted storage layer; data stays on your machine unless you send it to your chosen model, and privacy mode can warn or redact before sensitive context leaves.
- **Highly customizable** - every hotkey, action key, prompt, context source, paste-back behavior, model route, voice setting, and bubble dimension can be changed.
- **Approachable GUI** - Settings, setup checks, privacy reports, memory tools, and model warnings explain what is happening without requiring you to read the code.
- **Context capture** - Wisp can read selected text, clipboard text, focused UI, open documents, browser content, recent files, and optional screenshots, so it does not have to rely on screen grabs alone.
- **Voice in and out** - local STT via faster-whisper, plus on-device neural TTS (Kokoro, and GPT-SoVITS voice cloning) or cloud/compatible voices (Cartesia, ElevenLabs, OpenAI, any OpenAI-compatible server), with TTS and auto-spoken replies off by default.
- **Vision snips** - draw a region with `Ctrl+Alt+Q` and send the screenshot to a vision model.
- **Rewrite and paste** - use the rewrite hotkey to rewrite selected text with captured context and paste the result back into the active field.
- **Bring your own provider** - Groq, Anthropic, OpenAI, Google, DeepSeek, OpenRouter, Mistral, XAI, Together, Cerebras, Z.AI / GLM, NVIDIA, SambaNova, GitHub Models, Hugging Face, Chutes, Vercel, Fireworks, Cohere, AI21, Nebius, custom OpenAI-compatible servers, GitHub Copilot, and more.
- **Local memory** - optional short-term and long-term memory are stored locally, with a viewer for editing or deleting facts.
- **Addons and MCP** - extend Wisp with hooks, tray actions, settings, model-callable tools, actions, and hotkeys; a bundled MCP bridge turns any Model Context Protocol server into tools the model can call.
- **Agent tasks** - a sandboxed task framework exists for longer jobs that need decomposition, review, and artifacts.

## Demos

![Wisp Ctrl+Alt+Q screen snip demo](readme-assets/readme-2nd-demo.gif)

**Vision snip:** The snip flow is for cases where visual context matters. `Ctrl+Alt+Q` lets you draw a region, send just that crop to a vision model, and keep the answer in the overlay instead of switching apps.

![Wisp context-aware rewrite demo](readme-assets/readme-3rd-demo.gif)

**Context-aware rewrite:** Wisp can gather useful app context without taking a screenshot, so the model knows what you are working on. Then the rewrite hotkey rewrites only the selected text and targets paste-back at the original field captured when you pressed the hotkey.

![Wisp multi-agent task demo](readme-assets/readme-4th-demo.gif)

**Sandboxed agent run:** The agent task flow is for longer workspace jobs. Wisp can split a task across coordinator, builder, and reviewer roles, inspect project files, make a focused change, run checks, and leave behind a final report and artifacts for the run.

## Workflow

| Your side | What Wisp does |
| --- | --- |
| Highlight text, choose context, or draw a snip | Captures only the selected or enabled context |
| Press the caller hotkey and choose an action or custom prompt | Builds the model request from your prompt and chosen context |
| Send the request | Sends it directly to your configured model provider |
| Wait for the answer | Streams the reply into a bubble, with optional auto-speak TTS |
| Keep useful information for later | Stores memory locally only when memory is enabled |

Example flows:

| What you want | What Wisp does |
| --- | --- |
| You want an explanation of selected text | Reads the selection after you press the general hotkey and choose `W` (`What is this?`) or `A` (`Explain simply`), then explains it in the overlay |
| You want to rewrite a sentence | Reads the selected sentence, applies the rewrite action you choose, and can paste the result back |
| You need to ask your own question | Sends your custom prompt with whatever context is enabled for that caller |
| A UI element or image is confusing | Sends the `Ctrl+Alt+Q` screen snip to a vision model |
| You want to ask the model by voice | Transcribes your `F9` voice request and sends it as a model query |
| You want to dictate into another app | Transcribes your `F8` speech directly into the focused text field |
## Quick Start

There are two supported ways to start Wisp.

### Option 1: Packaged App

Use this if you want the app without cloning the repo or managing Python dependencies.

1. Download the latest asset for your platform from [GitHub Releases](https://github.com/SunnyLich/Python-AI-assistant-overlay/releases).
2. Unpack the archive and start the packaged app.
3. Open Settings to add your model provider keys, voice settings, and preferred hotkeys.

| OS | Release artifact | Start with |
| --- | --- | --- |
| Windows | `Wisp-<tag>-windows-x64.zip` | `Wisp.exe` |
| macOS | `Wisp-<tag>-macos-<arch>.zip` | `Wisp.app` |
| Linux | `Wisp-<tag>-linux-x64.tar.gz` | `Wisp` |

Release pages include `SHA256SUMS.txt` so you can verify the archive after
download. On Windows, run:

```powershell
Get-FileHash .\Wisp-<tag>-windows-x64.zip -Algorithm SHA256
```

Compare the hash with the matching line in `SHA256SUMS.txt`. Windows may still
show a SmartScreen warning for unsigned builds from an independent open-source
publisher; the checksum confirms the file matches the release asset uploaded by
the project.

### Option 2: Repo Launcher

Use this if you want to run from source, develop Wisp, or test the latest checkout.

Clone the repo:

```bash
git clone https://github.com/SunnyLich/Python-AI-assistant-overlay.git
cd Python-AI-assistant-overlay
```

Then start Wisp with the repo launcher for your platform:

| OS | Start with | Dependency source |
| --- | --- | --- |
| Windows | `Start Wisp.bat` | `requirements/requirements-windows.lock` |
| macOS | `Start Wisp.command` | `requirements/requirements-macos.lock` |
| Linux | `Start Wisp.sh` | `requirements/requirements-linux.lock` |

The first launch provisions the Python environment and installs dependencies. Later launches go straight into the app.

To build your own packaged copy, see [Building an EXE](../docs/BUILDING_EXE.md) for local build commands and the tagged-release workflow.

Requirements:

- Python `3.12`, pinned in `.python-version`
- Windows 10/11, macOS 13+, or Linux with X11 for the full hotkey/screenshot path
- At least one configured LLM provider key or local compatible server

For full runtime logs, use the matching debug launcher:

```text
Start Wisp Debug.bat
Start Wisp Debug.command
Start Wisp Debug.sh
```

## Configuration

Use the Settings window for normal setup. It can store provider keys, choose model routes, configure voice, run a setup check, explain missing optional features, and show warnings for unsupported model capabilities. Provider keys and OAuth tokens are saved in the **OS keychain**: Windows Credential Manager, macOS Keychain, or Secret Service/KWallet on Linux, **not a plain-text config file**.

For source builds and advanced setups, `.env.example` documents the available configuration keys. You usually do not need to edit those by hand.

For no-cost and free-tier model options, see [Free Model API Sources](#free-model-api-sources).

## Default Hotkeys

| Hotkey | Action |
| --- | --- |
| `Ctrl+Q` on Windows, `Ctrl+Alt+Space` on macOS/Linux | Open the general action picker |
| `Ctrl+Shift+Q` on Windows, `Ctrl+Alt+Shift+Space` on macOS/Linux | Open the rewrite/paste action picker |
| `Ctrl+Alt+Q` | Draw a screen snip for vision |
| `Alt+Q` | Add the current selection to the context buffer |
| `Alt+W` | Clear the context buffer |
| `F7` | Read the selected text aloud |
| `F9` hold | Record voice, transcribe, and query |
| `F8` hold | Direct dictation into the focused text field |
| `W` / `A` / `D` | Trigger built-in action rows |
| `S` | Custom prompt mode |
| `Esc` | Cancel the picker |

Every caller, hotkey, label, prompt, context source, paste-back setting, and UI dimension is configurable from Settings.

## Addons

Deeply extensible, Wisp transforms with addons - new features, new workflows, new possibilities. Each addon lives in its own folder under `addons/` with an `addon.toml` manifest, and runs in its own **isolated Python host process**, so a crash, a slow hook, or a bad dependency in one addon cannot take down the brain worker or any other addon. **Capabilities are opt-in:** an addon only gets what its manifest declares, and missing permissions are denied. Addons that need third-party packages get a dedicated virtual environment that you approve before it runs.

In portable packaged builds, Wisp creates an `addons` folder next to `Wisp.exe`
when that folder is writable. If the app is installed somewhere read-only, use
**Addon Manager -> Open addons folder** to open the fallback user-writable addon
directory.

An addon can hook into Wisp at several points:

- **Context** - read or rewrite the prompt and context before a query is sent.
- **Tools** - register model-callable tools the model can invoke mid-answer.
- **Responses** - observe completed responses to log, save, or forward them.
- **Actions and hotkeys** - add its own action rows and global hotkeys with custom prompts.
- **UI** - contribute tray actions, settings fields, and notifications.
- **LLM actions** - run its own capped model calls from a hook or hotkey.

**What addons can do:** because an addon can inject context, expose tools, and react to responses, the surface is broad. A few examples, and the hook each one uses:

| You want to... | Hook | Manifest needs |
| --- | --- | --- |
| Pull your git diff, calendar, or an open ticket into the prompt automatically | Context (`before_query`) | `query = "modify"` |
| Give the model a tool to search an internal wiki, query a database, hit a weather or stock API, or toggle a smart-home device | Tools (`get_tools`) | `tools = true` (plus `[dependencies]` for any packages) |
| Redact or tag sensitive context on its way out for compliance | Context (`before_query`) | `query = "modify"` |
| Append every answer to a daily journal, or push it to Notion or Slack | Responses (`after_response`) | `response = "read"` |
| Add a one-key "rewrite this in our house style" action backed by its own prompt | Actions and hotkeys | `[[intents]]` / `[[hotkeys]]`, `hotkeys = true` |

If you can write it in Python and it fits one of the hook points above, you can wire it into the same hotkey-driven overlay you already use.

## MCP Client and Server

### MCP Client: use external servers inside Wisp

Wisp ships with an **MCP bridge** addon (`addons/mcp_bridge`) that acts as an MCP client: list any [Model Context Protocol](https://modelcontextprotocol.io) servers in its `servers.json` and Wisp exposes their whole toolkit to its model as Wisp tools. This lets the overlay use external MCP capabilities without leaving the desktop workflow. See the [Addon guide](../addons/README.md) for the full manifest and hook contract, or the **Add-ons** page in the [Wisp documentation site](../Wisp%20Website/Wisp%20Docs.html).

### MCP Server: Wisp Context Server

Wisp also ships a local **MCP stdio server** called **Wisp Context Server**. Trusted MCP clients such as Claude Desktop, Cursor, and Codex can launch it to read live desktop context; the Wisp app itself does not need to stay open.

It provides five read-only tools:

- `get_selected_text` — the text currently selected on the desktop.
- `get_clipboard` — clipboard text.
- `get_active_window` — the active app, window title, and browser URL when available.
- `read_browser_page` — text from the visible browser page.
- `take_screen_snip` — a screenshot of the primary monitor.

### Connect a client

Start Wisp once, then copy the `mcpServers` entry from `addons/mcp_bridge/claude_config_snippet.json` into your MCP client's configuration. Wisp generates this snippet with the correct local path to its own Python interpreter and `addons/mcp_bridge/context_server.py`; do not substitute system Python. See the [MCP Bridge server setup guide](../addons/mcp_bridge/README.md) for platform notes and troubleshooting.

Only register the server with clients you trust: tool results can contain selected text, clipboard content, browser content, and screenshots from your desktop.

## Privacy And Control

Wisp is designed as a local desktop assistant. **Storage stays on your machine**, and requests go directly to the model provider or local server you configure.

- **Local data stays local:** settings, chats, memory, privacy reports, and configuration are stored on your machine.
- **Keys in your OS keychain:** provider keys and OAuth tokens are stored in the secure password store built into Windows, macOS, or your Linux desktop.
- **Direct requests:** model requests go straight from your machine to the provider or local server you configured.
- **You choose what is sent:** your configured model provider receives only the prompt you send and the context sources selected or enabled for that caller.
- **Previews stay local:** Wisp may inspect available context locally to show token estimates, availability, and privacy redaction counts before you send. Previewing a source does not send it to the model provider or save it as chat/memory.
- **Per-hotkey context control:** ambient app context, clipboard, documents, browser pages, GitHub context, memory, and screenshots can each be disabled, attached up front, or exposed as model-fetchable context where supported.
- **Separate tool permissions:** allowed tools are separate from context controls and cover the remaining model-callable capabilities, such as local file tools and add-on tools.
- **Privacy mode:** privacy-first setup checks and warning behavior stay enabled, including redaction status before sensitive context is sent.
- **Off until configured:** optional voice, document reading, browser content, screenshots, GitHub Copilot, and addons stay inactive until you set them up.
- **No surprise connections:** cloud TTS, model providers, compatible servers, or GitHub Copilot are contacted only when you configure and use those features.
- **Sandboxed addons:** addons run in isolated Python host processes and must declare the capabilities they need.
- **Lean setup checks:** heavy provider, audio, and STT stacks are not imported unless the feature is enabled.

## Platform Status

| Platform | Status |
| --- | --- |
| Windows 10+ | Supported |
| macOS 13+ | Supported* |
| Linux X11 | Supported |
| Linux Wayland | In progress - Wayland support is currently being worked on |

*This application was only tested on macOS during two weeks of major development, and I cannot test it afterward due to limited hardware access. If you find bugs on macOS, please create an issue on this repo and I will try my best to fix them. Better yet, if you can provide a solution, please create a pull request.

## Feedback And Platform Help

Bug reports are welcome, especially for desktop behaviors that depend on OS permissions, window managers, audio devices, or display servers. If you hit a crash, missing permission, broken hotkey, capture issue, paste-back failure, or setup-check warning that looks wrong, please open an issue with your OS version, launcher, logs, and the action that triggered it.

Logs can be found under the `build_logs/` folder.

We are currently working on Linux Wayland support, and help testing or improving it is especially useful. macOS support testing is also welcome; these platforms have the most native integration edge cases, so real-world reports from different machines, desktop environments, and permission states make Wisp better for everyone.

If you want to support this project and the broader mission, you can contribute to the development directly or make a donation [here](https://buymeacoffee.com/sunnylich).

<details>
<summary>Contributor docs</summary>

- [Developer README](../docs/DEVELOPER_README.md) - setup, runtime entrypoints, checks, and debugging notes.
- [Code overview](../docs/OVERVIEW.md) - subsystem ownership and runtime boundaries.
- [Addon guide](../addons/README.md) - addon manifest, permissions, hooks, tools, hotkeys, and packaging.
- [Building an EXE](../docs/BUILDING_EXE.md) - Windows packaging notes.

</details>



## Free Model API Sources

Wisp is free, and you can keep your model costs at zero too. Several providers offer free-tier examples, free monthly credits, or no-cost rate-limited access. Wisp reaches most of them through its OpenAI-compatible client — a few have a dedicated provider value, and the rest work through the custom endpoint. Choose the provider and add the key in **Settings → LLM**.

These examples were reviewed on **June 27, 2026** against provider docs, Z.AI docs, npm metadata, and OpenRouter's free LLM API comparison; OmniRoute was checked against its README on **July 1, 2026**. Free tiers change often, so confirm current limits, credit amounts, and eligibility on the provider's own pricing page before you depend on them.

| Provider | What's free | Good for |
| --- | --- | --- |
| OpenRouter | `:free` models — ~20 req/min and 50/day with no credits, 1,000/day after a one-time $10 top-up; plus an `openrouter/free` router | Easiest "one API, many models" option |
| Google AI Studio | Gemini API free tier in supported regions, with rate limits | Multimodal and long-context work, including vision |
| Mistral | Free experimental tier on La Plateforme, rate-limited | European, GDPR-friendly models and function calling |
| NVIDIA | Free API access to many open models via the NVIDIA API Catalog | Trying many open-weight models on fast hosted endpoints; Wisp can use `LLM_PROVIDER=nvidia` |
| GroqCloud | Free tier with rate limits | Very fast inference for open models like Llama and Qwen |
| Cerebras Inference | Free API tier for Cerebras-hosted models | Extremely fast text inference and prototyping |
| GitHub Models | Rate-limited no-cost access for every GitHub account | Prototyping, experiments, GitHub-integrated workflows; Wisp can use `LLM_PROVIDER=github_models` |
| Cloudflare Workers AI | Workers free plan with a free daily allocation | Apps already on Cloudflare; use Wisp's custom endpoint because the URL includes your account ID |
| Z.AI / GLM | GLM model access through Z.AI's OpenAI-compatible API, plus agent-specific free access in tools such as FreeBuff; free API quota details change by platform | Open-source coding and agent workflows; Wisp can use `LLM_PROVIDER=zai` with models like `glm-4.7-flash` |
| Cohere | Trial API key access to Command R+ with request caps; non-commercial use only | RAG and retrieval-focused experiments; Wisp can use `LLM_PROVIDER=cohere` |
| Hugging Face Inference Providers | Community and small-credit access varies by provider and account type | Trying lots of open models through one ecosystem; Wisp can use `LLM_PROVIDER=huggingface` |
| Chutes | Community access to open-source models, subject to availability and rate limits | Testing OpenAI-compatible hosted OSS endpoints; Wisp can use `LLM_PROVIDER=chutes` |
| Vercel AI Gateway | Free gateway credit for eligible models, with provider-dependent backend terms | Next.js/Vercel projects; Wisp can use `LLM_PROVIDER=vercel` |
| SambaNova Cloud | Trial API credit examples, often around $5 | Fast hosted open-model inference; Wisp can use `LLM_PROVIDER=sambanova` |
| DeepSeek / Fireworks / Nebius / AI21 | Trial credits or token grants for evaluation | Short comparison runs before choosing a paid or permanent route; Wisp has native provider values for each |
| Baseten | Trial or evaluation credits for hosted inference | Use Wisp's custom endpoint because Baseten URLs are deployment-specific |
| Puter.js | Front-end JS access to many models with no API key of your own | Browser apps and demos; not a Wisp backend provider |
| [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) (self-hosted) | Open-source MIT gateway you run yourself; pools the free tiers of ~16 providers (Google, Groq, Cerebras, Mistral, OpenRouter, GitHub Models, and more) behind one OpenAI-compatible endpoint with automatic failover | One token for many free backends; point Wisp's custom endpoint at your deployment (`LLM_PROVIDER=custom`, `CUSTOM_BASE_URL=http://localhost:3001/v1`) |
| [OmniRoute](https://github.com/diegosouzapw/OmniRoute) (local gateway) | Open-source router you run locally; aggregates many provider accounts and free tiers behind one OpenAI-compatible endpoint with routing, fallback, and optional compression | Route Wisp through OmniRoute by using the custom endpoint (`LLM_PROVIDER=custom`, `CUSTOM_BASE_URL=http://localhost:20128/v1`, model such as `auto`, and the API key from OmniRoute's dashboard) |
| Local — Ollama / LM Studio / vLLM | Free whenever you run the model yourself | Privacy, no token billing, OpenAI-compatible local endpoints |

Free tiers are rate-limited and change often, so add at least one fallback route, avoid sending sensitive context to providers that may train on your prompts, and treat trial, non-commercial, or agent-specific offers as evaluation-only unless the provider says otherwise. For the full how-to-connect guide and caveats, see the **Free API sources** page in the [Wisp documentation site](../Wisp%20Website/Wisp%20Docs.html).

## License

MIT
