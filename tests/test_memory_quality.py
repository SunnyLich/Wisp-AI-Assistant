from core.memory_store import store


def test_summarizer_rejects_task_request():
    assert not store._is_memory_worthy_fact("Please fix the settings dialog", source="summarizer")


def test_summarizer_keeps_durable_preference():
    assert store._is_memory_worthy_fact("I prefer concise answers", source="summarizer")


def test_explicit_can_keep_short_command_shaped_fact():
    assert store._is_memory_worthy_fact("fix grammar before pasting text", source="explicit")


def test_rejects_secrets():
    assert not store._is_memory_worthy_fact("My API key is sk-testabcdefghijklmnop", source="explicit")
