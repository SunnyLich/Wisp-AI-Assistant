# OpenWand chat experience audit

This audit records the behavior verified in code before the chat workspace is redesigned.

## Conversation identity and external history

- OpenWand conversations are stored locally in `chats/conversations.json` and have their own OpenWand IDs.
- The `codex` external-history adapter reads local Codex CLI JSONL files under the configured Codex home. It does not read ChatGPT web conversations or use a ChatGPT web thread ID.
- The `claude` adapter reads local Claude Code JSONL files. It does not connect to a Claude web conversation.
- Import and automatic import are pull operations. Automatic import scans while the OpenWand chat window is running and only considers source activity after the user enables it.
- An imported `external_source.session_id` identifies the local transcript that was read. It is not automatically used as the active agent-harness continuation session; harness continuation is stored separately in `harness_sessions`.
- New OpenWand turns are not written back by the chat UI. A guarded transcript-append helper exists in the storage module, but the UI deliberately exposes no push action.
- Export creates a new local Codex or Claude Code transcript file. It is an experimental compatibility operation, not an official API-created ChatGPT or Claude web conversation.

Therefore the supported UI states are:

- **Local OpenWand conversation**
- **Imported from local Codex history · pull-only**
- **Imported from local Claude Code history · pull-only**
- **Exported to a local provider transcript · pull-only updates**
- **Import unavailable**

The product must not describe these states as “synced with ChatGPT” or imply two-way synchronization.

## Imported fidelity

- Text user turns and final assistant text are imported.
- Basic source session IDs, timestamps, working directory, source path, and a file signature are retained.
- Codex tool events, reasoning events, images, attachments, citations, and most message metadata are not imported.
- Claude import keeps text blocks from the selected main chain but omits non-text blocks and sidechains.
- Consecutive same-role text records may be combined, so imported message boundaries are not guaranteed to be identical to the source.
- When a source transcript changes, OpenWand replaces the imported prefix and retains the locally appended OpenWand tail. Simultaneous edits can therefore produce a combined local view, not a conflict-resolved two-way conversation.

## Existing chat workspace coverage

Already present:

- Markdown/rich reply rendering, selectable text, copy controls, links, images, streaming states, cancellation paths, and error presentation.
- File picker and top-level drag-and-drop attachment ingestion.
- Local attachment persistence and generated-image rendering.
- Native-system move/resize requests from the custom window chrome.
- Conversation/project history, search, pinning, timestamps, and local persistence.

Incomplete or misleading before this work:

- The window recenters whenever it is shown and does not remember its geometry.
- The composer has a fixed height and does not grow with multiline input.
- Pasting an image into the text editor is not routed into the attachment pipeline.
- Pending attachments are summarized as names rather than shown as useful previews.
- Enter behavior is fixed rather than user-configurable.
- External-history controls use “sync with ChatGPT/Claude” wording even though they are local, pull-only imports.
- Conversation identity is conveyed mainly as a provider prefix instead of an explicit source/status explanation.
- Compact overlay and full workspace remain coupled to the same reply pipeline and need a clearer user-facing presentation policy.

## Contextual application integrations

True Word margin comments, Excel cell/range anchoring, browser DOM anchoring, page reflow, and inline Accept/Reject require application-specific integrations (Office add-ins, browser extension APIs, or accessibility adapters). A desktop overlay can approximate a side panel, but it cannot reliably behave like a native document comment or browser side panel by itself.

## Implemented in the workspace pass

- The main chat window restores its last normal geometry and maximized state instead of recentering every time it opens. Its existing native system move/resize requests remain available for Windows snap behavior.
- The composer grows from a compact resting height to a capped multiline height, then scrolls internally.
- Pasted and dropped images/files enter the same pending-attachment path as the file picker. Pending images receive a thumbnail and pending items can be cleared before sending.
- Enter-to-send is configurable. The alternate mode uses Ctrl+Enter to send and leaves Enter for new lines; the composer hint always reflects the current mode.
- The workspace `⋮` menu lives inside the bottom composer card and contains Enter behavior and local-history controls.
- Reader-controlled scrolling is latched during streaming. Scrolling upward stops automatic bottom-following and exposes a **Jump to latest** control; new chunks do not override the reader's position.
- Import implementation details are kept out of the conversation header. Local transcript actions use the recognizable product names `Codex` and `Claude Code` only where the user deliberately opens an import/export menu.
- The retired Formatted Replies add-on is ignored by the add-on manager and is no longer bundled. Rich Markdown/HTML/CSS rendering remains part of the default chat workspace and does not require a second model call.

## Remaining product decisions and integration work

- Choose a desktop presentation policy for compact answers: no persistent launcher, an optional edge/tab affordance, a transient answer card, or notification-only. This should be a user preference rather than a new always-visible surface.
- Keep full Chat as the durable reading/history workspace. Compact surfaces should be resumable views of the same conversation rather than independent transcript stores.
- Build native context surfaces as separate adapters: Office add-in tasks for Word/Excel and a browser-extension task for DOM anchoring, side-panel behavior, reflow, page/video context, and inline actions.
- If real ChatGPT-web continuity is still a requirement, define and implement an officially supported identity and transport layer first. The current local JSONL adapters cannot provide that contract.
