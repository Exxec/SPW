from __future__ import annotations

from collections import Counter
from typing import Any

from .jfr_events import event_thread_identity, resolve_thread_labels, stack_class_names

_RUNTIME_PACKAGE_PREFIXES = ("java/", "javax/", "jdk/", "sun/", "com/sun/", "org/w3c/", "org/xml/", "netscape/")


def _to_slash(dotted_class_name: str) -> str:
    return dotted_class_name.replace(".", "/")


def _is_runtime_class(slash_class_name: str) -> bool:
    return slash_class_name.startswith(_RUNTIME_PACKAGE_PREFIXES)


def _package_prefix(slash_class_name: str, depth: int = 2) -> str | None:
    parts = slash_class_name.split("/")[:-1]
    if not parts:
        return None
    return "/".join(parts[:depth])


def resolve_class_attribution(dotted_class_name: str, class_index: dict[str, list[str]], package_prefix_index: dict[str, list[str]]) -> dict[str, Any]:
    """Resolve one class name to a mod, in the design's evidence-ranked order.

    Order: exact class-to-JAR index, then package prefix, then "the JVM/JDK
    itself" (a real, positive answer — not a mod), then unknown. Source
    layout and JAR manifest evidence are not available from a JFR
    recording alone and are left to later attribution work.
    """

    slash_name = _to_slash(dotted_class_name)

    owners = class_index.get(slash_name)
    if owners:
        confidence = "EXACT" if len(owners) == 1 else "AMBIGUOUS"
        return {"class_name": dotted_class_name, "owner_candidates": owners, "confidence": confidence, "attribution_path": "exact class-to-JAR index"}

    prefix = _package_prefix(slash_name)
    if prefix is not None:
        owners = package_prefix_index.get(prefix)
        if owners:
            confidence = "LIKELY" if len(owners) == 1 else "AMBIGUOUS"
            return {"class_name": dotted_class_name, "owner_candidates": owners, "confidence": confidence, "attribution_path": "package prefix"}

    if _is_runtime_class(slash_name):
        return {"class_name": dotted_class_name, "owner_candidates": [], "confidence": "UNOWNED", "attribution_path": "runtime/JDK package prefix"}

    return {"class_name": dotted_class_name, "owner_candidates": [], "confidence": "UNKNOWN", "attribution_path": "unknown"}


def _first_application_class(class_names: list[str]) -> str | None:
    for name in class_names:
        if not _is_runtime_class(_to_slash(name)):
            return name
    return class_names[0] if class_names else None


def attribute_execution_samples(execution_sample_events: list[dict[str, Any]], class_index: dict[str, list[str]], package_prefix_index: dict[str, list[str]]) -> dict[str, Any]:
    """Aggregate execution-sample stacks into attributed CPU-sample counts.

    Each sample is attributed by its first non-runtime frame (skipping
    `java.*`/`jdk.*`/etc. frames that would otherwise dominate every
    stack); a sample whose entire stack is runtime code is attributed to
    the runtime itself, which is a real answer, not a gap.
    """

    # Disambiguate only thread names that actually collide across distinct
    # `javaThreadId`s (see `resolve_thread_labels`), so pooled/reused
    # thread names don't silently blend two different threads' samples
    # into one bucket.
    identities = [event_thread_identity(event) for event in execution_sample_events]
    thread_labels = resolve_thread_labels(identity for identity in identities if identity is not None)

    by_owner: Counter[str] = Counter()
    by_thread_owner: Counter[tuple[str, str]] = Counter()
    total = 0
    for event, identity in zip(execution_sample_events, identities):
        class_names = stack_class_names(event)
        if not class_names:
            continue
        total += 1
        target_class = _first_application_class(class_names)
        attribution = resolve_class_attribution(target_class, class_index, package_prefix_index)
        owners = attribution["owner_candidates"]
        label = "+".join(owners) if owners else attribution["confidence"]
        by_owner[label] += 1
        thread_label = thread_labels[identity] if identity is not None else "UNKNOWN"
        by_thread_owner[(thread_label, label)] += 1

    return {
        "total_samples_attributed": total,
        "samples_by_owner": dict(by_owner.most_common()),
        "samples_by_thread_and_owner": {f"{thread}::{owner}": count for (thread, owner), count in by_thread_owner.most_common()},
    }
