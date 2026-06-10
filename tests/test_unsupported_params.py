"""Regression tests for OpenAI-compatible unsupported-parameter detection.

GPT-5-family / reasoning models reject any non-default sampling value with an
"Unsupported value: 'temperature' ... only the default is supported" 400. The
route must recognise that and drop the parameter (falling back to the model
default) instead of failing the whole request.
"""
from core.llm_clients.client import (
    _apply_max_output,
    _apply_sampling,
    _model_rejects_custom_sampling,
    _model_uses_max_completion_tokens,
    _recover_openai_compat_kwargs,
    _stream_openai_compat_plain,
    _unsupported_parameter_name,
    _without_unsupported_parameter,
)


def _exc(msg: str) -> RuntimeError:
    return RuntimeError(msg)


def test_temperature_unsupported_value_detected_and_dropped():
    exc = _exc(
        "Error code: 400 - {'error': {'message': \"Unsupported value: "
        "'temperature' does not support 0.5 with this model. Only the default "
        "(1) value is supported.\", 'param': 'temperature', "
        "'code': 'unsupported_value'}}"
    )
    assert _unsupported_parameter_name(exc) == "temperature"
    kwargs = {"model": "gpt-5.5", "temperature": 0.5, "messages": []}
    assert _without_unsupported_parameter(kwargs, exc) == {
        "model": "gpt-5.5",
        "messages": [],
    }


def test_top_p_unsupported_value_detected():
    exc = _exc(
        "Unsupported value: 'top_p' does not support 0.9 with this model. "
        "Only the default (1) value is supported."
    )
    assert _unsupported_parameter_name(exc) == "top_p"


def test_legacy_unsupported_parameter_still_detected():
    exc = _exc(
        "Unsupported parameter: 'parallel_tool_calls' is not supported with this model."
    )
    assert _unsupported_parameter_name(exc) == "parallel_tool_calls"


def test_unrelated_error_returns_empty():
    assert _unsupported_parameter_name(_exc("rate limit exceeded")) == ""


# --- proactive: omit temperature for models that only accept the default --------

def test_models_that_reject_custom_sampling():
    for model in ("gpt-5.5", "gpt-5-mini", "o1", "o3-pro", "claude-opus-4-8",
                  "claude-opus-4-7", "claude-fable-5"):
        assert _model_rejects_custom_sampling(model), model


def test_models_that_accept_custom_sampling():
    for model in ("llama3-8b-8192", "gpt-4o", "claude-sonnet-4-6",
                  "claude-opus-4-6", "gemini-2.0-flash"):
        assert not _model_rejects_custom_sampling(model), model


def test_apply_sampling_omits_for_rejecting_model():
    kwargs = {"model": "gpt-5.5"}
    _apply_sampling(kwargs, "gpt-5.5", 0.5)
    assert "temperature" not in kwargs


def test_apply_sampling_keeps_for_accepting_model():
    kwargs = {"model": "claude-sonnet-4-6"}
    _apply_sampling(kwargs, "claude-sonnet-4-6", 0.7)
    assert kwargs["temperature"] == 0.7


def test_apply_sampling_omits_when_temperature_none():
    kwargs = {"model": "gpt-4o"}
    _apply_sampling(kwargs, "gpt-4o", None)
    assert "temperature" not in kwargs


# --- max_tokens vs max_completion_tokens naming --------------------------------

def test_max_completion_tokens_models():
    for m in ("gpt-5.5", "o1-mini", "o3"):
        assert _model_uses_max_completion_tokens(m), m
    for m in ("gpt-4o", "llama3-8b-8192", "claude-opus-4-8"):
        assert not _model_uses_max_completion_tokens(m), m


def test_apply_max_output_field_name():
    k1 = {}
    _apply_max_output(k1, "gpt-5.5", 1024)
    assert k1 == {"max_completion_tokens": 1024}
    k2 = {}
    _apply_max_output(k2, "gpt-4o", 1024)
    assert k2 == {"max_tokens": 1024}


# --- reactive recovery: rename / drop the field the model rejects --------------

def test_recover_renames_max_tokens():
    exc = _exc(
        "Error code: 400 - Unsupported parameter: 'max_tokens' is not supported "
        "with this model. Use 'max_completion_tokens' instead."
    )
    kw = {"model": "gpt-5.5", "max_tokens": 1024, "messages": []}
    out = _recover_openai_compat_kwargs("openai", "test-rename-model", kw, exc)
    assert out == {"model": "gpt-5.5", "max_completion_tokens": 1024, "messages": []}


def test_recover_drops_temperature_value_error():
    exc = _exc(
        "Unsupported value: 'temperature' does not support 0.5 with this model. "
        "Only the default (1) value is supported."
    )
    kw = {"model": "gpt-5.5", "temperature": 0.5, "messages": []}
    out = _recover_openai_compat_kwargs("openai", "test-temp-model", kw, exc)
    assert out == {"model": "gpt-5.5", "messages": []}


def test_recover_returns_none_for_unrelated_error():
    assert _recover_openai_compat_kwargs(
        "openai", "test-x-model", {"model": "m"}, _exc("rate limit exceeded")
    ) is None


def test_stream_openai_compat_plain_self_heals(monkeypatch):
    """First create() 400s on temperature; the helper drops it and retries,
    streaming successfully — proving the path self-heals on any rejected field."""
    import core.llm_clients.client as client

    calls = []

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            class _Delta:
                content = "hi"

            class _Choice:
                delta = _Delta()

            class _Chunk:
                choices = [_Choice()]

            return iter([_Chunk()])

    class _Completions:
        def create(self, **kwargs):
            calls.append(dict(kwargs))
            if "temperature" in kwargs:
                raise RuntimeError(
                    "Unsupported value: 'temperature' does not support 0.5 with "
                    "this model. Only the default (1) value is supported."
                )
            return _Stream()

    class _Client:
        class chat:
            completions = _Completions()

    monkeypatch.setattr(client, "_dynamic_openai_client", lambda _p: _Client())

    kwargs = {"model": "gpt-5.5", "messages": [], "stream": True, "temperature": 0.5}
    out = "".join(client._stream_openai_compat_plain("openai", "test-selfheal-model", kwargs))
    assert out == "hi"
    assert "temperature" in calls[0]       # first attempt sent it
    assert "temperature" not in calls[1]   # retry dropped it
