# Recommendations progress log

Tracks work against [PERFORMANCE_WORKBENCH_RECOMMENDATIONS.md](PERFORMANCE_WORKBENCH_RECOMMENDATIONS.md). Updated as each item is worked; a finished item's detail moves into the roadmap doc's audit trail and gets a one-line pointer here.

| # | Item | Status |
| --- | --- | --- |
| 1 | Driver/GPU version as a comparability-gate variable | done |
| 2 | Native crash log ingestion | done |
| 3 | Baseline-catalog freshness warning | done |
| 4 | Cached JAR class-index reuse | done |
| 5 | Save-complexity fingerprint | partial (scoped down) |
| 6 | Community runtime-capability detector plugin points | done |
| 7 | Local static HTML viewer | done |
| 8 | Scenario-manifest recorder | partial (scoped down honestly) |
| 9 | Tick-indexed bucketing of CPU/allocation samples | done |
| 10 | Thread identity by OS/Java thread id | done |
| 11 | Proactive Tick Marker mod detection during inventory | done |
| 12 | Findings split by the JSON file they describe | done |
| 13 | Surface why zero JFR events were read | done |

## Log

### 8. Scenario-manifest recorder — partial (scoped down honestly)

The original ask -- recording "an actual play session's reproducible inputs (seed, settings, scripted sequence)" -- would need a deeper in-game hook (something like the Tick Marker mod, but for input recording) that does not exist, and the exact tick-boundary/input-capture surface for that isn't something to guess at blindly. What's implemented instead, in `spw/scenario_recorder.py` and the `spw record-scenario` subcommand: turning an *observed launch-mode capture's own recorded command line* (already sitting in its `capture-descriptor.json`) into a valid, reusable `spw benchmark` manifest -- still removing the "hand-author a manifest from scratch" barrier the recommendation named, just for the command-line/settings axis rather than in-game inputs. The manifest is explicit about this limitation in its own `limitations` field, not silently overselling what it captured. Rejects attach-mode descriptors (nothing to recover a launch command from) and descriptors with empty `launch_args`.

Validated with a genuine, complete round trip, not just isolated unit tests: ran a real `spw capture` in launch mode, fed its real `capture-descriptor.json` into `spw record-scenario`, and successfully replayed the resulting `scenario.json` through `spw benchmark` end to end. Full suite green (118 tests).

### 6. Community runtime-capability detector plugin points — done

Refactored `runtime_capability.py`'s six detectors onto one uniform contract: a new frozen `DetectorContext` (installation_path/java_info/core_integrity) in, `list[dict]` capability records out (the five that used to return a single dict now wrap it in a list). `detect_runtime_capabilities` iterates a module-level registry (`register_detector`/`unregister_detector`/`reset_detector_registry`) seeded with the six built-ins, so `detect_runtime_capabilities`'s own public signature never changed — the existing 30+ tests exercising it needed no updates. A detector that raises is caught per-detector and turned into an `UNKNOWN`-state placeholder record instead of aborting the whole scan, since a third-party detector's failure mode is unknown by design.

CLI side: `spw inventory`/`spw diagnose --extra-detectors-dir <path>` imports every `.py` file in that directory (via `importlib.util`, the only place SPW imports code it did not ship) and registers whatever it exposes as `DETECTORS` (a list) or `detect` (a single function) — deliberately no other dynamic-loading machinery, and it only ever runs against a directory the user explicitly names, same trust boundary as them running a script they wrote themselves. Tests cover registration/unregistration, the raise-isolation behavior, and a full CLI round-trip that writes a real `.py` file to a temp directory and confirms its capability shows up in the real `runtime-capabilities.json` output. Full suite green (114 tests); real run against the actual installation confirms the refactor didn't change real-world detection output.

### 7. Local static HTML viewer — done

