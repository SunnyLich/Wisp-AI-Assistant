# OS Test Checklist

This checklist is organized by the OS behavior each function owns. For every
item, prefer a fast unit test with platform and dependency mocks first, then add
a real-host smoke test where the function touches OS APIs, permissions, audio,
window focus, clipboard, screenshots, or global hotkeys.

## Cross-platform baseline

These should pass on Windows, macOS, and Linux with no real native APIs.

### Configuration and environment

- [ ] `config._context_mode`: accepts valid modes and falls back on invalid input.
- [ ] `config._load_voice_caller`: mirrors general caller defaults and applies voice overrides.
- [ ] `config._load_caller_rows`: loads multiple caller blocks, intent rows, screenshots, memory, and tool modes.
- [ ] `config._load_config`: reloads all env-backed settings without stale globals.
- [ ] `config.get_system_prompt`: returns prompt text from configured or default source.
- [ ] `config.reload`: refreshes config and secret-cache dependent values.
- [ ] `core.system.env_utils.normalize_screenshot_mode`: normalizes legacy and invalid screenshot values.
- [ ] `core.system.env_utils.env_screenshot_mode`: reads env-backed screenshot modes.
- [ ] `core.system.env_utils.parse_tool_modes`: drops invalid tool override entries.
- [ ] `core.system.env_utils.format_tool_modes`: round-trips parsed modes.
- [ ] `core.system.env_utils.env_bool`: handles common true/false aliases.
- [ ] `core.system.env_utils.env_int`: falls back for missing or invalid ints.
- [ ] `core.system.env_utils.env_float`: falls back for missing or invalid floats.
- [ ] `core.system.env_utils.read_env_file`: preserves quoted values, comments, blanks, and duplicate behavior.
- [ ] `core.system.env_utils.format_env_value`: quotes special values safely.
- [ ] `core.system.env_utils.write_env_file`: updates, removes, and preserves unrelated keys.
- [ ] `core.system.paths._user_data_dir`: resolves per-OS writable app data roots.
- [ ] `core.system.paths._bundle_root`: resolves frozen and source layouts.
- [ ] `core.system.paths._repo_root`: respects `WISP_REPO_ROOT`.

### Query, context assembly, and prompt routing

- [ ] `core.query_pipeline.GenerationCounter.next/current/is_current`: is thread-safe and monotonic.
- [ ] `core.query_pipeline.build_context`: applies ordering for selected text, buffered context, clipboard, dropped files, active document, screenshots, and ambient text.
- [ ] `core.context_fetcher._redact`: removes sensitive env/API/token-looking text.
- [ ] `core.context_fetcher.get_temp_path`: creates/reuses the snapshot path safely.
- [ ] `core.context_fetcher.fetch_and_save`: writes complete snapshots and honors hotkey-time active window data.
- [ ] `core.context_fetcher.load_latest`: handles missing, corrupt, and current snapshot files.
- [ ] `core.context_fetcher.format_context_for_prompt`: formats each source with useful labels and no empty sections.
- [ ] `core.context_fetcher._snapshot_to_dict`: serializes dataclasses and nested values.
- [ ] `core.context_fetcher._persist`: writes atomically enough for repeated hotkey use.
- [ ] `core.context_fetcher.fetch_browser_content_for_tool`: truncates and handles fetch failures.
- [ ] `core.context_fetcher.fetch_browser_content_for_window`: prefers saved URL/handle/app routing.
- [ ] `core.llm_clients.client.read_document_file`: reads supported document types and enforces max chars.
- [ ] `core.llm_clients.client.read_active_document_for_context_with_debug`: returns both text and diagnostic reason.
- [ ] `core.llm_clients.client.read_active_document_for_context`: degrades to empty string on failures.
- [ ] `core.llm_clients.client._execute_get_context`: respects source allowlists.
- [ ] `core.llm_clients.client._execute_memory_search`: requires opt-in memory access.
- [ ] `core.llm_clients.client._execute_git_status/_execute_git_diff`: only run read-only git commands.

### LLM providers, tools, and fallbacks

