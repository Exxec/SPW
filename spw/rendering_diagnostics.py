from __future__ import annotations

import re
from typing import Any

from .jfr_events import event_thread_identity, resolve_thread_labels, stack_class_names

_RENDER_THREAD_NAME_PATTERNS = (
    re.compile(r"lwjgl", re.I),
    re.compile(r"render", re.I),
    re.compile(r"^main$", re.I),
    re.compile(r"opengl", re.I),
)

# Real evidence, not a guess: a live capture against a real, heavily-modded
# Starsector installation with Fast Rendering active found the actual
# hottest thread (~86% of all execution samples) carrying the JVM's own
# generic default name ("Thread-N") -- invisible to name-pattern matching --
# while its own sampled stacks were dominated by exactly these packages.
# Starsector's own core render/game loop thread is never given a
# descriptive name by the base game, unlike some render-adjacent mods
# (e.g. Fast Rendering) which do name their own worker threads.
# `com.genir.renderer.` is Fast Rendering's own bridge/interceptor package
# (observed directly in that capture, e.g. `com.genir.renderer.overrides.Sync`,
# `com.genir.renderer.bridge.context.*`) -- present only when Fast Rendering
# is installed and active; the other prefixes are vanilla/base-game paths.
_RENDER_RELATED_CLASS_PREFIXES = (
    "org.lwjgl.",
    "com.fs.graphics.",
    "com.fs.starfarer.renderers.",
    "com.genir.renderer.",
)

_CONTENT_MATCH_THRESHOLD = 0.5


def _looks_like_render_thread(name: str) -> bool:
    return any(pattern.search(name) for pattern in _RENDER_THREAD_NAME_PATTERNS)


def _has_render_related_frame(class_names: list[str]) -> bool:
    return any(name.startswith(prefix) for name in class_names for prefix in _RENDER_RELATED_CLASS_PREFIXES)


def analyze_rendering(cpu_thread_analysis: dict[str, Any], execution_samples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Best-effort rendering-thread diagnostics from CPU/thread analysis and sampled stack content.

    There is no standard JFR event for GL stalls or render-thread state.
    Two independent, honestly-labeled signals are combined: a thread whose
    *name* matches common LWJGL/render conventions (Starsector uses LWJGL;
    some render-adjacent mods name their own threads descriptively), and a
    thread whose *sampled stacks* are dominated by known rendering-related
    packages even when its name is a JVM default -- added after a real
    live capture showed name-matching alone significantly undercounts
    render activity (see module docstring above `_RENDER_RELATED_CLASS_PREFIXES`).
    Pass `execution_samples` (the same list `attribute_execution_samples`
    and `analyze_cpu_and_threads` use) to enable the content-based signal;
    without it, this falls back to name-matching only, unchanged from
    before. Neither signal is a reconstruction of frame timing or GL call
    stalls; RenderDoc or engine-level integration remains out of scope.
    """

    thread_names = set(cpu_thread_analysis.get("samples_by_thread", {}).keys()) | set(cpu_thread_analysis.get("average_thread_cpu_fraction", {}).keys())
    name_matched = sorted(name for name in thread_names if _looks_like_render_thread(name))

    execution_samples = execution_samples or []
    identities = [event_thread_identity(event) for event in execution_samples]
    labels = resolve_thread_labels(identity for identity in identities if identity is not None)

    render_related_counts: dict[str, int] = {}
    total_counts: dict[str, int] = {}
    for event, identity in zip(execution_samples, identities):
        if identity is None:
            continue
        label = labels[identity]
        total_counts[label] = total_counts.get(label, 0) + 1
        if _has_render_related_frame(stack_class_names(event)):
            render_related_counts[label] = render_related_counts.get(label, 0) + 1

    render_related_fraction = {name: render_related_counts.get(name, 0) / total_counts[name] for name in total_counts}
    content_matched = sorted(name for name, fraction in render_related_fraction.items() if fraction >= _CONTENT_MATCH_THRESHOLD)

    render_thread_names = sorted(set(name_matched) | set(content_matched))
    render_samples = {name: cpu_thread_analysis.get("samples_by_thread", {}).get(name, total_counts.get(name, 0)) for name in render_thread_names}
    render_cpu_fraction = {name: cpu_thread_analysis.get("average_thread_cpu_fraction", {}).get(name) for name in render_thread_names}

    return {
        "render_thread_names": render_thread_names,
        "render_thread_names_matched": name_matched,
        "render_thread_content_matched": content_matched,
        "render_related_sample_fraction_by_thread": render_related_fraction,
        "execution_samples_on_render_threads": render_samples,
        "average_cpu_fraction_on_render_threads": render_cpu_fraction,
        "limitations": (
            "Render-thread identification combines name-pattern matching (misses generically-named threads, e.g. "
            "Starsector's own unnamed core render loop) with sampled-stack content matching against a small list "
            "of known rendering packages (misses engine-internal render code outside that list, and could "
            "over-attribute a thread that merely calls into a rendering library occasionally -- the >=50% "
            "sampled-stack threshold is a heuristic, not a verified boundary). Neither is engine-verified; there "
            "is no standard JFR event for GL stalls or swap-buffer waits. RenderDoc/engine-level integration is "
            "not implemented."
        ),
    }