New `spw/report_viewer.py` (`render_html_report`) and `spw view <artifacts-directory>` subcommand: a single, self-contained HTML file (inline CSS only, no external requests, no JS dependency) summarizing environment, runtime capabilities, mod ownership, CPU/allocation/tick analysis, and comparison sections -- each independently optional, so it renders something useful whether given an inventory-only, capture+analyze-only, or full `spw diagnose` output. Resolves artifacts from either the flat `spw inventory` layout or the nested `inventory/`/`analysis/`/`compare/` `spw diagnose` layout automatically.

Caught two real bugs via testing, both the same underlying mistake: pre-3.12 Python forbids the f-string's own quote character from appearing anywhere inside its `{}` expression, even nested inside a different-quoted inner string. First attempt used escaped `\"` inside an f-string expression (a plain `SyntaxError`, caught immediately by `python3 -c "import spw.report_viewer"`); a hasty `sed` fix produced a second, subtler break (`'<li class='muted'>'` -- three adjacent tokens with no operator between them). Fixed properly by computing each fallback string as a plain variable before the f-string, avoiding the nested-quote pattern entirely rather than fighting with escaping. Added an HTML-escaping test using a `<script>` tag as a mod-supplied finding string, which would have caught an unescaped-output bug had the `html.escape` call been missing. Real end-to-end run against the actual 155-mod installation produces valid HTML (verified with Python's own `html.parser`, zero parse errors). Full suite green (109 tests).

### 5. Save-complexity fingerprint — partial (scoped down honestly)

Implemented what could actually be verified, not what was originally proposed. The recommendation named colony/fleet/market counts as the target signal; investigating a real save showed those aren't safely extractable: `<Market ` alone appears 1,081 times in one real `campaign.xml` (52 MB), mixing genuine markets with data references and spawn templates, and `isPlayerColony>true` appeared 0 times in a save that should have colonies, meaning a naive tag count would either be wrong or silently miss things. Rather than guess at campaign.xml's object model or skip the item outright, implemented `spw/save_fingerprint.py` against the much smaller, structurally simple `descriptor.xml` (tens of KB, never the 52 MB campaign.xml): character level, game version, difficulty, save date, and mod counts (a real complexity signal in its own right). Wired into `spw diagnose`: after a successful capture, the most-recently-modified save under `<installation>/saves/` is snapshotted to `save-fingerprint.json` in the capture directory, explicitly labeled as a best-effort proxy for "the save being actively played" (nothing outside the game can confirm which save is actually loaded).

Real save data was used throughout, not synthetic fixtures: parsed the actual installation's real save files directly (4 saves found, most recent correctly identified, 117 enabled mods, character level 4) before writing the test fixture, which is a trimmed excerpt of the genuine XML. Full suite green (104 tests). Colony/fleet/market counts remain a real gap versus the original recommendation — noted here rather than claimed as done.

### 2. Native crash log ingestion — done

New `spw/crash_log.py` and `spw crash-logs <directory>` subcommand, plus a proactive `crash-logs-present` finding wired into `spw inventory`/`spw diagnose` (non-recursive search of the installation root, where the JVM writes `hs_err_pid*.log` by default) so a user finds out even without running the dedicated command. Deliberately extracts only the fields that stay stable across crash types (native SIGSEGV vs. an internal VM assertion vs. an OOM-triggered crash all format their problem header differently) — raw `#`-prefixed problem-summary lines, pid/tid, the handful of stable `Key: value` lines, and the Java-frame stack — rather than attempting to fully model hs_err's large, sprawling format.