- [ ] `core.llm_clients.client.screenshot_capability_warnings`: warns for unsupported model/provider/mode combinations.
- [ ] `core.llm_clients.client.tool_capability_warnings`: warns when tool mode conflicts with provider capability.
- [ ] `core.llm_clients.client.subscription_auth_warnings`: reports ChatGPT/Copilot auth requirements.
- [ ] `core.llm_clients.client._get_tool_schemas/_get_openai_tool_schemas`: filters by prompt, pinning, and allowed tools.
- [ ] `core.llm_clients.client._append_pinned_tool_schemas`: adds only valid pinned schemas.
- [ ] `core.llm_clients.client._execute_model_tool`: enforces allowed tool names and returns errors as text.
- [ ] `core.llm_clients.client._inject_frontloaded_tool_context`: frontloads browser/git/context when tools are disabled.
- [ ] `core.llm_clients.client._capture_screen_b64`: uses provided image first and native capture second.
- [ ] `core.llm_clients.client._apply_sampling`: strips unsupported sampling parameters.
- [ ] `core.llm_clients.client._apply_max_output`: chooses `max_tokens` vs `max_completion_tokens`.
- [ ] `core.llm_clients.client._recover_openai_compat_kwargs`: retries without rejected parameters.
- [ ] `core.llm_clients.client._record_route_error_capabilities`: records stream/tool/json/image unsupported facts.
- [ ] `core.llm_clients.client._without_unsupported_parameter`: drops only the rejected parameter.
- [ ] `core.llm_clients.client.reset_clients`: clears cached provider clients and route state.
- [ ] `core.llm_clients.client.list_models`: handles provider-specific list models and errors.
- [ ] `core.llm_clients.client.test_route_connection`: validates standard, vision, fallback, custom, and subscription routes.
- [ ] `core.llm_clients.client.stream_response`: assembles context, tools, screenshot policy, memory, and fallback routing.
- [ ] `core.llm_clients.client._stream_with_fallbacks`: skips cooling routes and returns clear exhaustion errors.
- [ ] `core.llm_clients.client._stream_single_response_route`: dispatches to the right provider implementation.
- [ ] `core.llm_clients.client.stream_rewrite`: builds rewrite prompts and fallback route behavior.
- [ ] `core.llm_clients.client.stream_response_with_history`: preserves history, images, system prompts, and provider formats.

### Audio, TTS, and STT contracts

- [ ] `core.audio._load_sounddevice_if_allowed`: does not import/open sounddevice when disabled.
- [ ] `core.audio._load_soundfile_if_allowed`: defers soundfile import until allowed.
- [ ] `core.audio.stop`: stops filler/TTS streams idempotently.
- [ ] `core.audio.set_tts_speed_boost`: toggles effective stream speed.
- [ ] `core.audio._speed_adjust_pcm`: preserves valid PCM output for rate changes.
- [ ] `core.audio.prewarm_filler`: loads filler clips once and tolerates missing assets.
- [ ] `core.audio.play_filler`: uses cached clips and no-ops when unavailable.
- [ ] `core.audio.play_tts_stream`: drains text and calls `on_done` correctly.
- [ ] `core.audio.play_tts_stream_from_chunks`: handles streaming chunks, stop events, and callback completion.
- [ ] `core.tts.prewarm`: respects safe-mode and provider enablement.
- [ ] `core.tts.close/reset_connections`: closes cached websocket/client state safely.
- [ ] `core.tts.stream_audio`: routes Cartesia, ElevenLabs, and `none`.
- [ ] `core.tts.stream_audio_from_chunks`: streams chunked text and drops empty chunks.
- [ ] `core.tts.test_connection`: validates provider, key, voice, and audio-return cases.
- [ ] `core.stt.prewarm`: does not load models when disabled.
- [ ] `core.stt.start_recording`: opens exactly one recording stream and handles duplicate starts.
- [ ] `core.stt._audio_callback`: queues valid audio frames and ignores errors safely.
- [ ] `core.stt.stop_and_transcribe`: closes streams, concatenates frames, and returns text or empty fallback.

### Memory

- [ ] `core.memory_store.commands.extract_remember_fact`: extracts explicit remember commands and ignores conversational phrasing.
- [ ] `core.memory_store.store._infer_category`: categorizes durable facts predictably.
- [ ] `core.memory_store.store._normalize_fact_text`: trims and normalizes fact text.
- [ ] `core.memory_store.store._is_memory_worthy_fact`: rejects secrets, tasks, and low-value transient text.
- [ ] `core.memory_store.store._merge_fact_lists`: de-duplicates fallback and Chroma facts.
- [ ] `core.memory_store.store._format_memory_block`: formats empty and populated search results.
- [ ] `core.memory_store.store.add_fact_manual_lightweight`: writes JSON fallback without Chroma startup.
- [ ] `core.memory_store.store.update_fact_lightweight`: updates id/category/text or returns false.
- [ ] `core.memory_store.store.delete_fact_lightweight`: deletes by id or returns false.
- [ ] `core.memory_store.store.MemoryManager.record_turn`: records STM and schedules consolidation only when enabled.
- [ ] `core.memory_store.store.MemoryManager.get_stm_context`: summarizes recent turns.
- [ ] `core.memory_store.store.MemoryManager.retrieve_relevant`: uses fallback facts by default and semantic query only when opted in.
- [ ] `core.memory_store.store.MemoryManager.add_explicit_fact/add_fact_manual`: stores durable manual facts.
- [ ] `core.memory_store.store.MemoryManager.get_all_facts`: merges backend facts.
- [ ] `core.memory_store.store.MemoryManager.update_fact/delete_fact`: mutates both fallback and vector-backed records.
- [ ] `core.memory_store.store.get_manager`: is thread-safe.
- [ ] `core.memory_store.store.get_all_facts_lightweight`: avoids heavy imports when possible.

### Addons and tool registry

