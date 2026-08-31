from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .analysis import run_analysis, write_analysis_artifacts
from .benchmark import load_scenario_manifest, run_benchmark
from .capture import DEFAULT_LEVEL, run_attach_capture, run_launch_capture
from .capture_levels import LEVELS
from .comparability import MATERIAL_VARIABLES, compute_comparability
from .core_integrity import classify_core_integrity, load_baseline_catalog
from .crash_log import find_crash_logs, parse_crash_log
from .gpu_detector import detect_gpus
from .java_detector import detect_java
from .jar_ownership import build_jar_ownership
from .jfr_events import jfr_tool_path
from .mod_inventory import build_inventory
from .models import FingerprintResult
from .overhead import estimate_attach_overhead
from .report import render_markdown, write_artifacts
from .report_viewer import render_html_report
from .runtime_capability import detect_runtime_capabilities, register_detector
from .save_fingerprint import find_saves, parse_save_descriptor
from .scenario_recorder import record_scenario_from_capture_descriptor
from .vmparams import parse_configured_jvm_arguments


def _load_extra_detectors(directory: Path) -> int:
    """Import every `.py` file in `directory` and register its runtime-capability detector(s).

    A file registers by exposing a module-level `DETECTORS` list of
    callables, or a single module-level `detect` function -- each taking
    one `runtime_capability.DetectorContext` and returning a list of
    capability-record dicts (see `register_detector`'s docstring). This is
    the only place SPW imports code it did not ship: it only runs against
    a directory the user explicitly names, exactly like them running a
    script they wrote themselves. Returns the number of detectors
    registered; a file that fails to import or has neither convention is
    skipped with a printed warning, not a hard failure.
    """

    registered = 0
    for path in sorted(directory.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"spw_extra_detector_{path.stem}", path)
            if spec is None or spec.loader is None:
                raise ImportError("could not build a module spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 -- a third-party file's failure mode is unknown by design
            print(f"spw: could not load extra detector '{path}': {exc}", file=sys.stderr)
            continue

        detectors = getattr(module, "DETECTORS", None)
        if detectors is None and hasattr(module, "detect"):
            detectors = [module.detect]
        if not detectors:
            print(f"spw: '{path}' defines neither DETECTORS nor detect(); skipped", file=sys.stderr)
            continue
        for detector in detectors:
            register_detector(detector)
            registered += 1
    return registered


def _check_crash_logs(installation_path: Path, result: FingerprintResult) -> None:
    """Proactively surface native/JVM crash logs sitting at the installation root, without requiring a deliberate capture."""

    crash_logs = find_crash_logs(installation_path)
    if crash_logs:
        result.add(
            id="crash-logs-present",
            category="crash-log",
            severity="medium",
            confidence="DETERMINISTIC",
            explanation=f"Found {len(crash_logs)} JVM crash log(s) (hs_err_pid*.log) in the installation directory; run `spw crash-logs` for details.",
            evidence=[log.name for log in crash_logs],
        )


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_snapshot(artifacts_dir: Path) -> dict:
    """Assemble a comparability-gate snapshot from a prior `spw inventory`/`spw capture` output directory."""

    runtime_capabilities_doc = _load_json(artifacts_dir / "runtime-capabilities.json")
    capture_descriptor = _load_json(artifacts_dir / "capture-descriptor.json")
    return {
        "environment": _load_json(artifacts_dir / "environment.json"),
        "core_integrity": _load_json(artifacts_dir / "core-integrity.json"),
        "runtime_capabilities": runtime_capabilities_doc.get("capabilities", []),
        "capture_level": capture_descriptor.get("level"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spw", description="Independent, offline runtime-profiling workbench for Starsector installations.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    inventory = subcommands.add_parser("inventory", help="fingerprint an installation and its mods without modifying it")
    inventory.add_argument("installation_directory", type=Path)
    inventory.add_argument("--output", type=Path, default=Path("spw-artifacts"))
    inventory.add_argument("--java", type=Path, default=None, help="explicit Java executable used by this installation")
    inventory.add_argument("--no-execute-java", action="store_true", help="never invoke external commands (the Java executable, GPU/driver inventory queries); rely on release-file/static evidence only")
    inventory.add_argument("--baseline-catalog", type=Path, default=None, help="versioned baseline catalog for core-integrity comparison")
    inventory.add_argument("--starsector-build", default=None, help="detected Starsector build, used to select a baseline-catalog entry")
    inventory.add_argument("--extra-detectors-dir", type=Path, default=None, help="directory of .py files, each exposing DETECTORS or detect(), to run alongside the built-in runtime-capability detectors")

    capture = subcommands.add_parser("capture", help="start/stop a Java Flight Recorder capture (explicit and opt-in)")
    capture.add_argument("--output", type=Path, default=Path("spw-artifacts"))
    capture.add_argument("--duration", type=int, default=30, help="capture duration in seconds")
    capture.add_argument("--level", choices=LEVELS, default=DEFAULT_LEVEL, help="capture level (default: %(default)s)")
    capture.add_argument("--attach-pid", type=int, default=None, help="attach to an already-running Starsector JVM by process id")
    capture.add_argument("--jcmd", type=Path, default=None, help="explicit jcmd executable (attach mode)")
    capture.add_argument("--java", type=Path, default=None, help="explicit java executable (launch mode, or to locate jcmd alongside it)")
    capture.add_argument("--estimate-overhead", action="store_true", help="attach mode only: run short paired PASSIVE/level windows first to estimate collector overhead")
    capture.add_argument("launch_command", nargs="*", help="explicit launch arguments to spawn with --java when --attach-pid is not given; put -- before flag-like arguments, e.g. `spw capture --java java.exe -- -jar starfarer.jar`")

    analyze = subcommands.add_parser("analyze", help="normalize, attribute, and analyze an already-captured recording (external-process work only)")
    analyze.add_argument("recording", type=Path, help="path to a .jfr recording produced by `spw capture`")
    analyze.add_argument("--output", type=Path, default=Path("spw-artifacts"))
    analyze.add_argument("--jfr", type=Path, default=None, help="explicit jfr executable")
    analyze.add_argument("--java", type=Path, default=None, help="explicit java executable, used to locate jfr alongside it")
    analyze.add_argument("--mod-ownership", type=Path, default=None, help="mod-ownership.json from a prior `spw inventory` run, used for mod attribution")

    compare = subcommands.add_parser("compare", help="apply the comparability gate to two prior artifact directories")
    compare.add_argument("snapshot_a", type=Path, help="artifacts directory from a prior `spw inventory`/`spw capture` run")
    compare.add_argument("snapshot_b", type=Path, help="artifacts directory from a second prior run")
    compare.add_argument("--output", type=Path, default=Path("spw-artifacts"))
    compare.add_argument("--experiment-variable", default=None, choices=list(MATERIAL_VARIABLES), help="a single material variable the user deliberately changed as a declared A/B experiment")

    benchmark = subcommands.add_parser("benchmark", help="run an explicit, user-authored benchmark scenario manifest (optional mode)")
    benchmark.add_argument("manifest", type=Path, help="path to a scenario manifest JSON file")
    benchmark.add_argument("--output", type=Path, default=Path("spw-artifacts"))
    benchmark.add_argument("--frame-time-samples", type=Path, default=None, help="optional file with one per-frame duration in seconds per line")

    crash_logs = subcommands.add_parser("crash-logs", help="parse JVM/native crash logs (hs_err_pid*.log) found in a directory, without requiring a deliberate capture")
    crash_logs.add_argument("directory", type=Path, help="directory to search for hs_err_pid*.log files (non-recursive; typically the installation root)")
    crash_logs.add_argument("--output", type=Path, default=Path("spw-artifacts"))

    view = subcommands.add_parser("view", help="render a local, self-contained HTML summary of a prior inventory/capture/diagnose output directory")
    view.add_argument("artifacts_directory", type=Path, help="an `spw inventory`/`spw capture`/`spw diagnose` output directory")
    view.add_argument("--output", type=Path, default=None, help="path to write the HTML file (default: report.html inside artifacts_directory)")

    record_scenario = subcommands.add_parser("record-scenario", help="turn an observed launch-mode capture's command line into a reusable `spw benchmark` scenario manifest")
    record_scenario.add_argument("capture_descriptor", type=Path, help="path to a capture-descriptor.json from a launch-mode `spw capture`/`spw benchmark` run")
    record_scenario.add_argument("--name", required=True, help="a name for the recorded scenario")
    record_scenario.add_argument("--description", default=None)
    record_scenario.add_argument("--output", type=Path, default=Path("scenario.json"))

    diagnose = subcommands.add_parser("diagnose", help="V1.0 pipeline: inventory -> capture -> analyze -> (optional compare) -> one report")
    diagnose.add_argument("installation_directory", type=Path)
    diagnose.add_argument("--output", type=Path, default=Path("spw-artifacts"))
    diagnose.add_argument("--java", type=Path, default=None, help="explicit Java executable used by this installation")
    diagnose.add_argument("--no-execute-java", action="store_true")
    diagnose.add_argument("--baseline-catalog", type=Path, default=None)
    diagnose.add_argument("--starsector-build", default=None)
    diagnose.add_argument("--attach-pid", type=int, default=None, help="attach to an already-running Starsector JVM to capture")
    diagnose.add_argument("--jcmd", type=Path, default=None)
    diagnose.add_argument("--level", choices=LEVELS, default=DEFAULT_LEVEL)
    diagnose.add_argument("--duration", type=int, default=30)
    diagnose.add_argument("--estimate-overhead", action="store_true")
    diagnose.add_argument("--compare-with", type=Path, default=None, help="a prior `spw diagnose`/`spw inventory` artifacts directory to compare this run against")
    diagnose.add_argument("--experiment-variable", default=None, choices=list(MATERIAL_VARIABLES), help="a single material variable the user deliberately changed as a declared A/B experiment")
    diagnose.add_argument("--extra-detectors-dir", type=Path, default=None, help="directory of .py files, each exposing DETECTORS or detect(), to run alongside the built-in runtime-capability detectors")

    return parser


def _run_inventory(args: argparse.Namespace) -> int:
    installation_path = args.installation_directory.expanduser().resolve()
    if not installation_path.is_dir():
        print(f"spw: installation directory does not exist: {installation_path}", file=sys.stderr)
        return 2

    result = FingerprintResult(installation_path=installation_path, java_executable=args.java)
    if args.java is not None:
        result.java = detect_java(args.java, allow_execute=not args.no_execute_java)
    result.gpus = detect_gpus(allow_execute=not args.no_execute_java)
    for vmparams_name in ("vmparams", "vmparams.txt"):
        vmparams_path = installation_path / vmparams_name
        if vmparams_path.is_file():
            result.configured_jvm_arguments = parse_configured_jvm_arguments(vmparams_path)
            break

    build_inventory(installation_path, result)
    _check_crash_logs(installation_path, result)
    build_jar_ownership(installation_path, result)

    baseline_catalog = None
    if args.baseline_catalog is not None:
        try:
            baseline_catalog = load_baseline_catalog(args.baseline_catalog)
        except (OSError, ValueError) as exc:
            print(f"spw: could not read baseline catalog: {exc}", file=sys.stderr)
            return 2
    classify_core_integrity(installation_path, result, baseline_catalog=baseline_catalog, starsector_build=args.starsector_build)
    if args.extra_detectors_dir is not None:
        if not args.extra_detectors_dir.is_dir():
            print(f"spw: --extra-detectors-dir does not exist: {args.extra_detectors_dir}", file=sys.stderr)
            return 2
        _load_extra_detectors(args.extra_detectors_dir)
    result.runtime_capabilities = detect_runtime_capabilities(installation_path, result.java, result.core_integrity)

    try:
        paths = write_artifacts(result, args.output)
    except ValueError as exc:
        print(f"spw: {exc}", file=sys.stderr)
        return 2

    print(f"Inventoried {len(result.mods)} mods; {len(result.findings)} findings.")
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


def _run_capture(args: argparse.Namespace) -> int:
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    output_file = output / "profile.jfr"
    result = FingerprintResult(installation_path=output)

    overhead_estimate = None
    if args.attach_pid is not None:
        if args.launch_command:
            print("spw: --attach-pid was given; ignoring the supplied launch command", file=sys.stderr)
        if args.estimate_overhead:
            overhead_estimate = estimate_attach_overhead(
                result,
                pid=args.attach_pid,
                level=args.level,
                output_dir=output,
                jcmd_path=args.jcmd,
                java_executable=args.java,
            )
        descriptor = run_attach_capture(
            result,
            pid=args.attach_pid,
            output_file=output_file,
            duration_seconds=args.duration,
            level=args.level,
            jcmd_path=args.jcmd,
            java_executable=args.java,
        )
    elif args.launch_command and args.java is not None:
        descriptor = run_launch_capture(
            result,
            java_executable=args.java,
            launch_args=args.launch_command,
            output_file=output_file,
            duration_seconds=args.duration,
            level=args.level,
        )
    else:
        print("spw: capture requires either --attach-pid, or --java plus an explicit launch command", file=sys.stderr)
        return 2

    (output / "capture-descriptor.json").write_text(json.dumps(descriptor, indent=2, sort_keys=True), encoding="utf-8")
    if overhead_estimate is not None:
        (output / "collector-overhead.json").write_text(json.dumps(overhead_estimate, indent=2, sort_keys=True), encoding="utf-8")
    # Findings raised during capture (e.g. jcmd-unavailable, jfr-start-failed,
    # tooling-jdk-target-jvm-version-mismatch) have nowhere else to land in
    # the standalone `capture` subcommand -- there is no inventory stage
    # here to fold them into environment.json, unlike `diagnose`. Without
    # this they are silently discarded.
    (output / "findings.json").write_text(json.dumps(result.findings_dict(), indent=2, sort_keys=True), encoding="utf-8")

    report_path = output / "CAPTURE_REPORT.md"
    lines = [
        "# SPW capture report",
        "",
        f"- Type: {descriptor['capture_type']}",
        f"- Level: {descriptor.get('level')}",
        f"- Target: {descriptor['target']}",
    ]
    tooling_jdk = descriptor.get("tooling_jdk")
    target_jvm = descriptor.get("target_jvm")
    if tooling_jdk is not None:
        lines.append(f"- Profiler tooling JDK: {tooling_jdk.get('implementor', 'UNKNOWN')} {tooling_jdk.get('java_version', 'UNKNOWN')}")
    if target_jvm is not None:
        if target_jvm.get("jdk_version"):
            lines.append(f"- Target JVM (the process actually being profiled): {target_jvm.get('vm_name', 'UNKNOWN')} {target_jvm.get('jdk_version', 'UNKNOWN')}")
        else:
            lines.append(f"- Target JVM: could not be determined ({target_jvm.get('limitation', 'unknown reason')})")
    if descriptor.get("output_path"):
        lines.append(f"- Recording: `{descriptor['output_path']}`")
    if overhead_estimate is not None:
        lines.append(f"- Estimated collector overhead: {overhead_estimate.get('estimated_overhead_fraction')} (confidence: {overhead_estimate.get('confidence')})")
    if descriptor.get("incomplete_reason"):
        lines.append(f"- Limitation: {descriptor['incomplete_reason']}")
    lines.extend(["", "## Findings", ""])
    if not result.findings:
        lines.append("No findings.")
    for finding in result.findings:
        lines.append(f"### [{finding.confidence}] {finding.id}")
        lines.append("")
        lines.append(f"- Category: {finding.category}")
        lines.append(f"- Severity: {finding.severity}")
        lines.append(f"- {finding.explanation}")
        if finding.evidence:
            lines.append(f"- Evidence: {', '.join(finding.evidence)}")
        lines.append("")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Capture type: {descriptor['capture_type']} (level: {descriptor.get('level')})")
    if tooling_jdk is not None:
        print(f"Profiler tooling JDK: {tooling_jdk.get('implementor', 'UNKNOWN')} {tooling_jdk.get('java_version', 'UNKNOWN')}")
    if target_jvm is not None and target_jvm.get("jdk_version"):
        print(f"Target JVM: {target_jvm.get('vm_name', 'UNKNOWN')} {target_jvm.get('jdk_version', 'UNKNOWN')}")
    if result.findings:
        print(f"Findings: {len(result.findings)} (see findings.json / CAPTURE_REPORT.md)")
    if descriptor.get("output_path"):
        print(f"Recording: {descriptor['output_path']}")
    if descriptor.get("incomplete_reason"):
        print(f"Limitation: {descriptor['incomplete_reason']}")
    print(f"Report: {report_path}")
    return 0 if not descriptor.get("incomplete_reason") else 1


def _run_analyze(args: argparse.Namespace) -> int:
    recording_path = args.recording.expanduser().resolve()
    if not recording_path.is_file():
        print(f"spw: recording does not exist: {recording_path}", file=sys.stderr)
        return 2

    resolved_jfr_tool = jfr_tool_path(args.java, args.jfr)
    if resolved_jfr_tool is None:
        print("spw: no jfr executable found; pass --jfr or --java explicitly", file=sys.stderr)
        return 2

    mod_ownership = None
    if args.mod_ownership is not None:
        try:
            mod_ownership = json.loads(args.mod_ownership.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"spw: could not read mod-ownership file: {exc}", file=sys.stderr)
            return 2

    analysis = run_analysis(resolved_jfr_tool, recording_path, mod_ownership=mod_ownership)
    paths = write_analysis_artifacts(analysis, args.output)

    print(f"Execution samples analyzed: {analysis['cpu_thread']['total_execution_samples']}")
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


def _run_compare(args: argparse.Namespace) -> int:
    for path in (args.snapshot_a, args.snapshot_b):
        if not path.is_dir():
            print(f"spw: snapshot directory does not exist: {path}", file=sys.stderr)
            return 2

    snapshot_a = _load_snapshot(args.snapshot_a)
    snapshot_b = _load_snapshot(args.snapshot_b)
    comparison = compute_comparability(snapshot_a, snapshot_b, declared_experiment_variable=args.experiment_variable)

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    comparison_path = output / "comparison.json"
    comparison_path.write_text(json.dumps(comparison, indent=2, sort_keys=True), encoding="utf-8")

    report_path = output / "COMPARISON_REPORT.md"
    lines = [
        "# SPW comparison report",
        "",
        f"- Snapshot A: `{args.snapshot_a}`",
        f"- Snapshot B: `{args.snapshot_b}`",
        f"- Result: {comparison['result']}",
        f"- Changed material variables: {', '.join(comparison['changed_variables']) or 'none'}",
    ]
    if comparison.get("is_declared_experiment"):
        lines.append("- This is a declared A/B experiment, not an uncontrolled change.")
    if comparison.get("proposed_controlled_run_matrix"):
        lines.append("- Proposed controlled-run matrix:")
        for combination in comparison["proposed_controlled_run_matrix"]:
            lines.append(f"  - {combination}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Comparability result: {comparison['result']}")
    print(f"Comparison: {comparison_path}")
    print(f"Report: {report_path}")
    return 0


def _run_benchmark(args: argparse.Namespace) -> int:
    if not args.manifest.is_file():
        print(f"spw: scenario manifest does not exist: {args.manifest}", file=sys.stderr)
        return 2
    try:
        manifest = load_scenario_manifest(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"spw: {exc}", file=sys.stderr)
        return 2

    frame_time_samples = None
    if args.frame_time_samples is not None:
        try:
            frame_time_samples = [float(line) for line in args.frame_time_samples.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError) as exc:
            print(f"spw: could not read frame-time samples: {exc}", file=sys.stderr)
            return 2

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = FingerprintResult(installation_path=output)
    benchmark_run = run_benchmark(result, manifest, output, frame_time_samples_seconds=frame_time_samples)

    run_path = output / "benchmark-run.json"
    run_path.write_text(json.dumps(benchmark_run, indent=2, sort_keys=True), encoding="utf-8")

    report_path = output / "BENCHMARK_REPORT.md"
    lines = [
        "# SPW benchmark report (optional mode)",
        "",
        f"- Scenario: {benchmark_run['scenario_name']}",
        f"- Manifest hash: {benchmark_run['scenario_manifest_hash']}",
        f"- Capture level: {benchmark_run['run_settings']['level']}",
    ]
    if benchmark_run["capture"].get("incomplete_reason"):
        lines.append(f"- Capture limitation: {benchmark_run['capture']['incomplete_reason']}")
    percentiles = benchmark_run["frame_time_percentiles"]
    if percentiles["samples_available"]:
        lines.append(f"- Frame time: mean {percentiles['mean_seconds']:.4f}s, p95 {percentiles['p95_seconds']:.4f}s, p99 {percentiles['p99_seconds']:.4f}s, spikes {percentiles['spike_count']}")
    else:
        lines.append("- Frame-time percentiles unavailable: no frame-time samples were supplied.")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Benchmark scenario: {benchmark_run['scenario_name']}")
    print(f"Run: {run_path}")
    print(f"Report: {report_path}")
    return 0


def _run_record_scenario(args: argparse.Namespace) -> int:
    if not args.capture_descriptor.is_file():
        print(f"spw: capture descriptor does not exist: {args.capture_descriptor}", file=sys.stderr)
        return 2
    descriptor = _load_json(args.capture_descriptor)
    if not descriptor:
        print(f"spw: could not read capture descriptor: {args.capture_descriptor}", file=sys.stderr)
        return 2

    try:
        manifest = record_scenario_from_capture_descriptor(descriptor, name=args.name, description=args.description)
    except ValueError as exc:
        print(f"spw: {exc}", file=sys.stderr)
        return 2

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print(f"Scenario: {output}")
    print(f"Limitation: {manifest['limitations']}")
    return 0


def _run_view(args: argparse.Namespace) -> int:
    artifacts_directory = args.artifacts_directory.expanduser().resolve()
    if not artifacts_directory.is_dir():
        print(f"spw: artifacts directory does not exist: {artifacts_directory}", file=sys.stderr)
        return 2

    output_path = args.output.expanduser().resolve() if args.output is not None else artifacts_directory / "report.html"
    html_text = render_html_report(artifacts_directory)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")

    print(f"Report: {output_path}")
    return 0


def _run_crash_logs(args: argparse.Namespace) -> int:
    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        print(f"spw: directory does not exist: {directory}", file=sys.stderr)
        return 2

    logs = find_crash_logs(directory)
    parsed = [parse_crash_log(path) for path in logs]

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    crash_logs_path = output / "crash-logs.json"
    crash_logs_path.write_text(json.dumps(parsed, indent=2, sort_keys=True), encoding="utf-8")

    report_lines = [
        "# SPW crash log report",
        "",
        f"- Directory searched (non-recursive): `{directory}`",
        f"- Crash logs found: {len(parsed)}",
        "",
    ]
    for entry in parsed:
        report_lines.append(f"## `{Path(entry['path']).name}`")
        report_lines.append("")
        if entry["parse_status"] == "UNREADABLE":
            report_lines.append("- Could not be read.")
            report_lines.append("")
            continue
        report_lines.append(f"- PID: {entry['pid']}, TID: {entry['tid']}")
        report_lines.append(f"- JRE: {entry['jre_version']}")
        report_lines.append(f"- Command line: `{entry['command_line']}`")
        report_lines.append(f"- Crash time: {entry['crash_time_raw']}")
        if entry["problem_summary"]:
            report_lines.append("- Problem summary:")
            for line in entry["problem_summary"]:
                report_lines.append(f"  - {line}")
        if entry["java_frames"]:
            report_lines.append("- Java frames at the point of the crash:")
            for frame in entry["java_frames"][:20]:
                report_lines.append(f"  - {frame}")
        report_lines.append("")
    report_lines.extend(
        [
            "## Scope boundary",
            "",
            "This only extracts the small set of fields that stay stable across crash types (problem summary, "
            "version/command-line metadata, and the Java-frame stack); it is not a full hs_err_pid parse and does "
            "not claim to identify a root cause.",
            "",
        ]
    )
    report_path = output / "CRASH_LOG_REPORT.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    print(f"Crash logs found: {len(parsed)}")
    print(f"Parsed: {crash_logs_path}")
    print(f"Report: {report_path}")
    return 0


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        digest.update(path.read_bytes())
    except OSError:
        return None
    return digest.hexdigest()


def _run_diagnose(args: argparse.Namespace) -> int:
    """The V1.0 pipeline: inventory -> capture -> normalize/attribute/analyze -> (optional compare) -> report.

    Each stage's artifacts land in their own subdirectory of `--output` so
    every intermediate result stays inspectable, not just the final
    summary.
    """

    installation_path = args.installation_directory.expanduser().resolve()
    if not installation_path.is_dir():
        print(f"spw: installation directory does not exist: {installation_path}", file=sys.stderr)
        return 2

    output = args.output.expanduser().resolve()
    inventory_dir = output / "inventory"
    capture_dir = output / "capture"
    analysis_dir = output / "analysis"
    compare_dir = output / "compare"

    result = FingerprintResult(installation_path=installation_path, java_executable=args.java)
    if args.java is not None:
        result.java = detect_java(args.java, allow_execute=not args.no_execute_java)
    result.gpus = detect_gpus(allow_execute=not args.no_execute_java)
    for vmparams_name in ("vmparams", "vmparams.txt"):
        vmparams_path = installation_path / vmparams_name
        if vmparams_path.is_file():
            result.configured_jvm_arguments = parse_configured_jvm_arguments(vmparams_path)
            break
    build_inventory(installation_path, result)
    _check_crash_logs(installation_path, result)
    build_jar_ownership(installation_path, result)
    baseline_catalog = None
    if args.baseline_catalog is not None:
        try:
            baseline_catalog = load_baseline_catalog(args.baseline_catalog)
        except (OSError, ValueError) as exc:
            print(f"spw: could not read baseline catalog: {exc}", file=sys.stderr)
            return 2
    classify_core_integrity(installation_path, result, baseline_catalog=baseline_catalog, starsector_build=args.starsector_build)
    if args.extra_detectors_dir is not None:
        if not args.extra_detectors_dir.is_dir():
            print(f"spw: --extra-detectors-dir does not exist: {args.extra_detectors_dir}", file=sys.stderr)
            return 2
        _load_extra_detectors(args.extra_detectors_dir)
    result.runtime_capabilities = detect_runtime_capabilities(installation_path, result.java, result.core_integrity)
    try:
        inventory_paths = write_artifacts(result, inventory_dir)
    except ValueError as exc:
        print(f"spw: {exc}", file=sys.stderr)
        return 2

    capture_descriptor = None
    overhead_estimate = None
    analysis = None
    if args.attach_pid is not None:
        capture_dir.mkdir(parents=True, exist_ok=True)
        output_file = capture_dir / "profile.jfr"
        if args.estimate_overhead:
            overhead_estimate = estimate_attach_overhead(result, pid=args.attach_pid, level=args.level, output_dir=capture_dir, jcmd_path=args.jcmd, java_executable=args.java)
            (capture_dir / "collector-overhead.json").write_text(json.dumps(overhead_estimate, indent=2, sort_keys=True), encoding="utf-8")
        capture_descriptor = run_attach_capture(result, pid=args.attach_pid, output_file=output_file, duration_seconds=args.duration, level=args.level, jcmd_path=args.jcmd, java_executable=args.java)
        (capture_dir / "capture-descriptor.json").write_text(json.dumps(capture_descriptor, indent=2, sort_keys=True), encoding="utf-8")

        if capture_descriptor.get("target_jvm"):
            # Only known once the attach has actually happened, so the
            # inventory's environment.json (already written above) is
            # re-persisted here to include it -- this is what a later `spw
            # compare` run reads, and it must reflect the JVM that actually
            # ran the profiled process, not just the tooling JDK that was
            # known at inventory time.
            result.target_jvm = capture_descriptor["target_jvm"]
            inventory_paths["environment"].write_text(json.dumps(result.environment_dict(), indent=2, sort_keys=True), encoding="utf-8")
        # Re-render PERFORMANCE_REPORT.md now that the capture descriptor
        # (and, above, the target JVM) is known -- write_artifacts() was
        # called before capture, at a point when there was nothing to
        # report here yet.
        inventory_paths["report"].write_text(render_markdown(result, capture_descriptor), encoding="utf-8")

        # Best-effort: the most-recently-modified save is the one most
        # likely to be the actively-played campaign during this capture --
        # a reasonable proxy, not a certainty (nothing outside the game
        # can confirm which save is currently loaded). Only descriptor.xml
        # is read, never the much larger campaign.xml alongside it.
        saves = find_saves(installation_path)
        if saves:
            most_recent_save = max(saves, key=lambda path: (path / "descriptor.xml").stat().st_mtime)
            save_fingerprint = parse_save_descriptor(most_recent_save)
            save_fingerprint["selection_method"] = "most_recently_modified_descriptor_xml_best_effort"
            (capture_dir / "save-fingerprint.json").write_text(json.dumps(save_fingerprint, indent=2, sort_keys=True), encoding="utf-8")

        if capture_descriptor.get("output_path"):
            resolved_jfr_tool = jfr_tool_path(args.java, None)
            if resolved_jfr_tool is not None:
                mod_ownership = json.loads(inventory_paths["mod_ownership"].read_text(encoding="utf-8"))
                analysis = run_analysis(resolved_jfr_tool, Path(capture_descriptor["output_path"]), mod_ownership=mod_ownership)
                write_analysis_artifacts(analysis, analysis_dir)
            else:
                result.add(id="jfr-tool-unavailable-for-analysis", category="diagnose", severity="medium", confidence="DETERMINISTIC", explanation="Capture succeeded but no jfr executable was found, so the recording could not be analyzed in this run.")

    comparison = None
    if args.compare_with is not None:
        if not args.compare_with.is_dir():
            print(f"spw: --compare-with directory does not exist: {args.compare_with}", file=sys.stderr)
            return 2
        this_run_snapshot = {
            "environment": result.environment_dict(),
            "core_integrity": result.core_integrity_dict(),
            "runtime_capabilities": result.runtime_capabilities,
            "capture_level": capture_descriptor.get("level") if capture_descriptor else None,
        }
        prior_snapshot = _load_snapshot(args.compare_with)
        comparison = compute_comparability(prior_snapshot, this_run_snapshot, declared_experiment_variable=args.experiment_variable)
        compare_dir.mkdir(parents=True, exist_ok=True)
        (compare_dir / "comparison.json").write_text(json.dumps(comparison, indent=2, sort_keys=True), encoding="utf-8")

    reproducibility = {
        "schema_version": "0.1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "invocation_argv": sys.argv[1:],
        "artifact_hashes": {
            "environment.json": _hash_file(inventory_paths["environment"]),
            "mod-ownership.json": _hash_file(inventory_paths["mod_ownership"]),
            "core-integrity.json": _hash_file(inventory_paths["core_integrity"]),
            "profile.jfr": _hash_file(capture_dir / "profile.jfr") if capture_descriptor else None,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "reproducibility.json").write_text(json.dumps(reproducibility, indent=2, sort_keys=True), encoding="utf-8")

    report_lines = [
        "# SPW diagnosis report",
        "",
        f"- Installation: `{installation_path}`",
        f"- Mods discovered: {len(result.mods)}",
        f"- Findings: {len(result.findings)}",
    ]
    if capture_descriptor is not None:
        report_lines.append(f"- Capture: {capture_descriptor['capture_type']} at level {capture_descriptor.get('level')}")
        tooling_jdk = capture_descriptor.get("tooling_jdk")
        target_jvm = capture_descriptor.get("target_jvm")
        if tooling_jdk:
            report_lines.append(f"  - Profiler tooling JDK: {tooling_jdk.get('implementor', 'UNKNOWN')} {tooling_jdk.get('java_version', 'UNKNOWN')}")
        if target_jvm and target_jvm.get("jdk_version"):
            report_lines.append(f"  - Target JVM (the process actually being profiled): {target_jvm.get('vm_name', 'UNKNOWN')} {target_jvm.get('jdk_version', 'UNKNOWN')}")
        elif target_jvm:
            report_lines.append(f"  - Target JVM: could not be determined ({target_jvm.get('limitation', 'unknown reason')})")
        if capture_descriptor.get("incomplete_reason"):
            report_lines.append(f"  - Limitation: {capture_descriptor['incomplete_reason']}")
    else:
        report_lines.append("- Capture: not requested for this run (inventory-only diagnosis).")
    if analysis is not None:
        report_lines.append(f"- Analysis: {analysis['cpu_thread']['total_execution_samples']} execution samples; see `analysis/ANALYSIS_REPORT.md`.")
    if comparison is not None:
        report_lines.append(f"- Comparison against `{args.compare_with}`: {comparison['result']} (changed: {', '.join(comparison['changed_variables']) or 'none'})")
    report_lines.extend(["", f"See `inventory/PERFORMANCE_REPORT.md` for full environment detail, and `reproducibility.json` for this run's invocation and artifact hashes.", ""])
    (output / "DIAGNOSIS_REPORT.md").write_text("\n".join(report_lines), encoding="utf-8")

    print(f"Diagnosis complete: {len(result.mods)} mods, {len(result.findings)} findings.")
    print(f"Report: {output / 'DIAGNOSIS_REPORT.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inventory":
        return _run_inventory(args)
    if args.command == "capture":
        return _run_capture(args)
    if args.command == "analyze":
        return _run_analyze(args)
    if args.command == "compare":
        return _run_compare(args)
    if args.command == "benchmark":
        return _run_benchmark(args)
    if args.command == "crash-logs":
        return _run_crash_logs(args)
    if args.command == "view":
        return _run_view(args)
    if args.command == "record-scenario":
        return _run_record_scenario(args)
    if args.command == "diagnose":
        return _run_diagnose(args)
    return 2