Grounded in a genuine crash log, not a guessed format: generated a real one on this machine (`-Xmx32m -XX:+CrashOnOutOfMemoryError`, a real, safe, sanctioned way to force a crash dump without corrupting anything) and built the parser directly against it. Caught a real bug this way: `JRE version:`/`Java VM:` lines are `#`-prefixed (inside the boxed header) while `Command Line:`/`Host:`/`Time:` (in the SUMMARY section) are not — the first version only handled the unprefixed case and silently left those two fields `null`. Fixed by stripping a leading `#` before matching (a no-op where there isn't one). End-to-end `spw crash-logs` run against the real log file confirms every field populated correctly. Full suite green (100 tests).

### 12. Findings split by the JSON file they describe — done

Additive, not a breaking schema change: `environment.json` keeps its full, unfiltered `findings` list (still what `PERFORMANCE_REPORT.md` renders from), and `core-integrity.json`/`mod-ownership.json` now each additionally carry their own category-filtered subset (`FingerprintResult._findings_dicts(categories)`, matching on `Finding.category`). A reader grepping `core-integrity.json` for what's wrong now finds `baseline-catalog-unavailable` etc. there directly. New unit test plus a real run against the actual installation confirming the split: core-integrity.json got exactly the 1 baseline finding, mod-ownership.json got all 13 mod/jar-related ones. Full suite green (96 tests).

### 9. Tick-indexed bucketing of CPU/allocation samples — done

Added `correlate_stalls_with_execution_samples` to `tick_analysis.py`: for each stall `analyze_ticks` already detects, it finds every execution sample whose timestamp falls in `[stall.start_time - elapsed_seconds, stall.start_time]` (the window immediately preceding the tick that measured the long delta-time) and reports the top sampled frames during it. Promoted the timestamp parser out of `startup_analysis.py` into `jfr_events.parse_jfr_timestamp` as a shared utility rather than duplicating it a third time. Wired into `analysis.py` (new `tick_stall_correlation` output key, `tick-stall-correlation.json` artifact, and an ANALYSIS_REPORT.md section nesting "what was sampled" under each reported stall).

Validated against genuine, purpose-built real data, not just unit tests: extended the standalone Java harness to run 30 ticks with a real ~500ms CPU-bound busy-loop injected at tick 15 (surrounded by normal ~16ms ticks), captured it under the actual `DEEP_DIAGNOSTIC.jfc` settings, and ran it through `spw analyze`. The real output: tick 15 correctly flagged as the sole stall (0.4998799s vs a 0.0328s average), and the correlation correctly attributed 47 of the recording's 49 total execution samples to it, all in `StallTest.main` — exactly the injected busy-loop. Full suite green (95 tests) plus new unit tests for the window-boundary logic (inside/outside/empty cases).

### 4. Cached JAR class-index reuse — done

New `spw/jar_index_cache.py`: a disposable, per-user JSON cache (`%LOCALAPPDATA%/spw/jar-index-cache.json` on Windows) keyed by jar content sha256, storing only `{prefixes, classes}` — never game/mod content. `jar_ownership.py`'s mod-jar and core-jar loops were also deduplicated into one shared `_index_and_accumulate` helper (they were near-identical copy-paste before) while wiring the cache in; only successful (`INDEXED`) results are cached, so an unreadable jar is always retried fresh. `build_jar_ownership` gained `use_cache`/`cache_path` parameters (existing tests pass `use_cache=False` to stay hermetic and not touch the real machine's cache directory).

Verified for real, not just plausible: a test mocks `zipfile.ZipFile` on a second `build_jar_ownership` call and asserts it is **never called** — proving jars are genuinely not reopened on a cache hit, not just "probably faster". A real cold/warm run against the actual 155-mod, 138-jar installation produced byte-identical `mod-ownership.json` output both times (correctness confirmed) with a real 1.9 MB cache file written; wall-clock difference was modest on this machine's SSD (2.3s → 2.0s), which is expected — the mechanism's win scales with jar count/size and slower storage, not disproven by a small delta here. Full suite green (92 tests).

### 11. Proactive Tick Marker mod detection during inventory — done