- [ ] `core.tool_registry.ToolSpec.anthropic_schema/openai_schema`: converts schemas correctly.
- [ ] `core.tool_registry.ToolRegistry.load_keyword_filters/save_keyword_filters`: round-trips filters.
- [ ] `core.tool_registry.ToolRegistry.filtered_schemas/filtered_openai_schemas`: honors keyword filters and pinned server tools.
- [ ] `core.tool_registry.ToolRegistry.register_builtin/unregister_source`: updates registry without stale tools.
- [ ] `core.tool_registry.ToolRegistry.execute`: dispatches callbacks and script tools with useful errors.
- [ ] `core.tool_registry._load_script_tool`: validates manifest, entry point, timeout, and max output.
- [ ] `core.tool_registry._run_script_tool`: enforces timeout, JSON input, output truncation, and stderr handling.
- [ ] `core.addon_manager.load_manifest`: validates id, entry, permissions, settings, hooks, actions, and tools.
- [ ] `core.addon_manager.AddonHostProcess.start/stop/restart/call`: handles process lifecycle and timeout kills.
- [ ] `core.addon_manager.AddonManager.load_all`: skips invalid folders without breaking valid addons.
- [ ] `core.addon_manager.AddonManager.on_startup/on_shutdown`: calls addon hooks once.
- [ ] `core.addon_manager.AddonManager.before_query/after_response`: merges addon hook outputs.
- [ ] `core.addon_manager.AddonManager.get_tray_actions/run_tray_action`: enforces UI permissions.
- [ ] `core.addon_manager.AddonManager.set_enabled/set_setting`: persists addon state.
- [ ] `core.addon_manager.AddonManager._register_tools`: registers only permitted callable tools.
- [ ] `core.addon_host.AddonHost.load/call`: loads addon entry modules and dispatches methods.
- [ ] `core.addon_host.AddonHost._before_query/_run_tray_action/_execute_tool`: handles addon exceptions as structured errors.

### Agent runner and task UI logic

- [ ] `core.agent.workspace.ScopedWorkspace.resolve/relative`: rejects path traversal and absolute escapes.
- [ ] `core.agent.workspace.ScopedWorkspace.list_files/read_text`: respects blocked globs and size limits.
- [ ] `core.agent.workspace.ScopedWorkspace.write_text/patch_text/delete_file`: enforces create/edit/delete permission flags.
- [ ] `core.agent.toolbox.AgentToolbox.list_files/read_file/create_file/write_file/patch_file/delete_file`: returns structured `ToolResult` values.
- [ ] `core.agent.toolbox.AgentToolbox.run_command`: enforces allowlists and dangerous command rejection.
- [ ] `core.agent.toolbox.AgentToolbox.verification_commands`: detects project manifests and static allowed commands.
- [ ] `core.agent.toolbox.AgentToolbox.git_status/git_diff`: works without shell permission.
- [ ] `core.agent.runtime.AgentRunControl`: covers cancel, pause/resume, and manual nudges.
- [ ] `core.agent.runtime.FileLeaseRegistry`: handles acquire, claim, release, holder, and release_all.
- [ ] `core.agent.runtime.AgentPermissions.from_spec`: maps task permissions consistently.
- [ ] `core.agent.runner.AgentTaskRunner.start/run`: creates logs/artifacts and handles cancellation.
- [ ] `core.agent.runner.AgentTaskRunner._run_agent_loop`: routes roles, handoffs, approvals, review completion, and final summaries.
- [ ] `core.agent.runner.AgentTaskRunner._execute_tool_call`: accepts OpenAI/Anthropic alias formats and validates args.
- [ ] `core.agent.runner.AgentTaskRunner._guard_disabled_or_duplicate_tool`: blocks duplicate verification and disabled actions.
- [ ] `core.agent.runner.AgentTaskRunner._run_parallel_read_only_round/_run_parallel_work_round`: isolates failures and file leases.
- [ ] `core.agent.runner.AgentTaskRunner._build_agent_prompt/_build_agent_delta_prompt`: clips repeated state and includes allowed tools only.
- [ ] `ui.agent.log_parser.parse_live_log_event`: parses agent turn, direct message, status, trace, and plain log lines.
- [ ] `ui.agent.task_window.AgentTaskDialog._collect_spec`: emits complete task specs from UI state.
- [ ] `ui.agent.task_window.AgentRunDialog._update_live_meeting`: updates live meeting state from log lines.
- [ ] `ui.agent.task_window.AgentRunDialog._request_approval`: blocks for approval and returns the selected response.

### Shared UI widgets

