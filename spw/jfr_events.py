from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>[0-9.]+)D)?"
    r"(?:T(?:(?P<hours>[0-9.]+)H)?(?:(?P<minutes>[0-9.]+)M)?(?:(?P<seconds>[0-9.]+)S)?)?$"
)


def jfr_tool_path(java_executable: Path | None, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    if java_executable is not None:
        candidate = java_executable.with_name(f"jfr{java_executable.suffix}")
        if candidate.exists():
            return candidate
    return None


def read_events_with_status(jfr_tool: Path, recording_path: Path, event_types: Iterable[str]) -> tuple[list[dict[str, Any]], str | None]:
    """Read the requested JFR event types from a recording via `jfr print --json`.

    Each returned dict is the event's `values` payload with its event
    `type` name merged in. Also returns why an empty list might not mean
    "genuinely zero events" -- there is no plain `read_events` that
    discards this: every caller must handle the distinction explicitly,
    typically via `event_read_status`, rather than silently treating a
    failed read as a confirmed zero.

    The second element is `None` on success -- including a real, positive
    zero-event result (e.g. a `PASSIVE` recording correctly has no
    `jdk.ExecutionSample` events) -- or a short, stable, machine-readable
    reason otherwise: `"jfr_tool_not_found"`, `"jfr_print_timed_out"`
    (a real risk for a large `DEEP_DIAGNOSTIC` recording, since verbose
    per-event JSON with full stack traces can dwarf the recording file
    itself), `"jfr_print_failed:<returncode>"`, or
    `"jfr_print_output_unparseable"`.
    """

    command = [str(jfr_tool), "print", "--events", ",".join(sorted(set(event_types))), "--json", str(recording_path)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=True)
    except FileNotFoundError:
        return [], "jfr_tool_not_found"
    except subprocess.TimeoutExpired:
        return [], "jfr_print_timed_out"
    except subprocess.CalledProcessError as exc:
        return [], f"jfr_print_failed:{exc.returncode}"
    except OSError:
        return [], "jfr_print_os_error"
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return [], "jfr_print_output_unparseable"
    events = data.get("recording", {}).get("events", [])
    return [{"type": event.get("type"), **event.get("values", {})} for event in events], None


def event_read_status(events: list[dict[str, Any]], limitation: str | None) -> dict[str, Any]:
    """Build the canonical per-event-type read-status record for one `read_events_with_status` call.

    A bare event count is ambiguous: zero can mean "genuinely no events" or
    "the read failed and returned nothing". This makes that distinction an
    explicit, machine-checkable field instead of something a reader has to
    infer by separately checking a limitations string:
    `{"status": "OK", "event_count": N, "reason": None}` (N may be 0 -- a
    confirmed, genuine zero) only when the read itself succeeded;
    `{"status": "READ_FAILED", "event_count": None, "reason": limitation}`
    otherwise, so a failed read is never mistaken for a real zero.
    """

    if limitation is not None:
        return {"status": "READ_FAILED", "event_count": None, "reason": limitation}
    return {"status": "OK", "event_count": len(events), "reason": None}


def parse_iso_duration_seconds(value: str | None) -> float | None:
    """Parse a JFR-style ISO-8601 duration string (e.g. `PT0.0000462S`) into seconds."""

    if not value:
        return None
    match = _ISO_DURATION.match(value)
    if not match:
        return None
    parts = match.groupdict()
    if all(part is None for part in parts.values()):
        return None
    days = float(parts["days"] or 0.0)
    hours = float(parts["hours"] or 0.0)
    minutes = float(parts["minutes"] or 0.0)
    seconds = float(parts["seconds"] or 0.0)
    return days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds


def parse_jfr_timestamp(value: str | None) -> datetime | None:
    """Parse a JFR `startTime` string (ISO-8601 with an offset) into a `datetime`."""

    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def event_thread_name(event: dict[str, Any]) -> str | None:
    for key in ("sampledThread", "eventThread", "thread"):
        thread = event.get(key)
        if isinstance(thread, dict):
            name = thread.get("javaName") or thread.get("osName")
            if name:
                return str(name)
    return None


ThreadIdentity = tuple[str, int | None]


def event_thread_identity(event: dict[str, Any]) -> ThreadIdentity | None:
    """Return `(name, java_thread_id)` for an event's thread, or `None` if it has none.

    `javaThreadId` is assigned once per `Thread` object and never reused
    within a JVM's lifetime, unlike its name -- Java thread pools commonly
    reuse names like `pool-1-thread-1` for successive, distinct worker
    threads. Prefer this over `event_thread_name` alone wherever two
    samples from differently-lived threads must not be silently blended
    together.
    """

    for key in ("sampledThread", "eventThread", "thread"):
        thread = event.get(key)
        if isinstance(thread, dict):
            name = thread.get("javaName") or thread.get("osName")
            if not name:
                continue
            java_id = thread.get("javaThreadId")
            return str(name), (int(java_id) if isinstance(java_id, (int, float)) else None)
    return None


def resolve_thread_labels(identities: Iterable[ThreadIdentity]) -> dict[ThreadIdentity, str]:
    """Map each `(name, java_thread_id)` identity to a display label, one per distinct identity.

    Grouping purely by name risks silently blending two distinct threads
    that happen to share a name. This disambiguates only names that
    actually collide (appending `#<id>` to those), so the common case --
    every name unique -- is unaffected and stays exactly the plain name.
    """

    # Materialized once: this is iterated twice below, and a caller may
    # reasonably pass a single-use generator expression.
    identities = list(identities)

    ids_by_name: dict[str, set[int | None]] = defaultdict(set)
    for name, java_id in identities:
        ids_by_name[name].add(java_id)

    labels: dict[ThreadIdentity, str] = {}
    for name, java_id in identities:
        identity = (name, java_id)
        if identity in labels:
            continue
        if len(ids_by_name[name]) > 1 and java_id is not None:
            labels[identity] = f"{name}#{java_id}"
        else:
            labels[identity] = name
    return labels


def top_frame(event: dict[str, Any]) -> dict[str, Any] | None:
    stack = event.get("stackTrace")
    if not isinstance(stack, dict):
        return None
    frames = stack.get("frames") or []
    if not frames:
        return None
    return _frame_info(frames[0])


def stack_class_names(event: dict[str, Any]) -> list[str]:
    stack = event.get("stackTrace")
    if not isinstance(stack, dict):
        return []
    names = []
    for frame in stack.get("frames") or []:
        info = _frame_info(frame)
        if info and info["class_name"]:
            names.append(info["class_name"])
    return names


def _frame_info(frame: dict[str, Any]) -> dict[str, Any] | None:
    method = frame.get("method") or {}
    klass = method.get("type") or {}
    class_name = klass.get("name")
    return {
        "class_name": class_name.replace("/", ".") if class_name else None,
        "method_name": method.get("name"),
        "line_number": frame.get("lineNumber"),
        "frame_type": frame.get("type"),
    }