`build_inventory` now checks the discovered mods for `spw_tick_marker` (must match `spw/agent-mod/mod_info.json`'s declared id) and adds an informational finding: `tick-marker-mod-not-installed` if absent, `tick-marker-mod-not-enabled` if present but not enabled, nothing if it's installed and enabled. Turns a silent capability gap (previously only discoverable after capturing at `DEEP_DIAGNOSTIC` and finding an empty `tick-analysis.json`) into an actionable finding at inventory time. Three new tests cover all three states; real run against the actual installation confirms `tick-marker-mod-not-installed` appears (it isn't installed there). Full suite green (85 tests).

### 1. Driver/GPU version as a comparability-gate variable — done

Turned out the recommendation's premise didn't hold: the design doc lists GPU/driver among `EnvironmentFingerprint`'s fields, but V0.1 never actually implemented GPU detection at all. Added it first (`spw/gpu_detector.py`, Windows-only for now — queries `Win32_VideoController` via PowerShell/CIM; other platforms report `source: "UNAVAILABLE"` rather than guessing), wired into `environment.json` under `gpus`, then added `gpu_driver_state` to `comparability.MATERIAL_VARIABLES`. Reports every detected GPU, not "the" GPU — this dev machine has hybrid Intel/NVIDIA graphics, and there is no reliable way from here to tell which one an application actually renders on, so guessing one would violate the evidence-first discipline. List order doesn't cause a false "changed" (sorted before comparing). New tests for the detector (Windows/non-Windows, single/multi-GPU JSON shapes, subprocess/parse failures) and the comparability integration; real end-to-end run against the actual installation confirms both GPUs show up correctly in `PERFORMANCE_REPORT.md`. Full suite green (82 tests).

### 10. Thread identity by OS/Java thread id — done

Added `event_thread_identity` (returns `(name, javaThreadId)`) and `resolve_thread_labels` to `jfr_events.py`; the latter disambiguates a name into `name#id` only where it actually collides across distinct ids, so the common case (every name unique) is byte-for-byte unchanged. Wired into `cpu_thread_analysis.py`'s `samples_by_thread`, `average_thread_cpu_fraction`, and (the real bug) `still_running_at_capture_end` lifecycle tracking, plus `attribution.py`'s per-thread breakdown.

Caught a real bug while testing this: `resolve_thread_labels` iterated its `identities` argument twice, but every call site passed a single-use generator expression — the second pass silently saw nothing and the label map came back empty, raising `KeyError` on lookup. Fixed by materializing the iterable inside the function. A new regression test (`test_reused_thread_name_does_not_hide_a_still_running_thread`) reproduces the scenario the recommendation described directly: two distinct threads sharing a name, one ended and one not — name-only tracking computes `{"worker"} - {"worker"} = {}` and hides the still-running one; identity-based tracking correctly reports `["worker#2"]`. Full suite green (74 tests); real `test.jfr` run confirms the common case (uniquely-named `pool-1-thread-1`/`pool-1-thread-2`) is unaffected.

### 13. Surface why zero JFR events were read — done

Added `read_events_with_status` to `jfr_events.py` (`read_events` now a thin wrapper that discards the status, so existing callers are unaffected). Returns a stable, machine-readable reason on failure — `jfr_tool_not_found`, `jfr_print_timed_out`, `jfr_print_failed:<code>`, `jfr_print_os_error`, `jfr_print_output_unparseable` — instead of a silent `[]` indistinguishable from a genuine zero-event result. Wired into `cpu_thread_analysis.py`, `allocation_gc_analysis.py`, `startup_analysis.py`, and `tick_analysis.py`, each now returning a `read_limitations` dict (or `read_limitation` for tick analysis, which only reads one event type) alongside their counts. `analysis.py`'s `run_analysis` reads execution samples once with status and threads the limitation through to `cpu_thread_analysis` so it isn't silently dropped on the pre-fetched path. New tests cover all four failure-reason mappings plus the genuine-success case; full suite green (69 tests) and a real end-to-end `spw analyze` run against the earlier captured `test.jfr` confirms `read_limitations: {}` on a real successful read.

### 3. Baseline-catalog freshness warning — done

`classify_core_integrity` now distinguishes three previously-conflated cases instead of one generic `baseline-catalog-unavailable`: no catalog supplied at all (`baseline-catalog-unavailable`, low), a catalog supplied but no `--starsector-build` given to look an entry up (`baseline-catalog-build-not-specified`, low), and — the actual "staleness" case — a catalog supplied and a build named, but that build absent from the catalog (`baseline-catalog-stale`, medium, names the missing build in the message and evidence). Two new tests added; full suite still green (65 tests).