- [ ] `ui.drop_zone.process_drop_mime`: handles files, images, text, and unsupported MIME data.
- [ ] `ui.drop_zone.ContextPanel.add_item/show_context_summary/remove/clear`: maintains badge indexes after removal.
- [ ] `ui.chat_window._truncate_for_display/_truncate_segments`: prevents render freezes on huge messages.
- [ ] `ui.chat_window._render_markdown_segments`: preserves simple markdown formatting.
- [ ] `ui.chat_window.ChatWindow.start_new_conversation`: creates and selects the new conversation.
- [ ] `ui.chat_window.ChatWindow.ingest_new_conversations`: merges externally added conversations.
- [ ] `ui.chat_window.ChatWindow._send/_on_chunk/_on_finished`: streams replies and restores UI state after success/failure.
- [ ] `ui.chat_window.ChatWindow.update_live_highlight`: highlights spoken text and reverts cleanly.
- [ ] `ui.intent_overlay.IntentOverlay._select/_enter_custom_mode/_fire_custom/_cancel`: covers preset, custom, and cancel paths.
- [ ] `ui.snip_overlay.SnipOverlay.mousePressEvent/mouseMoveEvent/mouseReleaseEvent/keyPressEvent`: emits regions and cancel reliably.
- [ ] `ui.memory_viewer.MemoryPanel.refresh_facts/_on_add_fact/_on_mutation_done`: keeps UI responsive with background work.
- [ ] `ui.settings_panel.dialog.SettingsDialog._load_values/_do_save/_apply`: round-trips settings and secrets.
- [ ] `ui.settings_panel.dialog.SettingsDialog._reset_env_keys_for_page/_reset_current_page/_reset_all`: resets only intended env keys.
- [ ] `ui.settings_panel.dialog.SettingsDialog._test_llm_route/_test_tts_connection`: surfaces async test results and stale-token protection.

## Windows-specific

Run these on Windows 10/11 with a visible desktop session. Use mocks for unit
coverage, then perform manual/CI smoke tests with Notepad, a browser, Office or
LibreOffice files, and the Wisp overlay running.

### Capture, clipboard, and screenshots

- [ ] `core.capture._get_uia`: initializes UIAutomation once and caches unavailable state.
- [ ] `core.capture._get_selected_text_uia`: reads selected text without touching clipboard.
- [ ] `core.capture._get_selected_text_clipboard`: sends Ctrl+C, waits, and restores previous clipboard.
- [ ] `core.capture.get_selected_text`: prefers UIA, then clipboard fallback.
- [ ] `core.capture.get_clipboard_text`: reads text clipboard and returns `None` for empty/non-text/errors.
- [ ] `core.capture.get_screen_snippet`: captures full primary monitor and explicit regions through `mss`.
- [ ] `core.capture.image_to_base64`: encodes captured images for vision calls.
- [ ] `core.context_fetcher._fetch_clipboard_win`: distinguishes text, image, empty, and error clipboard states.
- [ ] `core.context_fetcher._capture_screen_to_file`: saves a screenshot path with `mss`.

### Windows windows, browser, and document context

- [ ] `core.context_fetcher._fetch_active_window_win`: returns title, pid, process, hwnd for the foreground app.
- [ ] `core.context_fetcher._fetch_window_info_win`: resolves a specific hwnd, including invalid hwnd fallback.
- [ ] `core.context_fetcher.get_browser_window_for_context`: prefers provided browser hwnd over foreground Wisp UI.
- [ ] `core.context_fetcher._find_visible_browser_window_win`: finds Chrome/Edge/Firefox windows that are visible.
- [ ] `core.context_fetcher._get_browser_url_uia`: reads browser address bar URL through UIA.
- [ ] `core.context_fetcher._get_browser_text_uia`: reads rendered browser text and truncates.
- [ ] `core.context_fetcher._get_window_text_uia`: reads generic document/editor text by hwnd.
- [ ] `core.context_fetcher._browser_content`: uses hwnd-based browser text when available.
- [ ] `core.context_fetcher._fetch_ui_focused`: reports focused UI element text/name/class/control type.
- [ ] `core.context_fetcher._fetch_recent_files_win`: resolves `.lnk` targets and ignores broken shortcuts.
- [ ] `core.context_fetcher._config_dir`: uses `%APPDATA%`.
- [ ] `core.context_fetcher._vscode_find_file/_vscode_running_roots`: resolves active VS Code file from storage/process hints.
- [ ] `core.context_fetcher._jetbrains_find_file`: resolves JetBrains active file from config paths.
- [ ] `core.context_fetcher._obsidian_find_note`: resolves note title to markdown path.
- [ ] `core.context_fetcher._resolve_doc_path`: resolves Word/Excel/PDF/PPT/text windows to file paths.
- [ ] `core.context_fetcher._enumerate_open_doc_windows_win`: lists candidate document windows.
- [ ] `core.context_fetcher._win_open_files_for_pid`: gathers open files for a Windows pid.
- [ ] `core.context_fetcher._win_match_open_file`: matches document title to open file path.
- [ ] `core.context_fetcher.get_active_document_path`: returns the active document path or empty fallback.
- [ ] `core.context_fetcher.get_all_open_document_paths`: returns unique open document paths.
- [ ] `core.context_fetcher.get_all_open_document_window_texts`: uses UIA text fallback for unsaved documents.

### Windows hotkeys and foreground control

