"""Both OpenCode 2 artifacts held to the committed 2.1.4 corpus in the sibling `blizzard`: every session, message,
part, tool state, event, model, permission request, form, and error either answers with uses only keys the corpus's
samples of its kind carry, with the same JSON types, and carries every key all those samples do, save what
:data:`_OMITTED` declares. A kind the corpus never captured is declared in :data:`_UNCAPTURED`; anything else fails.
Feeding these shapes through `blizzard`'s production parsers is a separate tier."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest
from typing_extensions import TypeIs

from blizzard_mock.harness.opencode2_engine.domain import failures, fence
from tests.opencode2_service import Json, Service, Starter, artifact, objects

pytestmark = pytest.mark.needs_blizzard

_BLIZZARD = Path(os.environ.get("BLIZZARD_SOURCE") or Path(__file__).resolve().parents[2] / "blizzard")
_CORPUS = _BLIZZARD / "src" / "blizzard" / "runner" / "harness" / "contracts" / "opencode" / "2.1.4"

#: Keys every corpus sample of a kind carries that the mock does not mint, by kind, each with why.
_OMITTED: dict[str, dict[str, str]] = {
    "message.assistant": {
        "native": "the provider's own response record, opaque to blizzard",
        "snapshot": "the worktree snapshot hash 2.1.4 takes per step; the mock keeps no snapshots",
    },
    "part.text": {"native": "the provider's own part record, opaque to blizzard"},
    "part.reasoning": {"native": "the provider's encrypted reasoning, opaque to blizzard"},
    "part.tool": {"native": "the provider's own call record, opaque to blizzard"},
    "event.session.created": {
        "slug": "the human-readable session name the TUI shows",
        "subpath": "the session's path below its project root",
        "version": "the OpenCode version that created the session",
        "agent": "a session created without an agent names none; the one captured creation is a child's, which names one",
        "parentID": "a root session has no parent; the one captured creation is a child's",
    },
    "event.session.step.started": {"snapshot": "the worktree snapshot hash; the mock keeps no snapshots"},
    "event.session.step.ended": {
        "native": "the provider's own response record, opaque to blizzard",
        "snapshot": "the worktree snapshot hash; the mock keeps no snapshots",
    },
    "event.session.text.ended": {"native": "the provider's own part record, opaque to blizzard"},
    "event.session.tool.called": {"native": "the provider's own call record, opaque to blizzard"},
    "model": {
        "package": "the provider SDK package that serves the model",
        "settings": "provider settings, redacted in the corpus",
        "time": "the model's release date",
    },
}

#: Kinds the mock mints that the 2.1.4 corpus has no sample of, each with why the mock mints it anyway.
_UNCAPTURED: dict[str, str] = {
    "event.session.tool.failed": "a refused or failed tool call's event; the live proof captured only succeeding calls",
    "event.session.permission.asked": "a raised permission request's event; the live proof read requests by polling",
}

#: Keys whose values are the caller's or the tool's own, so their contents are not held to the corpus's samples.
_OPEN = frozenset({"metadata", "input", "item"})


def _is_object(value: object) -> TypeIs[Json]:
    """Whether a decoded JSON value is an object — JSON object keys are always strings."""
    return isinstance(value, dict)


def _is_array(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _load(name: str) -> object:
    return json.loads((_CORPUS / name).read_text())


def _corpus_files() -> list[str]:
    return sorted(path.name for path in _CORPUS.glob("*.json") if path.name not in {"manifest.json", "report.json"})


class _Kinds:
    """Samples of every kind, split the way the shapes differ: messages by type, parts by type, tool states by
    status, events by type."""

    def __init__(self) -> None:
        self.samples: dict[str, list[Json]] = {}

    def add(self, kind: str, value: object) -> None:
        if _is_object(value):
            self.samples.setdefault(kind, []).append(value)

    def session(self, record: object) -> None:
        self.add("session", record)

    def message(self, message: Json) -> None:
        kind = str(message.get("type"))
        self.add(f"message.{kind}", {key: value for key, value in message.items() if key != "content"})
        for part in objects(message.get("content")):
            self.part(part)

    def part(self, part: Json) -> None:
        kind = str(part.get("type"))
        self.add(f"part.{kind}", {key: value for key, value in part.items() if key != "state"})
        state = part.get("state")
        if _is_object(state):
            self.add(f"state.{state.get('status')}", state)

    def page(self, page: object) -> None:
        if isinstance(page, dict):
            self.add("page", {key: value for key, value in page.items() if key != "data"})
            for message in objects(page.get("data")):
                self.message(message)

    def event(self, event: Json) -> None:
        self.add(f"event.{event.get('type')}", event.get("data"))
        self.add("envelope", {key: value for key, value in event.items() if key != "data"})


def _corpus() -> _Kinds:
    kinds = _Kinds()
    for name in ("caller_named_session.json", "interrupt_root_session.json", "model_session.json"):
        kinds.session(_load(name))
    kinds.session(_load("usage_root_session.json"))
    for name in ("child_sessions.json", "usage_child_sessions.json"):
        for record in objects(_load(name)):
            kinds.session(record)
    for name in _corpus_files():
        loaded = _load(name)
        if name.endswith("_messages.json") or name.startswith("transcript_"):
            for message in objects(loaded):
                kinds.message(message)
        elif name.endswith("_parts.json") or name == "concurrent_denied_tool.json":
            for part in objects(loaded):
                kinds.part(part)
    for event in objects(_load("events.json")):
        kinds.event(event)
    for model in objects(_load("model_listing.json")):
        kinds.add("model", model)
    kinds.add("permission", _load("permission_request.json"))
    kinds.add("form", _load("form_request.json"))
    pages = _load("message_pages.json")
    assert isinstance(pages, dict)
    for page in pages.values():
        kinds.page(page)
    missing = _load("session_not_found.json")
    assert isinstance(missing, dict)
    kinds.add("not_found", missing["body"])
    return kinds


def _type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    return type(value).__name__


def _conform(kind: str, value: object, samples: list[object], path: str, problems: list[str]) -> None:
    allowed = {_type(sample) for sample in samples}
    if _type(value) not in allowed:
        problems.append(f"{path}: {_type(value)}, where the corpus has {sorted(allowed)}")
        return
    if _is_object(value):
        shaped = objects(samples)
        present = set[str]().union(*(set(sample) for sample in shaped))
        everywhere = set[str].intersection(*(set(sample) for sample in shaped))
        for key, item in value.items():
            if key not in present:
                problems.append(f"{path}.{key}: no corpus sample carries it")
            elif key not in _OPEN:
                _conform(kind, item, [sample[key] for sample in shaped if key in sample], f"{path}.{key}", problems)
        if path == kind:
            missing = everywhere - set(value) - set(_OMITTED.get(kind, {}))
            problems.extend(f"{path}.{key}: every corpus sample carries it" for key in sorted(missing))
    elif _is_array(value):
        items = [item for sample in samples if _is_array(sample) for item in sample]
        if items:
            for index, item in enumerate(value):
                _conform(kind, item, items, f"{path}[{index}]", problems)


def _check(mock: _Kinds, corpus: _Kinds, *, required: Iterable[str]) -> None:
    problems: list[str] = []
    for kind, values in sorted(mock.samples.items()):
        samples = corpus.samples.get(kind)
        if samples is None:
            if kind not in _UNCAPTURED:
                problems.append(f"{kind}: the corpus has no sample of it, and it is not declared uncaptured")
            continue
        for value in values:
            _conform(kind, value, list(samples), kind, problems)
    unexercised = sorted(set(required) - set(mock.samples))
    assert not unexercised, f"the exercise produced no {unexercised}"
    assert not problems, "\n".join(sorted(set(problems)))


# -- Exercising each artifact --


def _wait(predicate: Callable[[], bool], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("condition not reached")


def _first_pending(service: Service, session_id: str, path: str) -> Json:
    found: list[Json] = []

    def raised() -> bool:
        found[:] = objects(service.data("GET", f"/api/session/{session_id}/{path}"))
        return bool(found)

    _wait(raised)
    return found[0]


def _messages(service: Service, session_id: str) -> list[Json]:
    return objects(service.data("GET", f"/api/session/{session_id}/message"))


def _collect(service: Service, kinds: _Kinds, events: list[Json]) -> None:
    for record in objects(service.data("GET", "/api/session")):
        kinds.session(record)
        for message in objects(service.data("GET", f"/api/session/{record['id']}/message")):
            kinds.message(message)
    for event in events:
        kinds.event(event)
    for model in objects(service.data("GET", "/api/model")):
        kinds.add("model", model)
    _, first = service.call("GET", "/api/session/ses_conform/message?limit=1")
    kinds.page(first)
    _, whole = service.call("GET", "/api/session/ses_conform/message?limit=200")
    assert isinstance(whole, dict)
    cursor = whole["cursor"]
    assert isinstance(cursor, dict)
    _, tail = service.call("GET", f"/api/session/ses_conform/message?cursor={cursor['next']}")
    kinds.page(tail)
    status, missing = service.call("GET", "/api/session/ses_conform_missing/message")
    assert status == 404
    kinds.add("not_found", missing)


_REQUIRED = (
    "session",
    "message.user",
    "message.assistant",
    "message.idle",
    "message.compaction",
    "message.synthetic",
    "part.text",
    "part.tool",
    "state.completed",
    "state.error",
    "event.session.created",
    "event.session.step.started",
    "event.session.step.ended",
    "event.session.tool.called",
    "event.session.tool.success",
    "event.session.usage.updated",
    "event.session.execution.started",
    "event.session.execution.succeeded",
    "event.session.inbox.enqueued",
    "event.session.inbox.delivered",
    "envelope",
    "model",
    "permission",
    "form",
    "page",
    "not_found",
)


def _exercise(service: Service, repo: Path, prompts: dict[str, str], kinds: _Kinds) -> None:
    rules = [
        {"action": "shell", "resource": "touch asked*", "effect": "ask"},
        {"action": "shell", "resource": "touch denied*", "effect": "deny"},
    ]
    with service.events() as events:
        service.open_session("ses_conform", repo, rules)
        service.data("POST", "/api/session/ses_conform/prompt", {"text": prompts["turn"]})
        service.settle("ses_conform")
        service.data("POST", "/api/session/ses_conform/prompt", {"text": prompts["asked"]})
        request = _first_pending(service, "ses_conform", "permission")
        kinds.add("permission", request)
        service.call("POST", f"/api/session/ses_conform/permission/{request['id']}/reply", {"decision": "once"})
        service.settle("ses_conform")
        service.data("POST", "/api/session/ses_conform/prompt", {"text": prompts["denied"]})
        service.settle("ses_conform")
        form = prompts.get("form")
        if form is None:
            raised = service.data("POST", "/api/session/ses_conform/form", {"title": "t", "fields": [{"key": "k"}]})
        else:
            service.data("POST", "/api/session/ses_conform/prompt", {"text": form})
            raised = _first_pending(service, "ses_conform", "form")
        kinds.add("form", raised)
        assert isinstance(raised, dict)
        service.call("DELETE", f"/api/session/ses_conform/form/{raised['id']}")
        service.settle("ses_conform")
        service.call("POST", "/api/session/ses_conform/compact")
        service.settle("ses_conform")
        service.data("POST", "/api/session/ses_conform/prompt", {"text": prompts["background"]})
        _wait(lambda: any(m.get("type") == "synthetic" for m in _messages(service, "ses_conform")))
        service.settle("ses_conform")
    _collect(service, kinds, events)


_SURFACE_PROMPTS = {
    "turn": (
        "Use the shell tool exactly once to run `echo ROOT`. Then use the subagent tool exactly once to launch a "
        "`general` subagent whose instructions are: use the shell tool exactly once to run `echo CHILD` and report "
        "its exact output. Then report both outputs verbatim and stop."
    ),
    "asked": "Use the shell tool exactly once to run `touch asked`. Then reply DONE.",
    "denied": "Use the shell tool exactly once to run `touch denied`. If it is refused, reply REFUSED. Then reply DONE.",
    "background": (
        "Use the subagent tool exactly once, with background set to true, to launch a `general` subagent whose "
        "instructions are: use the shell tool exactly once to run `echo LATE` and report its output. "
        "Do not wait for it; reply STARTED immediately."
    ),
}

_ENGINE_PROMPTS = {
    "turn": (
        "reasoning('planning')\n"
        "usage(input=100, output=5, reasoning=3, cache_read=64, cost=0.01)\n"
        "shell('echo ROOT')\n"
        "step()\n"
        "child = subagent(\"text(shell('echo CHILD').output)\", description='Report the child')\n"
        "text('ROOT ' + child.output)\n"
    ),
    "asked": "shell('touch asked')\ntext('DONE')\n",
    "denied": "text(shell('touch denied').status)\n",
    "form": "form('blizzard question', [{'key': 'answer', 'type': 'string'}])\ntext('DONE')\n",
    "background": "subagent(\"text('LATE')\", background=True, wake=\"text('WOKE ' + wake_result())\")\ntext('STARTED')\n",
}


def test_the_cli_surface_artifact_answers_in_the_corpus_shapes(tmp_path: Path, start_service: Starter) -> None:
    service = start_service(artifact(tmp_path / "bin"))
    assert service.cli("service", "start").returncode == 0
    repo = tmp_path / "repo"
    repo.mkdir()
    mock = _Kinds()
    _exercise(service, repo, _SURFACE_PROMPTS, mock)
    _check(mock, _corpus(), required=_REQUIRED)


def test_the_engine_backed_artifact_answers_in_the_corpus_shapes(tmp_path: Path, start_service: Starter) -> None:
    binary = artifact(tmp_path / "bin", verb="emit-engine")
    service = start_service(binary, extra_env={fence.FENCE_ENV_VAR: fence.FENCE_ENV_VALUE})
    assert service.cli("service", "start").returncode == 0
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / fence.FENCE_MARKER_FILENAME).touch()
    mock = _Kinds()
    _exercise(service, repo, _ENGINE_PROMPTS, mock)
    _check(mock, _corpus(), required=(*_REQUIRED, "part.reasoning"))


def test_the_engines_provider_errors_take_the_corpus_shapes() -> None:
    corpus = _Kinds()
    for name in ("error_usage_limit.json", "error_unknown_model.json", "error_unknown_variant.json"):
        corpus.add("error", _load(name))
    mock = _Kinds()
    model = {"providerID": "openai", "id": "gpt-6-luna", "variant": "no-such"}
    for error in (
        failures.usage_limit(),
        failures.usage_limit(None, kind="provider.quota"),
        failures.unknown_model(model),
        failures.unknown_variant(model),
        failures.provider_refusal(),
        failures.overload(),
    ):
        mock.add("error", error)
    _check(mock, corpus, required=("error",))
    sourced = _load("error_shapes_from_source.json")
    assert _is_object(sourced)
    typed = {
        "provider_refusal": failures.provider_refusal()["type"],
        "overload": failures.overload()["type"],
        "unknown_model_or_variant": failures.unknown_model(model)["type"],
    }
    for name, kind in typed.items():
        entry = sourced[name]
        assert isinstance(entry, dict) and entry["type"] == kind, name
    limits = sourced["usage_limit"]
    assert _is_array(limits)
    assert {str(entry["type"]) for entry in objects(limits)} == set(failures.USAGE_LIMIT_TYPES)
    assert failures.unknown_variant(model)["message"] == "Variant unavailable for openai/gpt-6-luna: no-such"
