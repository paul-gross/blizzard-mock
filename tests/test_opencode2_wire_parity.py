"""The engine-backed OpenCode 2 mock's answers, fed through `blizzard`'s production OpenCode 2 readers (the shapes,
the transcript normalizer, and the page and info readers) under blizzard's own interpreter: every session, message,
page, permission request, form, error record, event, inbox item, model and ``/api/info`` answer must parse, its
version one blizzard binds to OpenCode 2, since a mock the real driver refuses is a mock nothing downstream can trust.
Holding the same records to the corpus's keys is `test_opencode2_corpus_conformance.py`'s; this tier holds them to the
code that reads them."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from blizzard_mock.harness.opencode2_engine.domain import fence
from tests.opencode2_service import Json, Service, Starter, artifact, objects

pytestmark = pytest.mark.needs_blizzard

_BLIZZARD = Path(os.environ.get("BLIZZARD_SOURCE") or Path(__file__).resolve().parents[2] / "blizzard")
_FENCE = {fence.FENCE_ENV_VAR: fence.FENCE_ENV_VALUE}

#: Runs under blizzard's interpreter: reads ``{kind: [record, ...]}`` on stdin, parses every record with the reader
#: for its kind, and prints ``{kind: count}`` — or the first refusal, naming its kind, record, and message.
_PARSER_DRIVER = """
import json, sys
from blizzard.runner.harness.opencode_v2 import client, declaration, shapes
from blizzard.runner.harness.opencode_v2.transcript import normalizer

def transcript(record):
    message = shapes.parse_message(record)
    normalizer.message_turns(message)
    normalizer.spawned_children(message)

def page(record):
    try:
        client.parse_message_page(record)
    except client.ServiceShapeError as exc:
        raise shapes.OpenCodeTwoShapeError(str(exc)) from exc

def info(record):
    try:
        answer = client.parse_service_info(record)
    except client.ServiceShapeError as exc:
        raise shapes.OpenCodeTwoShapeError(str(exc)) from exc
    if not declaration.OPENCODE_V2_DECLARATION.admits_version(answer.version):
        raise shapes.OpenCodeTwoShapeError(f"version {answer.version} is not one OpenCode 2 binds")

readers = {
    "session": shapes.parse_session,
    "message": shapes.parse_message,
    "permission": shapes.parse_permission_request,
    "form": shapes.parse_form_request,
    "error": shapes.parse_error,
    "event": shapes.parse_event,
    "inbox": shapes.parse_inbox_item,
    "model": shapes.parse_listed_model,
    "transcript": transcript,
    "page": page,
    "info": info,
}
counts = {}
for kind, records in json.load(sys.stdin).items():
    for record in records:
        try:
            readers[kind](record)
        except shapes.OpenCodeTwoShapeError as exc:
            print(json.dumps({"refused": kind, "message": str(exc), "record": record}))
            sys.exit(0)
    counts[kind] = len(records)
print(json.dumps(counts))
"""


def _parse_through_blizzard(records: dict[str, list[Json]]) -> Json:
    python = _BLIZZARD.resolve() / ".venv" / "bin" / "python"
    assert python.is_file(), (
        f"no blizzard virtual environment at {python} — parity is unverifiable, not green; "
        f"run `uv sync` in {_BLIZZARD} or set $BLIZZARD_SOURCE"
    )
    proc = subprocess.run(
        [str(python), "-c", _PARSER_DRIVER], input=json.dumps(records), capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _wait_for_list(service: Service, path: str, timeout: float = 20) -> list[Json]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = objects(service.data("GET", path))
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"nothing listed at {path}")


def test_every_engine_record_parses_through_blizzards_opencode_2_readers(
    tmp_path: Path, start_service: Starter
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / fence.FENCE_MARKER_FILENAME).touch()
    service = start_service(artifact(tmp_path / "bin", verb="emit-engine"), extra_env=_FENCE)
    assert service.cli("service", "start").returncode == 0
    records: dict[str, list[Json]] = {kind: [] for kind in ("permission", "form", "inbox", "error")}

    with service.events() as events:
        service.open_session("ses_parity_asks", repo)
        script = "permission_request('shell', 'touch x')\nform('Pick', [{'key': 'a', 'type': 'string'}])\ntext('ok')"
        service.data("POST", "/api/session/ses_parity_asks/prompt", {"text": script})
        records["permission"] = _wait_for_list(service, "/api/session/ses_parity_asks/permission")
        request_id = records["permission"][0]["id"]
        service.call("POST", f"/api/session/ses_parity_asks/permission/{request_id}/reply", {"decision": "reject"})
        records["form"] = _wait_for_list(service, "/api/session/ses_parity_asks/form")
        service.call("DELETE", f"/api/session/ses_parity_asks/form/{records['form'][0]['id']}")
        service.settle("ses_parity_asks")

        service.open_session("ses_parity_wake", repo)
        child = "shell('sleep 1')\ntext('CHILD')"
        wake = "text('woken ' + wake_result())"
        script = f"subagent({child!r}, background=True, wake={wake!r})\ntext('STARTED')"
        service.data("POST", "/api/session/ses_parity_wake/prompt", {"text": script})
        records["inbox"] = _wait_for_list(service, "/api/session/ses_parity_wake/inbox")
        _settle_tree(service, "ses_parity_wake")

        service.open_session("ses_parity_limit", repo)
        service.data("POST", "/api/session/ses_parity_limit/prompt", {"text": "usage_limit(retry_after_ms=1000)"})
        limited = service.settle("ses_parity_limit")
    received = list(events)

    sessions = [service.data("GET", f"/api/session/{sid}") for sid in ("ses_parity_asks", "ses_parity_wake")]
    sessions += objects(service.data("GET", "/api/session?parentID=ses_parity_wake"))
    messages = [
        message
        for session in sessions
        if isinstance(session, dict)
        for message in objects(service.data("GET", f"/api/session/{session['id']}/message"))
    ]
    records["error"] = [error for m in limited if isinstance(error := m.get("error"), dict)]
    pages = [service.call("GET", f"/api/session/{s['id']}/message?limit=1")[1] for s in sessions if isinstance(s, dict)]
    records["page"] = [page for page in pages if isinstance(page, dict)]
    records.update(
        session=[s for s in sessions if isinstance(s, dict)],
        message=messages + limited,
        transcript=messages + limited,
        event=received,
        model=objects(service.data("GET", "/api/model")),
        info=objects([service.call("GET", "/api/info")[1]]),
    )

    counts = _parse_through_blizzard(records)

    assert "refused" not in counts, counts
    assert all(isinstance(n := counts.get(kind), int) and n > 0 for kind in records), counts


def _settle_tree(service: Service, root: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = service.data("GET", "/api/session/active")
        inbox = objects(service.data("GET", f"/api/session/{root}/inbox"))
        if isinstance(active, dict) and not active and not inbox:
            return
        time.sleep(0.2)
    raise AssertionError(f"{root}'s tree did not settle")