- [ ] `core.hotkeys._hotkey_parts/_is_f_key/is_safe_global_hotkey`: rejects unsafe bare typing keys and accepts F keys/modifier combos.
- [ ] `core.hotkeys._parse_hotkey_win32`: maps modifiers and virtual keys correctly.
- [ ] `core.hotkeys._Win32Impl.start`: registers multiple hotkeys and reports per-hotkey failures.
- [ ] `core.hotkeys._Win32Impl._message_pump`: dispatches the right callback for each hotkey id.
- [ ] `core.hotkeys._Win32Impl.stop`: unregisters all hotkeys and stops the pump.
- [ ] `core.hotkeys.HotkeyListener.start/status/stop`: starts Win32 implementation and voice listener when configured.
- [ ] `core.platform_utils.send_keys`: uses Windows-compatible copy/paste combos through `pynput`.
- [ ] `core.platform_utils.get_foreground_window/set_foreground_window`: round-trips an hwnd.
- [ ] `core.platform_utils.get_window_title/get_window_pid/list_visible_windows`: returns expected metadata for real windows.
- [ ] `main.App._window_pid_win/_window_title_win`: reads hwnd metadata.
- [ ] `main.App._is_external_context_window_win/_find_external_context_window_win/_context_target_hwnd`: avoids capturing Wisp UI as context.
- [ ] `ui.intent_overlay.IntentOverlay._win_force_foreground`: brings the picker to front without stealing forever.

### Windows runtime, single instance, and packaging

- [ ] `core.system.single_instance._acquire_windows`: prevents duplicate app instances with the named mutex.
- [ ] `Start Wisp.bat`: creates/uses venv and starts the supervisor.
- [ ] `tools/build_exe.ps1` and `packaging/Wisp.spec`: build without missing assets or hidden imports.
- [ ] `macos_py.workers.native_host._win_window_pid/_win_window_title/_win_process_name`: return valid metadata.
- [ ] `macos_py.workers.native_host._win_is_wisp_ui_window/_win_is_external_context_window`: classify Wisp vs external windows.
- [ ] `macos_py.workers.native_host._win_find_external_context_window/_win_context_window_id`: find an external target when Wisp has focus.
- [ ] `macos_py.workers.native_host.context_snapshot`: captures selected text, active app, hwnd, browser URL, and document context on Windows.

## macOS-specific

Run these on a real Mac with Accessibility, Screen Recording, Automation,
Microphone, and input-monitoring style permissions tested both granted and
denied where applicable. Also run the crash harness in `docs/TESTBOT.md`.

### macOS safe mode and native locks

- [ ] `core.system.macos_safety.is_macos`: detects only darwin.
- [ ] `core.system.macos_safety.safe_mode_enabled`: defaults on for macOS and off elsewhere.
- [ ] `core.system.macos_safety.audio_enabled`: blocks in-process audio unless explicitly opted in.
- [ ] `core.system.macos_safety.tts_prewarm_enabled/stt_prewarm_enabled`: follows safe-mode and targeted env overrides.
- [ ] `core.system.macos_safety.fs_watcher_enabled/chromadb_enabled/memory_background_llm_enabled`: disables crash-prone background paths by default.
- [ ] `core.system.macos_safety.openai_compat_streaming_enabled`: disables OpenAI-compatible streaming by default for affected providers.
- [ ] `core.system.macos_safety.openai_compat_tools_enabled`: disables OpenAI-compatible live tools by default.
- [ ] `core.system.native_locks.native_init_lock/ssl_init_lock/keychain_lock`: serialize macOS native initialization and are no-ops elsewhere.
- [ ] `core.system.main_thread.set_main_thread_runner/run_on_main`: routes native work to the GUI thread and propagates exceptions.
- [ ] `core.system.sdk_clients.disable_env_proxy_lookup/install_proxy_guard/httpx_client/openai_client/anthropic_client`: avoid env proxy lookup and use native locks.
- [ ] `core.llm_clients.client._use_macos_openai_compat_non_streaming`: switches OpenAI-compatible providers to non-streaming safe mode.
- [ ] `core.llm_clients.client._get_openai_compat_stdlib_ssl_context`: caches certifi SSL context under lock.
- [ ] `core.llm_clients.client._openai_compat_stdlib_completion_text`: performs stdlib request under the native lock.
- [ ] `core.llm_clients.client._dynamic_openai_client/_dynamic_anthropic_client`: construct first client once under lock.
- [ ] `core.llm_clients.client.prewarm`: warms safe providers without touching skipped providers.

### macOS native helper functions

