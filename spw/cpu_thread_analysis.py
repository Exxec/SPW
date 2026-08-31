from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .jfr_events import event_read_status, event_thread_identity, read_events_with_status, resolve_thread_labels, top_frame

_TOP_N = 25


def analyze_cpu_and_threads(
    jfr_tool: Path,
    recording_path: Path,
    execution_samples: list[dict[str, Any]] | None = None,
    execution_samples_limitation: str | None = None,
) -> dict[str, Any]:
    """Summarize sampled CPU, thread lifecycle, and per-thread CPU load from a recording.

    Every count here is only as complete as the capture level allowed:
    `PASSIVE` recordings carry no `jdk.ExecutionSample` events at all, so a
    `PASSIVE`-captured report correctly shows zero samples rather than an
    error -- callers should check `execution_samples_available`. Pass
    `execution_samples` when the caller (e.g. attribution) has already read
    them, to avoid parsing a potentially large recording twice; pass
    `execution_samples_limitation` alongside it if that read failed rather
    than genuinely finding zero events, so it is not silently indistinguishable
    from "PASSIVE correctly has none". The returned `event_reads` gives every
    event type's `{"status", "event_count", "reason"}` record (see
    `event_read_status`) -- a `"READ_FAILED"` status means the corresponding
    count is not trustworthy as "zero", it is "unknown".
    """

    event_reads: dict[str, dict[str, Any]] = {}
    if execution_samples is None:
        execution_samples, execution_samples_limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.ExecutionSample"])
    event_reads["jdk.ExecutionSample"] = event_read_status(execution_samples, execution_samples_limitation)

    thread_cpu_loads, limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.ThreadCPULoad"])
    event_reads["jdk.ThreadCPULoad"] = event_read_status(thread_cpu_loads, limitation)
    thread_starts, limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.ThreadStart"])
    event_reads["jdk.ThreadStart"] = event_read_status(thread_starts, limitation)
    thread_ends, limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.ThreadEnd"])
    event_reads["jdk.ThreadEnd"] = event_read_status(thread_ends, limitation)
    jvm_cpu_loads, limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.CPULoad"])
    event_reads["jdk.CPULoad"] = event_read_status(jvm_cpu_loads, limitation)

    # Grouping by name alone risks blending two distinct threads that
    # happen to share a name (a real pattern for pooled worker threads);
    # `resolve_thread_labels` disambiguates only names that actually
    # collide, keyed by `javaThreadId` which -- unlike a name -- is never
    # reused within one JVM's lifetime.
    exec_identities = [event_thread_identity(event) for event in execution_samples]
    exec_labels = resolve_thread_labels(identity for identity in exec_identities if identity is not None)

    samples_by_thread: Counter[str] = Counter()
    samples_by_top_frame: Counter[tuple[str, str]] = Counter()
    for event, identity in zip(execution_samples, exec_identities):
        label = exec_labels[identity] if identity is not None else "UNKNOWN"
        samples_by_thread[label] += 1
        frame = top_frame(event)
        if frame and frame.get("class_name"):
            samples_by_top_frame[(frame["class_name"], frame.get("method_name") or "?")] += 1

    cpu_load_identities = [event_thread_identity(event) for event in thread_cpu_loads]
    cpu_load_labels = resolve_thread_labels(identity for identity in cpu_load_identities if identity is not None)
    thread_cpu_sums: dict[str, list[float]] = defaultdict(list)
    for event, identity in zip(thread_cpu_loads, cpu_load_identities):
        if identity is None:
            continue
        user = event.get("user") or 0.0
        system = event.get("system") or 0.0
        thread_cpu_sums[cpu_load_labels[identity]].append(user + system)
    average_thread_cpu_fraction = {name: sum(values) / len(values) for name, values in thread_cpu_sums.items()}

    start_identities = {identity for identity in (event_thread_identity(event) for event in thread_starts) if identity is not None}
    end_identities = {identity for identity in (event_thread_identity(event) for event in thread_ends) if identity is not None}
    still_running_identities = start_identities - end_identities
    lifecycle_labels = resolve_thread_labels(start_identities | end_identities)
    still_running = sorted({lifecycle_labels[identity] for identity in still_running_identities})

    jvm_user = [event.get("jvmUser", 0.0) for event in jvm_cpu_loads]
    jvm_system = [event.get("jvmSystem", 0.0) for event in jvm_cpu_loads]
    machine_total = [event.get("machineTotal", 0.0) for event in jvm_cpu_loads]
    average_jvm_cpu = {
        "user": sum(jvm_user) / len(jvm_user) if jvm_user else None,
        "system": sum(jvm_system) / len(jvm_system) if jvm_system else None,
        "machine_total": sum(machine_total) / len(machine_total) if machine_total else None,
    }

    return {
        "execution_samples_available": bool(execution_samples),
        "total_execution_samples": len(execution_samples),
        "samples_by_thread": dict(samples_by_thread.most_common(_TOP_N)),
        "samples_by_top_frame": [
            {"class_name": class_name, "method_name": method_name, "samples": count}
            for (class_name, method_name), count in samples_by_top_frame.most_common(_TOP_N)
        ],
        "average_thread_cpu_fraction": dict(sorted(average_thread_cpu_fraction.items(), key=lambda item: item[1], reverse=True)[:_TOP_N]),
        "thread_lifecycle": {
            "started": len(start_identities),
            "ended": len(end_identities),
            "still_running_at_capture_end": still_running,
        },
        "average_jvm_cpu": average_jvm_cpu,
        "event_reads": event_reads,
    }
