from __future__ import annotations

import re
from typing import Any

_RENDER_THREAD_PATTERNS = (
    re.compile(r"lwjgl", re.I),
    re.compile(r"render", re.I),
    re.compile(r"^main$", re.I),
    re.compile(r"opengl", re.I),
)


def _looks_like_render_thread(name: str) -> bool:
    return any(pattern.search(name) for pattern in _RENDER_THREAD_PATTERNS)


def analyze_rendering(cpu_thread_analysis: dict[str, Any]) -> dict[str, Any]:
    """Best-effort rendering-thread diagnostics from generic CPU/thread analysis.

    There is no standard JFR event for GL stalls or render-thread state;
    this only re-slices the CPU/thread analysis to threads whose name
    matches common LWJGL/render-thread conventions (Starsector uses
    LWJGL). It is a real, if limited, signal -- not a reconstruction of
    frame timing or GL call stalls. RenderDoc or engine-level integration
    remains out of scope and is not attempted here.
    """

    thread_names = set(cpu_thread_analysis.get("samples_by_thread", {}).keys()) | set(cpu_thread_analysis.get("average_thread_cpu_fraction", {}).keys())
    render_thread_names = sorted(name for name in thread_names if _looks_like_render_thread(name))

    render_samples = {name: cpu_thread_analysis.get("samples_by_thread", {}).get(name, 0) for name in render_thread_names}
    render_cpu_fraction = {name: cpu_thread_analysis.get("average_thread_cpu_fraction", {}).get(name) for name in render_thread_names}

    return {
        "render_thread_names_matched": render_thread_names,
        "execution_samples_on_render_threads": render_samples,
        "average_cpu_fraction_on_render_threads": render_cpu_fraction,
        "limitations": (
            "Render-thread identification is name-pattern-based, not engine-verified; there is no standard JFR "
            "event for GL stalls or swap-buffer waits. RenderDoc/engine-level integration is not implemented."
        ),
    }