- [ ] `core.platform.macos_native.capture_screen_to_file`: calls `screencapture` for full screen and region captures.
- [ ] `core.platform.macos_native.send_key_combo`: invokes the helper with supported key combos.
- [ ] `core.platform.macos_native.get_clipboard_text`: reads `pbpaste` and returns `None` for empty/errors.
- [ ] `core.platform.macos_native.set_clipboard_text`: writes `pbcopy`.
- [ ] `core.platform.macos_native.get_selected_text`: saves clipboard, sends Cmd+C, returns selected text, restores clipboard.
- [ ] `core.platform.macos_native.paste_text`: writes clipboard and sends Cmd+V.
- [ ] `core.platform.macos_native.list_document_windows`: parses JXA JSON and degrades on permission errors.
- [ ] `core.platform_utils._send_keys_macos_pyobjc`: posts key events on main thread.
- [ ] `core.platform_utils._mac_on_screen_windows/_mac_active_window/_mac_window_info`: report frontmost visible windows.
- [ ] `core.platform_utils._mac_focus_window`: activates the owning app by pid.
- [ ] `core.platform_utils.keep_overlay_visible_across_apps`: applies macOS always-on-top/tool-window behavior.
- [ ] `core.platform_utils.activate_self`: activates Wisp without crashing Cocoa.
- [ ] `core.context_fetcher._osascript_run`: captures stdout/stderr and permission failures clearly.
- [ ] `core.context_fetcher._mac_browser_url/_mac_browser_text`: reads Safari/Chrome/Edge/Firefox active tabs via AppleScript.
- [ ] `core.context_fetcher._fetch_active_window_macos`: returns frontmost document window from native helper.
- [ ] `core.context_fetcher._fetch_clipboard_macos`: reads clipboard through native helper only.
- [ ] `core.context_fetcher._enumerate_open_doc_windows_macos`: maps native helper rows to `WindowInfo`.
- [ ] `core.context_fetcher._mac_open_files_for_pid/_mac_match_open_file`: resolves document titles with `lsof`.
- [ ] `core.context_fetcher._config_dir`: uses `~/Library/Application Support`.

### macOS hotkeys and event taps

- [ ] `core.hotkeys._macos_accessibility_enabled`: detects Accessibility trust.
- [ ] `core.hotkeys._to_pynput_hotkey/_to_pynput_hotkeys`: adds Cmd aliases for Ctrl shortcuts on macOS.
- [ ] `core.hotkeys._parse_hotkey_carbon`: maps modifiers/keycodes and rejects unsafe combos.
- [ ] `core.hotkeys._CarbonImpl.start`: registers event hotkeys, detects permission failure, and starts voice listener.
- [ ] `core.hotkeys._CarbonImpl._dispatch`: dispatches callbacks for the registered hotkey id.
- [ ] `core.hotkeys._CarbonImpl.stop`: unregisters event hotkeys and removes handlers.
- [ ] `macos_py.workers.hotkey_helper._session_diagnostics`: reports session, TTY, and permission context.
- [ ] `macos_py.workers.hotkey_helper._become_ui_element`: hides helper from Dock when allowed.
- [ ] `macos_py.workers.hotkey_helper._parse_combo_to_tap`: parses modifier/key pairs including voice keys.
- [ ] `macos_py.workers.hotkey_helper._build_tap_table`: skips unparseable specs and keeps valid ones.
- [ ] `macos_py.workers.hotkey_helper._match_tap_event`: ignores Caps Lock/Fn noise and matches exact combos.
- [ ] `macos_py.workers.hotkey_helper._hotkey_specs_from_config`: produces caller, snip, context, clear, and voice specs.
- [ ] `macos_py.workers.hotkey_helper._install_hotkey_tap`: active tap swallows matched keydown/keyup and falls back to listen-only if needed.
- [ ] `macos_py.workers.hotkey_helper._teardown_hotkey_tap`: removes tap/run-loop resources.
- [ ] `macos_py.workers.hotkey_helper._rearm_hotkey_tap_if_disabled`: re-enables disabled taps.
- [ ] `macos_py.workers.hotkey_helper._stop_on_parent_pipe_close`: exits when parent dies.
- [ ] `macos_py.workers.native_host._HotkeyHelper`: starts, stops, and cleans stale helper processes.
- [ ] `macos_py.workers.native_host._DirectHotkeys`: fallback direct hotkey path starts/stops cleanly.
- [ ] `macos_py.workers.native_host.hotkeys_start/hotkeys_stop`: surfaces registration errors to supervisor/UI.

### macOS workers, permissions, capture, and paste-back

