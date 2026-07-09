# MCP Bridge

This addon speaks the [Model Context Protocol](https://modelcontextprotocol.io)
in **both directions**:

- **Client** (`__init__.py`): connects to MCP servers listed in `servers.json`
  and exposes their tools to Wisp's model. One addon imports an entire
  external toolkit.
- **Server** (`context_server.py`): the *Wisp Context Server* — lets external
  MCP clients (Claude Desktop, Cursor, ...) read your desktop context through
  Wisp's capture machinery.

## Client: using external MCP servers inside Wisp

Add entries to `servers.json`:

```json
{
  "servers": [
    {"name": "example", "command": "python", "args": ["example_server.py"]}
  ]
}
```

Each server's tools appear in Wisp as `mcp_<server>_<tool>`. `example_server.py`
is a bundled dependency-free server for smoke-testing the bridge.

## Server: giving other AI apps Wisp's desktop eyes

`context_server.py` is a standalone MCP stdio server. Wisp does not run it —
your MCP client launches it (Wisp doesn't even need to be open). It exposes:

| Tool | What it reads |
|---|---|
| `get_selected_text` | The text you have highlighted |
| `get_clipboard` | Your clipboard text |
| `get_active_window` | The window you're working in (title, app, URL) |
| `read_browser_page` | The page text of your visible browser window |
| `take_screen_snip` | A screenshot of your primary monitor |

### Setup

The addon writes a ready-to-paste snippet to `claude_config_snippet.json` in
this folder (also printed in the addon's log at startup). Paste its
`mcpServers` entry into your client's config — for Claude Desktop that is
`claude_desktop_config.json` (Settings → Developer → Edit Config). The snippet
uses **Wisp's own Python interpreter**; don't replace it with system Python,
the capture stack needs Wisp's installed dependencies.

The server logs a self-check to stderr on startup (visible in Claude Desktop's
MCP logs): interpreter, Wisp root, and any missing capture dependency, each
naming the tools it affects.

To turn the server off, disable "Enable context server" in this addon's
settings — the server then refuses to start.

### Platform notes

- **Windows**: full support, no permissions needed. Selection is read via UI
  Automation without touching the clipboard when possible.
- **macOS**: full support. Grant the *client* app (e.g. Claude Desktop) the
  permissions it prompts for — Automation/Accessibility for selection reading,
  Screen Recording for snips. macOS attributes permissions to the app that
  launched the server, not to Wisp.
- **Linux**: full support on X11 (selection reading is best-in-class there —
  the PRIMARY selection needs no focus and no clipboard). On Wayland, screen
  snips and some window queries degrade with a clear error message. Clipboard
  needs `xclip`, `xsel`, or `wl-clipboard`.

Because the *client* app has focus while it calls these tools, the server
never sends a copy keystroke when the client's own window is focused (it
would copy from the assistant's chat). If `get_selected_text` comes back
empty, copy the text and let the model use `get_clipboard`.

### Security

Anyone able to launch this script can read your selection, clipboard, browser
pages, and screen — the same things any program running under your user
account could read. Register it only with AI apps you trust, and remember that
whatever they read leaves your machine when the model call does.
