"""Agent protocol response parsing and repair helpers."""
from __future__ import annotations

from typing import Callable
import ast
import json
import re
import time

from core.agent.runtime import LogCallback


class AgentResponseMixin:
    @staticmethod
    def _compact_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        head = max_chars // 2
        tail = max_chars - head
        return (
            text[:head]
            + f"\n... [middle truncated {len(text) - max_chars} chars for prompt; full data is in run artifacts] ...\n"
            + text[-tail:]
        )

    def _repair_agent_response(
        self,
        bad_response: str,
        log: LogCallback,
        verbose: Callable[[str, object], None] | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        fallbacks: str | None = None,
    ) -> str | None:
        local_repair = self._locally_repair_agent_response(bad_response)
        if local_repair is not None:
            log("repaired invalid JSON locally")
            return local_repair
        if not self._has_repairable_protocol_object(bad_response):
            # No complete protocol object to fix: the model rambled in prose, dumped
            # code into a <thought> block, or was cut off mid-JSON. A model repair
            # call would only invent content, so retry the turn cheaply instead.
            log("invalid JSON has no complete protocol object to repair; using local fallback without model repair")
            fallback = self._fallback_invalid_response(bad_response)
            log("using local fallback for invalid JSON response")
            return fallback
        excerpt = self._repair_response_excerpt(bad_response)
        repair_prompt = (
            "Convert the following text into valid JSON for the agent protocol. "
            "Return only one JSON object with keys thought, status, next_agent, "
            "reason, tool_calls, and final. Do not explain, do not use markdown, "
            "and do not wrap the result in an output field. If the response is "
            "truncated or missing file content, return a retry JSON object with "
            "an empty tool_calls list instead of inventing missing content. "
            "Preserve intended complete tool calls and final text. The bad response "
            "may be excerpted; do not attempt to reconstruct omitted file content.\n\n"
            f"Bad response excerpt ({len(bad_response)} original chars):\n"
            + excerpt
        )
        log(f"requesting JSON repair with {len(excerpt)} char excerpt from {len(bad_response)} char response")
        started = time.perf_counter()
        repaired = self._call_model(repair_prompt, log, provider=provider, model=model, fallbacks=fallbacks, max_tokens=1024)
        log(f"JSON repair response received in {time.perf_counter() - started:.1f}s ({len(repaired)} chars)")
        if verbose:
            verbose("JSON repair response", repaired)
        try:
            self._parse_agent_response(repaired)
        except ValueError as exc:
            log(f"JSON repair response invalid: {exc}")
            fallback = self._fallback_invalid_response(bad_response)
            log("using local fallback for invalid JSON response")
            return fallback
        return repaired

    @staticmethod
    def _repair_response_excerpt(response_text: str, max_chars: int = 3000) -> str:
        return AgentResponseMixin._compact_text(response_text, max_chars)

    @staticmethod
    def _parse_agent_response(response_text: str) -> dict:
        text = AgentResponseMixin._extract_json_text(response_text)
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Agent response was not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            if isinstance(parsed, list):
                parsed = {"thought": "", "tool_calls": parsed, "final": None}
            else:
                raise ValueError("Agent response JSON must be an object.")
        parsed.setdefault("tool_calls", [])
        parsed.setdefault("final", None)
        return parsed

    @staticmethod
    def _locally_repair_agent_response(response_text: str) -> str | None:
        text = AgentResponseMixin._strip_json_fence(AgentResponseMixin._extract_json_text(response_text))
        candidates = [text]
        sanitized = AgentResponseMixin._escape_control_chars_inside_json_strings(text)
        if sanitized != text:
            candidates.append(sanitized)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                if isinstance(parsed, dict) and isinstance(parsed.get("output"), str):
                    nested = AgentResponseMixin._locally_repair_agent_response(parsed["output"])
                    if nested is not None:
                        return nested
                if isinstance(parsed, (dict, list)):
                    try:
                        AgentResponseMixin._parse_agent_response(json.dumps(parsed, ensure_ascii=False))
                    except ValueError:
                        continue
                    return json.dumps(parsed, ensure_ascii=False)
            try:
                literal = ast.literal_eval(candidate)
            except (SyntaxError, ValueError):
                continue
            if isinstance(literal, (dict, list)):
                try:
                    AgentResponseMixin._parse_agent_response(json.dumps(literal, ensure_ascii=False))
                except ValueError:
                    continue
                return json.dumps(literal, ensure_ascii=False)
        return None

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        text = text.strip()
        if not text.startswith("```"):
            return text
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _escape_control_chars_inside_json_strings(text: str) -> str:
        result: list[str] = []
        in_string = False
        escaped = False
        for char in text:
            if in_string:
                if escaped:
                    result.append(char)
                    escaped = False
                    continue
                if char == "\\":
                    result.append(char)
                    escaped = True
                    continue
                if char == '"':
                    result.append(char)
                    in_string = False
                    continue
                if char == "\n":
                    result.append("\\n")
                    continue
                if char == "\r":
                    result.append("\\r")
                    continue
                if char == "\t":
                    result.append("\\t")
                    continue
            else:
                if char == '"':
                    in_string = True
            result.append(char)
        return "".join(result)

    @staticmethod
    def _looks_like_truncated_agent_response(response_text: str) -> bool:
        # Operate on the raw response, not the extracted object: a response that
        # spends its whole token budget on a reasoning preamble and then gets cut
        # off mid-JSON must be detected here so the runner retries cheaply instead
        # of paying for a model repair call that can only guess at lost content.
        text = response_text.strip()
        if not text:
            return False
        # An object or array was opened but never closed: content was lost, so a
        # model repair call can only guess. Retry instead.
        if text.count("{") > text.count("}") or text.count("[") > text.count("]"):
            return True
        # A file-writing payload that stops before its closing brace is also a cut
        # off response. Allow a trailing reasoning close tag (e.g. </thought>) that
        # some models append after the JSON.
        stripped = re.sub(r"</\w+>\s*$", "", text.rstrip().rstrip("`").rstrip()).rstrip()
        return any(key in text for key in ('"content_base64"', '"content"')) and not stripped.endswith(("}", "]"))

    # Keys that identify the agent protocol object, used to pick the real JSON
    # object out of a response that also contains stray braces in prose preambles.
    _PROTOCOL_KEYS = ("tool_calls", "final", "status", "next_agent", "thought", "reason")

    @staticmethod
    def _has_repairable_protocol_object(response_text: str) -> bool:
        """True only when a model repair call has real content to fix.

        Requires a complete (balanced) brace structure carrying at least two
        protocol keys. Pure prose, code dumped inside a ``<thought>`` block, and
        responses cut off mid-JSON all fail this check, so they retry cheaply
        instead of paying for a repair that could only guess the lost content.
        """
        text = response_text.strip()
        if "{" not in text or text.count("{") != text.count("}"):
            return False
        if text.count("[") != text.count("]"):
            return False
        protocol_keys = sum(1 for key in AgentResponseMixin._PROTOCOL_KEYS if f'"{key}"' in text)
        return protocol_keys >= 2

    @staticmethod
    def _iter_balanced_json_objects(text: str):
        """Yield each top-level ``{...}`` substring, respecting JSON string escaping.

        Brace matching ignores braces and quotes that appear inside JSON string
        literals so that prose like ``f"score {n}"`` embedded in a reasoning
        preamble does not derail the scan.
        """
        depth = 0
        start = -1
        in_string = False
        escaped = False
        for i, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif char == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        yield text[start:i + 1]

    @staticmethod
    def _score_json_candidate(candidate: str) -> tuple[int, int] | None:
        """Rank a candidate as ``(protocol_key_count, length)`` or None if unusable.

        Control characters are escaped only to decide whether the candidate is a
        valid object; the caller keeps the raw text so the existing local-repair
        path stays responsible for normalising loosely-encoded content.
        """
        for form in (candidate, AgentResponseMixin._escape_control_chars_inside_json_strings(candidate)):
            try:
                parsed = json.loads(form)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return sum(1 for key in AgentResponseMixin._PROTOCOL_KEYS if key in parsed), len(candidate)
        return None

    @staticmethod
    def _best_json_object(text: str) -> str | None:
        """Pick the balanced object that best matches the agent protocol.

        Prefers the object carrying the most protocol keys (then the longest),
        which skips JSON-looking fragments (e.g. a lone ``{"name": ...}`` tool
        call) quoted inside a reasoning preamble.
        """
        best_text: str | None = None
        best_rank: tuple[int, int] | None = None
        for candidate in AgentResponseMixin._iter_balanced_json_objects(text):
            rank = AgentResponseMixin._score_json_candidate(candidate)
            if rank is None:
                continue
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_text = candidate
        return best_text

    @staticmethod
    def _extract_json_text(response_text: str) -> str:
        text = response_text.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fenced:
            candidate = fenced.group(1).strip()
            if candidate.startswith(("{", "[")):
                text = candidate
        if text.startswith("```"):
            return text
        if text.lstrip().startswith("["):
            start = text.find("[")
            end = text.rfind("]")
            return text[start:end + 1].strip() if 0 <= start < end else text
        best = AgentResponseMixin._best_json_object(text)
        if best is not None:
            return best
        start = text.find("{")
        end = text.rfind("}")
        if 0 <= start < end:
            return text[start:end + 1].strip()
        return text

    @staticmethod
    def _tool_call_name(call: dict) -> str:
        name = call.get("tool")
        if name is None:
            name = call.get("function")
        if name is None:
            name = call.get("name")
        if isinstance(name, dict):
            name = name.get("name")
        return str(name or "")

    @staticmethod
    def _tool_call_args(call: dict) -> dict | object:
        args = call.get("args")
        if args is None:
            args = call.get("parameters")
        if args is None and isinstance(call.get("function"), dict):
            args = call["function"].get("arguments")
        if args is None:
            args = {}
        return args

    @staticmethod
    def _fallback_invalid_response(response_text: str) -> str:
        return json.dumps(
            {
                "thought": "The previous model response was malformed JSON and could not be repaired.",
                "status": "retry",
                "next_agent": "same",
                "reason": "Retry the same agent because the prior JSON response was malformed.",
                "tool_calls": [],
                "final": None,
                "raw_response_excerpt": response_text[:4000],
            },
            ensure_ascii=False,
        )