- [ ] `macos_py.workers.native_host.permissions_snapshot`: reports Accessibility, Screen Recording, Automation, and Microphone states.
- [ ] `macos_py.workers.native_host._active_app`: returns name, pid, bundle id, and window title.
- [ ] `macos_py.workers.native_host._clipboard_text_primary/clipboard_get`: reads text clipboard safely.
- [ ] `macos_py.workers.native_host._clipboard_set_primary/clipboard_set`: writes clipboard safely.
- [ ] `macos_py.workers.native_host.selected_text`: captures selected text without leaving clipboard mutated.
- [ ] `macos_py.workers.native_host.context_snapshot`: captures app/window/browser/document context before overlays appear.
- [ ] `macos_py.workers.native_host.context_browser_content`: fetches page text by browser app or saved URL.
- [ ] `macos_py.workers.native_host.capture_fullscreen/capture_region`: writes image files and normalizes regions.
- [ ] `macos_py.workers.native_host._frontmost_pid/_activate_pid`: focus target apps without crashing.
- [ ] `macos_py.workers.native_host._ax_capture_focus/_ax_selected_text/_ax_apply_selected_text`: focus-token paste-back path works when AX is granted.
- [ ] `macos_py.workers.native_host.paste_text`: prefers AX replacement, falls back to clipboard paste, and respects target pid/focus token.
- [ ] `macos_py.workers.native_host.notify`: sends native user notifications.
- [ ] `macos_py.workers.native_host.open_privacy_settings`: opens the requested privacy pane.
- [ ] `macos_py.workers.audio_host.audio_config_reload/audio_prewarm`: reloads config without importing forbidden stacks too early.
- [ ] `macos_py.workers.audio_host.record_start/record_stop_transcribe`: wraps STT start/stop through the audio worker.
- [ ] `macos_py.workers.audio_host.tts_synthesize`: writes WAV for Cartesia, ElevenLabs, `none`, and float/offline providers.
- [ ] `macos_py.workers.audio_host.play_file/audio_stop/audio_speed_boost`: controls playback lifecycle.
- [ ] `core.macos_helper.protocol.write_message/read_message`: round-trips newline-delimited JSON.
- [ ] `core.macos_helper.client.HelperClient.call/call_with_events equivalent paths`: handles startup, timeout, process death, and events.
- [ ] `core.macos_helper.handlers.stt_prewarm/stt_start_recording/stt_stop_and_transcribe`: run in helper process and emit events.
- [ ] `core.macos_helper.handlers.stt_selftest/stt_mic_probe`: report useful diagnostics.

### macOS supervisor and UI flows

- [ ] `macos_py.supervisor.ipc.WorkerClient.start/call/call_with_events/restart/shutdown`: handles worker lifecycle and stderr logs.
- [ ] `macos_py.supervisor.ipc.WispSupervisor.start_all/call/shutdown`: starts UI, native, audio, and brain workers.
- [ ] `macos_py.supervisor.flows.FlowController.start/start_hotkeys`: wires events and reports failed hotkey registration.
- [ ] `macos_py.supervisor.flows.FlowController.begin_caller`: captures context before showing intent overlay.
- [ ] `macos_py.supervisor.flows.FlowController.begin_snip/snip_region_selected`: captures selected screen region and passes image to query.
- [ ] `macos_py.supervisor.flows.FlowController.intent_chosen`: routes general query vs rewrite/paste caller.
- [ ] `macos_py.supervisor.flows.FlowController.add_context/clear_context/context_items_dropped/remove_context_item`: manages buffered context and UI badges.
- [ ] `macos_py.supervisor.flows.FlowController.voice_start/voice_stop`: records, transcribes, and queries with voice caller config.
- [ ] `macos_py.supervisor.flows.FlowController.reload_settings`: refreshes supervisor, brain, audio, hotkeys, and UI.
- [ ] `macos_py.supervisor.flows.FlowController.chat_request`: streams chat through brain worker and memory context.
- [ ] `macos_py.supervisor.flows.FlowController.memory_add/memory_update/memory_delete`: routes memory mutations through brain worker.
- [ ] `macos_py.supervisor.flows.FlowController.plugin_run_action/plugin_set_enabled/plugin_set_setting`: routes plugin UI events.
- [ ] `macos_py.supervisor.flows.FlowController.run_agent_task/cancel_agent_task/respond_agent_approval`: controls agent worker runs.
- [ ] `macos_py.supervisor.flows.FlowController._query`: streams chunks, TTS, memory, bubble, and final idle state.
- [ ] `macos_py.supervisor.flows.FlowController._rewrite_and_paste`: restores focus and pastes into original app.
- [ ] `macos_py.supervisor.flows.FlowController._context_snapshot/_fetch_browser_snapshot`: avoids reading Wisp overlay as active context.
- [ ] `macos_py.supervisor.flows.FlowController._brain_query_params`: maps caller context modes, tools, memory, screenshots, and images.
- [ ] `macos_py.supervisor.flows.FlowController._capture_fullscreen_b64/_capture_model_tool_b64`: uses native capture before overlays listen.
- [ ] `macos_py.supervisor.flows.FlowController._speak_text`: sends TTS to the audio worker only when enabled.
- [ ] `macos_py.workers.ui_host.QtFreezeWatchdog`: writes freeze diagnostics on UI hangs.
- [ ] `macos_py.workers.ui_host.QtProtocolHost`: handles settings, memory, plugins, chat, agent dialogs, bubble, and snip events without blocking Qt.
- [ ] `scripts/macos_smoke.py`: imports and calls real pyobjc/native helper paths.
- [ ] `scripts/macos_testbot.py ssl-race`: confirms SSL native lock prevents crash.
- [ ] `scripts/macos_testbot.py query`: confirms LLM streaming plus TTS concurrency.
- [ ] `scripts/macos_testbot.py qt`: confirms GUI-run-loop TTS path is stable.

## Linux-specific

Run these under X11 and Wayland where possible. Validate missing optional tools
as well as installed `xclip`, `xsel`, `wl-clipboard`, EWMH/Xlib, and desktop
notification behavior.

### Linux capture, clipboard, screenshots, and documents

