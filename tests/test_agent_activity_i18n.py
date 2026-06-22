from __future__ import annotations


def test_agent_activity_translates_handoff_tool_and_model_lines(monkeypatch) -> None:
    """Verify auto-agent activity text translates known run-log patterns."""
    from ui.agent import activity_i18n

    translations = {
        "Coordinator": "協調員",
        "Builder": "建構者",
        "waiting": "等待中",
        "Handoff ({status}): {reason}": "交接（{status}）：{reason}",
        "Told {target}: {message}": "已告知 {target}：{message}",
        "Heard from {source}: {message}": "收到 {source} 的訊息：{message}",
        "tool {tool} failed: {message}": "工具 {tool} 失敗：{message}",
        "Message cannot be empty.": "訊息不可為空。",
        "agent turn {turn}: {agent}": "代理輪次 {turn}：{agent}",
        "requesting LLM tool response via {route}": "正透過 {route} 請求 LLM 工具回應",
        "model call still waiting after {elapsed} via {route}": "模型呼叫透過 {route} 等待已超過 {elapsed}",
        "prompt prepared for {agent}: {chars} chars ({mode})": "已為 {agent} 準備提示詞：{chars} 字元（{mode}）",
        "delta": "差異",
    }
    monkeypatch.setattr(activity_i18n, "t", lambda text: translations.get(text, text))

    assert activity_i18n.translate_agent_activity_text(
        "Told Builder: Handoff (waiting): check files"
    ) == "已告知 建構者：交接（等待中）：check files"
    assert activity_i18n.translate_agent_activity_text(
        "Heard from Builder: Handoff (waiting): done"
    ) == "收到 建構者 的訊息：交接（等待中）：done"
    assert activity_i18n.translate_agent_activity_text(
        "tool send_message failed: Message cannot be empty."
    ) == "工具 send_message 失敗：訊息不可為空。"
    assert activity_i18n.translate_agent_log_line(
        "[12:00:00] agent turn 3/30: Coordinator"
    ) == "[12:00:00] 代理輪次 3/30：協調員"
    assert activity_i18n.translate_agent_activity_text(
        "requesting LLM tool response via openai / gpt-5.5"
    ) == "正透過 openai / gpt-5.5 請求 LLM 工具回應"
    assert activity_i18n.translate_agent_activity_text(
        "model call still waiting after 5s via openai / gpt-5.5"
    ) == "模型呼叫透過 openai / gpt-5.5 等待已超過 5s"
    assert activity_i18n.translate_agent_activity_text(
        "prompt prepared for Coordinator: 2587 chars (delta)"
    ) == "已為 協調員 準備提示詞：2587 字元（差異）"


def test_agent_status_and_health_translate_dynamic_values(monkeypatch) -> None:
    """Verify auto-agent status and health summaries translate templates."""
    from ui.agent import activity_i18n

    translations = {
        "Builder": "建構者",
        "Waiting {elapsed}": "等待 {elapsed}",
        "Receiving response ({elapsed})": "正在接收回應（{elapsed}）",
        "Handing off to {agent}": "正在交接給 {agent}",
        "avg {avg} | invalid {invalid} | repair {repairs} | fallback {fallbacks}": "平均 {avg} | 無效 {invalid} | 修復 {repairs} | 回退 {fallbacks}",
        "calls {calls}, average latency {avg}s, invalid JSON {invalid}, repairs {repairs}, fallbacks {fallbacks}": "呼叫 {calls}，平均延遲 {avg}s，無效 JSON {invalid}，修復 {repairs}，回退 {fallbacks}",
    }
    monkeypatch.setattr(activity_i18n, "t", lambda text: translations.get(text, text))
    health = {"calls": 2, "total_latency": 16.4, "invalid_json": 0, "repairs": 0, "fallbacks": 0}

    assert activity_i18n.translate_agent_status("Waiting 5s") == "等待 5s"
    assert activity_i18n.translate_agent_status("Receiving response (5.6s)") == "正在接收回應（5.6s）"
    assert activity_i18n.translate_agent_status("Handing off to Builder") == "正在交接給 建構者"
    assert activity_i18n.translate_agent_health_badge(health) == "平均 8.2s | 無效 0 | 修復 0 | 回退 0"
    assert activity_i18n.translate_agent_health_detail(health) == "呼叫 2，平均延遲 8.2s，無效 JSON 0，修復 0，回退 0"
