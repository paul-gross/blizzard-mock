"""The managed service: ``service start`` detaches it, it listens on an ephemeral loopback port, registers itself with
a fresh id and password, and answers the ``/api/*`` subset with Basic authentication; ``service stop`` ends it and
``service restart`` replaces it with a new lifetime."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import queue
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import FrameType
from typing import cast
from urllib.parse import parse_qs, unquote, urlsplit

from ..domain import records
from ..levers import Lever
from . import paths, registration
from .engine import ConflictError, Engine, NotFoundError, restart_in_background

START_WAIT_SECONDS = 15.0
STOP_WAIT_SECONDS = 10.0

type Json = dict[str, object]


def _version(baked: str, levers: frozenset[Lever]) -> str:
    """The version the service reports: the baked one, or under ``VERSION_MISMATCH`` the next patch release."""
    if Lever.VERSION_MISMATCH not in levers:
        return baked
    major, _, rest = baked.partition(".")
    minor, _, patch = rest.partition(".")
    return f"{major}.{minor or 0}.{int(patch or 0) + 1}"


def _answers(record: Json) -> bool:
    try:
        registration.call(record, "GET", "/api/info")
    except (OSError, ValueError):
        return False
    return True


def start(env: Mapping[str, str], argv0: str, levers: frozenset[Lever], version: str) -> int:
    path = paths.registration_path(env)
    current = registration.read(path)
    if current is not None and registration.pid_alive(int(str(current.get("pid", 0)))) and _answers(current):
        print(f"opencode service already running at {current.get('url')}")
        return 0
    if Lever.NEVER_REGISTERED in levers:
        print("opencode service started")
        return 0
    if Lever.NEVER_ANSWERS in levers:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        registration.write(
            path,
            {
                "id": records.new_id("svc"),
                "version": _version(version, levers),
                "url": f"http://127.0.0.1:{port}",
                "pid": os.getpid(),
                "password": secrets.token_urlsafe(24),
            },
        )
        print("opencode service started")
        return 0
    log = paths.service_log_path(env)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as sink:
        child = subprocess.Popen(
            [sys.executable, argv0, "service", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=sink,
            start_new_session=True,
        )
    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        record = registration.read(path)
        if record is not None and record.get("pid") == child.pid:
            print(f"opencode service started at {record.get('url')}")
            return 0
        if child.poll() is not None:
            break
        time.sleep(0.1)
    print("opencode service did not start", file=sys.stderr)
    return 1


def stop(env: Mapping[str, str]) -> int:
    path = paths.registration_path(env)
    current = registration.read(path)
    if current is None:
        print("opencode service not running")
        return 0
    pid = int(str(current.get("pid", 0)))
    if pid != os.getpid() and registration.pid_alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        while registration.pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if registration.pid_alive(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
    registration.remove_if_owned(path, str(current.get("id")))
    print("opencode service stopped")
    return 0


def status(env: Mapping[str, str]) -> int:
    current = registration.read(paths.registration_path(env))
    if current is None or not registration.pid_alive(int(str(current.get("pid", 0)))):
        print("opencode service not running")
        return 1
    print(json.dumps({"status": "running", "url": current.get("url"), "pid": current.get("pid")}))
    return 0


def restart(env: Mapping[str, str], argv0: str, levers: frozenset[Lever], version: str) -> int:
    stop(env)
    return start(env, argv0, levers, version)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, engine: Engine, password: str, version: str, levers: frozenset[Lever]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.engine = engine
        self.password = password
        self.version = version
        self.levers = levers
        self.url = f"http://127.0.0.1:{self.server_address[1]}"


_SESSION = re.compile(r"^/api/session/(?P<id>[^/]+)(?P<rest>(?:/.*)?)$")


class _Handler(BaseHTTPRequestHandler):
    @property
    def service(self) -> _Server:
        return cast(_Server, self.server)

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _authorized(self) -> bool:
        expected = base64.b64encode(f"opencode:{self.service.password}".encode()).decode()
        return secrets.compare_digest(self.headers.get("authorization", ""), f"Basic {expected}")

    def _body(self) -> Json:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        decoded = json.loads(self.rfile.read(length))
        return decoded if isinstance(decoded, dict) else {}

    def _send(self, status: int, payload: object | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        split = urlsplit(self.path)
        query = {key: values[-1] for key, values in parse_qs(split.query).items()}
        try:
            self._route(method, split.path, query)
        except NotFoundError as exc:
            self._send(404, {"error": f"not found: {exc}"})
        except ConflictError as exc:
            self._send(409, {"error": f"already exists: {exc}"})
        except (ValueError, KeyError, TypeError) as exc:
            self._send(400, {"error": str(exc)})

    def _route(self, method: str, path: str, query: Mapping[str, str]) -> None:
        engine, levers = self.service.engine, self.service.levers
        if path.startswith("/api/experimental/"):
            self._send(404, {"error": "not found"})
        elif (method, path) == ("GET", "/api/info"):
            self._send(200, records.service_info(self.service.version, self.service.url, os.getpid()))
        elif (method, path) == ("GET", "/api/model"):
            self._send(200, {"data": records.model_listing(priced=Lever.NO_PRICES not in levers)})
        elif (method, path) == ("GET", "/api/event"):
            self._events()
        elif (method, path) == ("GET", "/api/session"):
            self._send(200, {"data": engine.children(query.get("parentID"))})
        elif (method, path) == ("POST", "/api/session"):
            self._send(200, {"data": engine.create(self._body()).record()})
        elif (method, path) == ("GET", "/api/session/active"):
            self._send(200, {"data": engine.active()})
        elif match := _SESSION.match(path):
            self._session(method, unquote(match.group("id")), match.group("rest"), query)
        else:
            self._send(404, {"error": "not found"})

    def _session(self, method: str, sid: str, rest: str, query: Mapping[str, str]) -> None:
        engine, levers = self.service.engine, self.service.levers
        parts = [unquote(piece) for piece in rest.split("/") if piece]
        route = (method, *parts[:1], *(["{id}"] if len(parts) > 1 else []), *parts[2:])
        if route == ("GET",):
            self._send(200, {"data": engine.get(sid).record()})
        elif route == ("PATCH",):
            engine.update(sid, self._body())
            self._send(204)
        elif route == ("DELETE",):
            engine.get(sid)
            engine.remove(sid)
            self._send(204)
        elif route == ("PUT", "environment"):
            variables = self._body().get("variables")
            engine.set_environment(sid, variables if isinstance(variables, dict) else {})
            self._send(204)
        elif route == ("POST", "prompt"):
            body = self._body()
            delivery = body.get("delivery")
            self._send(
                200, {"data": engine.prompt(sid, str(body["text"]), str(delivery) if delivery is not None else None)}
            )
        elif route == ("POST", "interrupt"):
            self._send(200, {"interrupted": engine.interrupt(sid)})
        elif route == ("POST", "compact"):
            engine.compact(sid)
            self._send(204)
        elif route == ("POST", "model"):
            model = self._body().get("model")
            engine.switch_model(sid, model if isinstance(model, dict) else {})
            self._send(204)
        elif route == ("POST", "agent"):
            engine.switch_agent(sid, str(self._body().get("agent")))
            self._send(204)
        elif route == ("GET", "message"):
            limit = query.get("limit")
            listed = [
                _shaped(message, levers)
                for message in engine.messages(
                    sid, query.get("order", "asc"), int(limit) if limit else None, query.get("cursor")
                )
            ]
            self._send(200, {"data": {"items": listed} if Lever.DISTORTED_SHAPES in levers else listed})
        elif route == ("GET", "message", "{id}"):
            self._send(200, {"data": _shaped(engine.message(sid, parts[1]), levers)})
        elif route == ("GET", "inbox"):
            self._send(200, {"data": engine.inbox(sid)})
        elif route == ("GET", "permission"):
            self._send(200, {"data": engine.permission_requests(sid)})
        elif route == ("POST", "permission", "{id}", "reply"):
            engine.reply_permission(sid, parts[1], str(self._body().get("decision")))
            self._send(204)
        elif route == ("GET", "form"):
            self._send(200, {"data": engine.forms(sid)})
        elif route == ("POST", "form"):
            self._send(200, {"data": engine.create_form(sid, self._body())})
        elif route == ("DELETE", "form", "{id}"):
            engine.cancel_form(sid, parts[1])
            self._send(204)
        else:
            self._send(404, {"error": "not found"})

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        if Lever.EVENTS_DROPPED in self.service.levers:
            return
        engine = self.service.engine
        subscriber = engine.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = subscriber.get(timeout=5)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                else:
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.flush()
        except OSError:
            return
        finally:
            engine.unsubscribe(subscriber)


def _shaped(message: Json, levers: frozenset[Lever]) -> Json:
    """``message`` as the API answers it: under ``UNKNOWN_PART_TYPES`` its tool parts carry a type no client knows."""
    if Lever.UNKNOWN_PART_TYPES not in levers:
        return message
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "tool":
                part["type"] = "tool-call"
    return message


def serve(env: Mapping[str, str], argv0: str, levers: frozenset[Lever], version: str) -> int:
    """The service's own process: listen, register, resume what the last lifetime was running, and answer."""
    path = paths.registration_path(env)
    log = paths.service_log_path(env)
    engine = Engine(levers, paths.store_path(env), lambda: restart_in_background(argv0, log))
    interrupted = engine.load()
    server = _Server(engine, secrets.token_urlsafe(24), _version(version, levers), levers)
    registration_id = records.new_id("svc")

    def _shutdown(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        engine.halt()
        registration.remove_if_owned(path, registration_id)
        os._exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    registration.write(
        path,
        {
            "id": registration_id,
            "version": server.version,
            "url": server.url,
            "pid": os.getpid(),
            "password": server.password,
        },
    )
    engine.resume(interrupted)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        signal.pause()


__all__ = ["restart", "serve", "start", "status", "stop"]