- [ ] `core.capture._get_primary_selection_linux`: prefers Wayland primary selection when `WAYLAND_DISPLAY` exists.
- [ ] `core.capture._get_primary_selection_linux`: falls back through `xclip`, `xsel`, and `wl-paste`.
- [ ] `core.capture.get_selected_text`: prefers PRIMARY selection, then clipboard copy fallback.
- [ ] `core.capture.get_clipboard_text`: handles pyperclip backend missing errors.
- [ ] `core.capture.get_screen_snippet`: captures monitor and explicit region through `mss`.
- [ ] `core.context_fetcher._fetch_active_window_linux`: returns active window info through available Linux backend or empty fallback.
- [ ] `core.context_fetcher._fetch_clipboard_linux`: returns text, empty, and error states.
- [ ] `core.context_fetcher._fetch_recent_files_linux`: returns recent files from supported desktop metadata or safe fallback.
- [ ] `core.context_fetcher._enumerate_open_doc_windows_linux`: lists visible document/editor windows.
- [ ] `core.context_fetcher._config_dir`: uses `~/.config`.
- [ ] `core.context_fetcher._vscode_find_file/_vscode_running_roots`: handles Linux VS Code config roots.
- [ ] `core.context_fetcher._jetbrains_find_file`: handles `~/.config/JetBrains` and Google Android Studio paths.
- [ ] `core.context_fetcher.get_active_document_path`: resolves active document or empty fallback.
- [ ] `core.context_fetcher.get_all_open_document_paths`: returns unique Linux document paths.

### Linux hotkeys, windows, and focus

- [ ] `core.hotkeys._to_pynput_hotkey`: maps `ctrl`, `shift`, `alt`, `cmd/super`, letters, numbers, and function keys.
- [ ] `core.hotkeys._to_pynput_hotkeys`: returns aliases only when appropriate.
- [ ] `core.hotkeys._PynputImpl.start`: starts `GlobalHotKeys` and voice listener when configured.
- [ ] `core.hotkeys._PynputImpl.stop`: stops listeners cleanly and releases voice listener.
- [ ] `core.hotkeys.HotkeyListener.start/status/stop`: reports Linux listener availability.
- [ ] `core.platform_utils._get_ewmh`: handles missing EWMH/Xlib deps gracefully.
- [ ] `core.platform_utils._xlib_active_window/_xlib_focus_window`: reads and focuses windows under X11.
- [ ] `core.platform_utils.get_foreground_window/set_foreground_window`: degrades gracefully under Wayland restrictions.
- [ ] `core.platform_utils.get_window_title/get_window_pid/list_visible_windows`: returns metadata when X11 APIs are available.
- [ ] `core.platform_utils.keep_overlay_visible_across_apps`: applies Qt flags without platform crashes.
- [ ] `core.platform_utils.activate_self`: brings Wisp forward where supported.

### Linux runtime and packaging

- [ ] `core.system.single_instance._acquire_posix`: prevents duplicate instances with a lock file.
- [ ] `Start Wisp.command`: creates/uses venv and starts the supervisor on Linux.
- [ ] `tools/build_exe.sh` and `packaging/WispLinux.spec`: build with Linux assets and hidden imports.
- [ ] `macos_py.supervisor.ipc.WorkerClient`: starts worker processes on Linux despite the `macos_py` namespace.
- [ ] `macos_py.supervisor.flows.FlowController`: runs caller, snip, voice, chat, memory, plugin, and agent flows on Linux with Linux native/capture backends.

## Manual end-to-end smoke matrix

Run these on each target OS after unit tests pass.

- [ ] Launch: clean venv install, first launch, second launch, duplicate launch rejection.
- [ ] Hotkey: general caller, rewrite caller, snip caller, add context, clear context, voice hotkey.
- [ ] Text capture: browser, text editor, terminal, Office/LibreOffice, no selection.
- [ ] Clipboard: text, image/non-text, empty, locked/unavailable clipboard.
- [ ] Browser context: Chrome, Edge, Firefox, Safari on macOS, inactive/background browser, permission denied.
- [ ] Document context: saved docx/xlsx/pptx/pdf/txt/csv/md, unsaved document with visible text, renamed/moved file.
- [ ] Screenshot: full screen, region, multi-monitor, permission denied, tiny/invalid region.
- [ ] Query: streaming success, fallback route success, all routes fail, tools on/off/auto, memory on/off/model-decided.
- [ ] TTS: provider `none`, Cartesia, ElevenLabs, stop midstream, speed boost, missing key, bad voice.
- [ ] STT: push-to-talk success, empty recording, denied mic, missing model, rapid start/stop.
- [ ] Rewrite paste-back: same app focus, focus changed, AX/UIA path, clipboard fallback, paste failure.
- [ ] UI: overlay pinning, intent picker keyboard paths, snip cancel, chat large message, memory viewer, settings apply/reset.
- [ ] Addons: enabled/disabled, hook success/failure, tray action, settings change, model tool execution, host timeout.
- [ ] Agent: run, cancel, pause/resume, approval allow/deny, retry, continue, open history, blocked permissions.
