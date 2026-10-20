# Starsector Performance Workbench roadmap

SPW follows the design in [PERFORMANCE_WORKBENCH_DESIGN.md](PERFORMANCE_WORKBENCH_DESIGN.md): understand the environment first, capture second, attribute with evidence, and never claim causation the data does not support. SPW never modifies Starsector, a mod, or a JVM/launch configuration.

## Product boundary

SPW profiles runtime behavior. It is an independent program from **Bridgeforge**, the separate mod-modernization scanner — the two do not share a codebase, a repository, or a runtime dependency. The only planned interchange between them is a small set of versioned JSON schemas (see "Bridgeforge interoperability" in the design doc). Either tool must remain useful when the other is absent.

## First implementation phases — V0.1 foundation

The design doc's V0.1 line item ("Environment fingerprint and JFR capture") decomposes into these implementation phases, in the order they were built:

1. **CLI and installation target** — accept an explicit, user-selected Starsector installation directory and an explicit Java executable; never guess or auto-discover paths silently.
2. **Java detection** — fingerprint the actual JVM from its `release` file (`IMPLEMENTOR`, `JAVA_VERSION`, `OS_ARCH`, etc.) rather than trusting a folder name or the system `PATH`; fall back to explicit `-version` output only when the release file is unavailable.
3. **Mod inventory** — parse `enabled_mods.json` and each enabled mod's `mod_info.json` read-only and tolerantly; preserve malformed/missing metadata as findings with raw evidence instead of discarding the mod.
4. **JAR ownership indexing** — build a class-to-JAR and package-prefix index per mod, classify ownership confidence (`EXACT` / `LIKELY` / `AMBIGUOUS` / `UNOWNED` / `UNKNOWN`), and flag shared package prefixes bundled by more than one mod without assigning a runtime frame to one of them.
5. **Core-integrity hashing** — hash an explicit, user-selected set of core/launcher/native files and compare against an explicitly supplied, version-matched baseline catalog; report `BASELINE_UNAVAILABLE` rather than guessing when no matching catalog is supplied.
6. **JFR capture orchestration** — start/stop a Java Flight Recorder capture against an explicit PID (attach) or an explicit launch command (spawn-with-flags); report the limitation and take no fake substitute when the target JVM does not support JFR, `jcmd`/`jfr` are unavailable, or attachment fails.
7. **Environment fingerprint assembly** — combine Java detection, configured JVM arguments, mod inventory, JAR ownership, and core integrity into one hash-stamped `environment.json`.
8. **Artifacts and reporting** — emit `environment.json`, `core-integrity.json`, `mod-ownership.json`, `profile.jfr` (when captured), and a basic `PERFORMANCE_REPORT.md`.

**Status: implemented locally.** The V0.1 foundation is read-only with respect to the installation and mod directories; capture is opt-in, explicit, and writes only to a selected output directory.

## Subsequent releases

All of V0.2 through V1.0 below have a first working implementation, initially validated against a real JDK 25 (Temurin) install and real `jcmd`/`jfr` tooling. Two live Starsector validation passes followed on 2026-09-01, as recorded below. Each entry distinguishes evidence-backed behavior from explicit limitations.

- **V0.2 — runtime capability detectors** (`spw/runtime_capability.py`). `VanillaJava17`/`GenericAlternateJDK` are evidence-based (parsed Java version). `MikohimeConfiguration`/`FastRendering` match documented installation files. `FastRenderingResourceCache`/`StarsectorPrepatcher` use confirmed installation and launch-configuration fingerprints, validated on 2026-09-01 (see the progress log). `UnknownModifiedRuntime` is derived from core-integrity's `UNKNOWN_DIFFERENCE` status. Wired into `spw inventory`, emitting `runtime-capabilities.json`.
- **V0.3 — startup profiling** (`spw/startup_analysis.py`). Reports a class-loading curve and a `time_to_first_execution_sample_seconds` proxy. Explicitly documented limitation: there is no engine-defined "reached main menu" marker available from stock JFR events, so this is a rough proxy, not a real startup-phase breakdown.
- **V0.4 — CPU and thread analysis** (`spw/cpu_thread_analysis.py`). Real aggregation of `jdk.ExecutionSample`, `jdk.ThreadCPULoad`, `jdk.ThreadStart`/`ThreadEnd`, and `jdk.CPULoad` via the actual `jfr print --json` output format.
- **V0.5 — mod attribution** (`spw/attribution.py`, plus a `class_index`/`package_prefix_index` added to `jar_ownership.py`). Implements the evidence-ranked order from the design doc (exact class-to-JAR index → package prefix → "the runtime itself" → unknown); source-layout and JAR-manifest evidence tiers are not yet implemented.
- **V0.6 — allocation, GC, and retention analysis** (`spw/allocation_gc_analysis.py`). Real aggregation of `jdk.ObjectAllocationSample`, `jdk.GCPhasePause*`, and `jdk.GCHeapSummary`.
- **V0.7 — combat benchmark mode, optional** (`spw/benchmark.py`). Scenario manifests are explicit and user-authored; a launch-mode capture is real. Frame-time percentiles require externally supplied per-frame samples — Starsector has no standard JFR frame-render event, so SPW does not fabricate them; this is reported as an explicit `samples_available: false` limitation, not silently skipped.
- **V0.8 — baseline comparison / comparability gate** (`spw/comparability.py`). Implements `COMPARABLE`/`PARTIALLY_CONTROLLED`/`NOT_COMPARABLE` over the design's material-variable list, including a declared-experiment label and a proposed controlled-run matrix when exactly two variables are confounded.
- **V0.9 — rendering diagnostics, best-effort** (`spw/rendering_diagnostics.py`). Combines two independent signals: threads matching common LWJGL/render-thread name conventions, plus (added after live validation exposed a generically-named dominant render thread, see below) threads whose sampled stacks are dominated by known rendering-related packages. There is no standard JFR event for GL stalls, and RenderDoc/engine-level integration is explicitly out of scope and unimplemented.
- **V1.0 — diagnosis pipeline** (`spw diagnose`). Chains inventory → capture → analyze → optional compare into one command with per-stage subdirectories, plus `reproducibility.json` (invocation argv, UTC timestamp, sha256 of each key artifact).

### Validated against a real installation

`spw inventory` has since been run read-only against a genuine, heavily modded Starsector installation (Mikohime fork, Fast Rendering, 155 mod directories, 138 JARs — the same scale the design doc's own installation stats describe) with a JDK 27 runtime. It completed without error and produced a materially better report after three real fixes this exposed:

- **Lenient `mod_info.json` parsing** (`spw/lenient_json.py`). Real mod authors' `mod_info.json` files routinely use `#`/`//` comments, trailing commas, and single-quoted scalar values — none of which strict `json.loads` accepts. On the test installation this meant **99 of 149** real `mod_info.json` files (66%) initially failed to parse and were misreported as broken metadata; a string-aware comment/trailing-comma/single-quote normalizer (falling back to strict parsing first, so the common well-formed case pays no cost) brought that to 0 failures. Order matters here: comments must be stripped *before* single-quote conversion, since an apostrophe in ordinary comment prose (`"Starsector's parser"`) would otherwise be misread as opening a quoted string.
- **`mod_info.json.disabled` recognized as a real, evidenced state** (`spw/mod_inventory.py`). Mod managers commonly disable a mod by renaming its `mod_info.json`, which makes Starsector itself stop recognizing the directory as a mod. This is now reported as `mod-disabled-via-rename` (informational) instead of the high-severity `missing-mod-info` finding it used to produce.
- **`FastRendering` detection broadened to where it's actually evidenced** (`spw/runtime_capability.py`). The original detector only checked the installation root for `fr.bat`/`fr.vmparams`. On the real installation, Fast Rendering ships in its own `fast-rendering-<version>/` folder and, once activated, copies those files directly into `starsector-core/` — neither of which is the installation root. The detector now checks all three locations and records which ones matched, so a reader can distinguish "the distribution is present" from "it's copied into the active core".

The same run also produced three genuinely correct findings that are not SPW bugs: three `enabledMods` entries (`THI`, `QualityCaptains`, `Red_stripe`) referencing mod IDs that don't exist anywhere in the installation — real drift from deleting a mod folder without disabling it first — and three non-mod directories (a `DDSCache` texture-cache folder, a `TriOS` mod-manager installation, and a stale old Fast Rendering folder) correctly flagged as "not a mod" rather than guessed at.

### Second-pass code audit: fixes that only real testing surfaced

A full re-read of every module (prompted by wanting to trust it before the "later" live-Starsector pass) found and fixed several real defects, none of which any of the earlier hermetic unit tests caught:

- **Launch-mode capture could hang indefinitely** (`spw/capture.py`). `run_launch_capture` used a blocking `subprocess.run`, which waits for the *launched process* to exit — but `-XX:StartFlightRecording=duration=Ns` only bounds the recording, not the game itself. Against a real, long-running game (or even a 30-second synthetic test process with a 3-second capture window) this would block far longer than intended, and would never return at all for an actual play session. Fixed to spawn non-blocking (`Popen`) and wait out only the capture duration, exactly like attach mode — confirmed with a real process: the call now returns in ~4s instead of blocking for the test process's full ~30s lifetime, and the game is correctly left running afterward.
- **A mod disabled via the `mod_info.json.disabled` rename could still be reported as enabled** (`spw/mod_inventory.py`) if its id happened to still appear in `enabled_mods.json` — a real scenario found on the test installation, where an old disabled copy and a newer enabled copy declared the same id. The rename is now treated as authoritative regardless of what the (possibly stale) enabled-mods list says.
- **Duplicate mod ids across directories were undetected.** The same real installation had three ids each claimed by two directories (an old disabled copy beside a newer one). `jar_ownership` keys by id, so this silently conflated two different codebases; a new `duplicate-mod-id` finding now surfaces it.
- **Base-game and Fast Rendering code had no attribution path at all** (`spw/jar_ownership.py`, `spw/attribution.py`). Only mod JARs were indexed, so any CPU/allocation sample landing in Starsector's own code (`starsector-core/*.jar`) — typically the majority of any real session — fell through to `UNKNOWN`, indistinguishable from a genuine gap. `starsector-core/*.jar` is now indexed into the same class/package index under reserved `__starsector_core__`/`__fast_rendering__` pseudo-owners, so it resolves through the same exact-match tier as mod attribution, and a collision between a mod's bundled library and a base-game dependency (e.g. both bundling `log4j`) correctly shows as ambiguous rather than picking one silently.
- Smaller fixes: `_load_json`/`_hash_file` in `spw/cli.py` didn't catch all the exceptions a corrupted or unreadable prior-run artifact could raise, which would have crashed `compare`/`diagnose` late in the pipeline after useful work was already done; the `--experiment-variable` choices list was duplicated in three places (now sourced from `comparability.MATERIAL_VARIABLES` once); passing a flag-like launch argument (`-jar ...`) to `spw capture`'s positional `launch_command` failed with a cryptic argparse error (now documented — use `--`); a startup-analysis reference-timestamp bug could report a negative "time to first sample"; an unevidenced, seemingly-erroneous thread-name-pattern regex was removed from `rendering_diagnostics.py`.

### `DEEP_DIAGNOSTIC` tick-boundary event: implemented

The custom event is real now, via `spw/agent-mod` — the **SPW Tick Marker mod**. It is an ordinary Starsector mod (a `mod_info.json` plus a compiled jar, installed into `mods/` like any other mod) built entirely on the public, documented modding API: it registers an `EveryFrameScript` (`Global.getSector().addTransientScript(...)`), and its `advance(float)` — called once per campaign tick by the engine itself — commits a `com.spw.TickBoundary` JFR event carrying a running tick index and the engine's own elapsed-time argument. It never touches Starsector's own (obfuscated) classes and does not read or write any game state.

This was deliberately built as a mod rather than a bytecode-instrumenting Java agent: a `-javaagent` that patches the game's own classes to find "the tick" would require reverse-engineering the obfuscated core, which the safety contract's "do not decompile" line rules out. Registering a script through the same public API every other mod uses sidesteps that entirely.

Validated so far: compiles cleanly against the real `starfarer.api.jar` (confirmed the exact `EveryFrameScript`/`ModPlugin`/`BaseModPlugin` method signatures via `javap`, and cross-checked the registration pattern against a real installed mod's source, `Aptly Simple Hullmods`'s `ModPlugin.java`). A standalone harness that commits the same event class under the real `DEEP_DIAGNOSTIC.jfc` settings produces exactly the expected `com.spw.TickBoundary` events, readable by `spw/tick_analysis.py` (tick count, average/max tick duration, stall detection for a tick many times longer than the running average). **Since validated**: actually installed and enabled inside a running Starsector campaign, during the live-Starsector validation passes — see "What was still unvalidated" below.

To use it: copy `releases/spw-tick-marker/` (or extract `releases/spw-tick-marker.zip`) into the target installation's `mods/` directory (so `mods/spw-tick-marker/mod_info.json` exists; this is the prebuilt, runtime-only copy — `spw/agent-mod/` remains the source of truth if you need to rebuild it), enable `spw_tick_marker` like any other mod, then run `spw capture --level DEEP_DIAGNOSTIC ...`. `spw analyze` will emit `tick-analysis.json` and an ANALYSIS_REPORT.md section reporting `ticks_available: false` (with an explanatory limitation, not an error) whenever the mod isn't installed or enabled.

### What was still unvalidated (now closed)

Everything above the "Validated against a real installation" line was, at the time it was written, only exercised against a synthetic Java test harness and real `jcmd`/`jfr` tooling, not yet against a live Starsector process. Both deliberately-deferred live-Starsector validation passes have since happened (2026-09-01) — see [PERFORMANCE_WORKBENCH_PROGRESS_LOG.md](PERFORMANCE_WORKBENCH_PROGRESS_LOG.md#2026-09-01--the-live-starsector-validation-pass-for-real): the Tick Marker mod fired 9,377 real `com.spw.TickBoundary` events correctly in pass 1 and 15,882 in pass 2, and one real gap surfaced and was fixed (rendering-thread identification by name alone missed the actual dominant render thread; see the V0.9 entry above).

See [PERFORMANCE_WORKBENCH_RECOMMENDATIONS.md](PERFORMANCE_WORKBENCH_RECOMMENDATIONS.md) for candidate additions to this roadmap that are not yet committed to it.

## Sister repository interchange (proposed 2026-09-27)

Design: [SISTER_REPO_INTERCHANGE_DESIGN.md](SISTER_REPO_INTERCHANGE_DESIGN.md).

1. [ ] Freeze and export a versioned performance report for BridgeForge, with artifact hashes, metric units, attribution confidence, unavailable reasons, and SPW's comparability result. Validate with neutral synthetic fixtures before consumer integration.
2. [ ] Export an optional versioned mod identity inventory for BridgeForge and VoidSmith, preserving duplicate IDs, disabled states, parse warnings, and install/hash provenance. Both consumers retain independent scans.

**Implementation checkpoint 2026-09-27:** `spw inventory`/`diagnose` now write `mod-identity.json`; `analyze`/`diagnose` write `performance-report.json` when analysis runs. The performance metric is attributed execution *samples*, with no inferred CPU percentage or startup milestone. These producer files are versioned and covered by synthetic tests. BridgeForge ingestion and a real cross-repository round trip remain open, so the two items stay unchecked.

**Real-install round trip 2026-10-20 (OBSERVED, inventory only):** `spw inventory` on the real install found 160 mods (9 findings) and wrote `mod-identity.json`; VoidSmith's reviewer matched 151, 9 UNKNOWN, 0 mismatches. BridgeForge ingestion and a real `performance-report.json` (needs a capture launch) remain open. Local evidence now goes to the ignored `local-evidence/`.

## Proposed ideas (2026-10-06, owner asked for per-project ideas; proposed, not approved)

1. [ ] **Accelerated-time profile.** Benchmark a campaign under a speed-up multiplier (for example the SpeedUp utility mod's key
   held), since BridgeForge's soak runs now use it. Benefit: shows whether per-tick cost of fleet managers, economy ticks and
   intel stays acceptable when many days pass per second, and which mods dominate.
2. [ ] **Logged-error cost.** Count log lines and stack traces per mod and estimate their cost. A caught exception logged every
   tick (Nexerelin's `StrategicAI` ERROR on the Avesta target) is invisible to a crash check but can slow a campaign.
   Benefit: attributes performance loss to a mod's error spam, with evidence.
3. [ ] **Save-growth against frame time.** Correlate BridgeForge's `save-growth` object counts per mod with SPW's captured
   performance across a save chain. Benefit: flags leaks (ever-growing lists, intel that never cleans up) by their cost, not
   only their size.
4. [ ] **Per-revival performance gate.** Consume SPW evidence as one gate in BridgeForge's `release` for a revived mod, using
   the existing versioned performance report export and its comparability result. Benefit: a revival cannot ship a large
   regression unnoticed. This is already sister-interchange item 1; this idea is the gate itself.
5. [ ] **Capture during a BridgeForge live run (2026-10-20, proposed).** A real `performance-report.json` still needs a
   capture launch. Let `spw capture` attach to a game BridgeForge's `probe-group live` already started (or let BridgeForge
   pass SPW's capture arguments through its launcher), so one rig launch yields the live run record and the performance
   report for the same enabled set. Benefit: closes the open real round trip without an extra launch, and every revival
   live run can carry a performance baseline for item 4.

## Lessons carried over from BridgeForge's hardening (item 70) and regression pass (2026-10-12; proposed, not approved)

Source: BridgeForge `ROADMAP.md` item 70, `docs/HARDENING_PLAN.md`, `docs/RC8_BEHAVIOUR.md` (RC8-30 to RC8-33). Each line says what it would change here and what it buys; none is started.

- **Atomic writes and guarded deletes.** Write through a temp file and `os.replace`; delete a tree only inside an allowed root and never through a junction (BridgeForge `safeio.py`, with a ratchet test whose baseline may only shrink). Benefit: an interrupted write or a wrong path cannot leave a half-written or vanished file.
- **Bounded reads of untrusted archives.** Cap a zip or jar entry (200 MB; class files 32 MB) before reading it (`zipsafe.read_limited`); a hostile or corrupt archive then fails that entry instead of exhausting memory.
- **Parse structured files with a real (lenient) parser, never a regex for the first field.** BridgeForge's master log took the first `"name"` and `"version"` in each `mod_info.json` and listed 17 mods as "LazyLib" with LazyLib's version, because those files list dependencies first. Benefit: wrong labels and versions stop appearing in reports.
- **Guard the contract, not just the code.** A hash of every command's `--help` (a golden test) let agents split a 4,100-line `cli.py` and an 8,800-line `scanner.py` with the interface unchanged; a known-failure file with an expiry keeps an environmental failure from hiding new ones; baselines of accepted findings expire when the mod version changes.
- **Check an agent's branch before merging it** (`agent-diff-check`: files deleted or cut by more than a fifth) and rerun the whole suite on the merged tree; do not edit tracked files while the suite runs (its hermeticity check reports the checkout as changed).
- **Line endings and backups.** LF everywhere (`.gitattributes`; batch files CRLF). An append-only snapshot of the irreplaceable state is taken after each major new revision, not on a weekly clock (owner rule 2026-10-12).
- **Say PREDICTED or OBSERVED.** BridgeForge keeps a prediction ledger: of two forecasts about a mod pair, one held live and one was refuted. Record the method, date and run id with each fact.
- **Environment comparability (BridgeForge items 61 and 62, first live run 2026-10-12).** 150 archived mods booted on Java 25 and 28, direct and with Fast Rendering: no mod differed between Java 25 and 28 or between direct and Fast Rendering; 13 mods failed only on the Fast Rendering variants without LunaLib enabled (GraphicsLib's `ShaderLib.init` reads a LunaLib class, RC8-33). Record the launcher variant and whether LunaLib is on with every performance capture, or comparisons mislead.
- **A runaway-frame scenario to reuse.** A hull mod that re-ran its death effect on its wreck every frame grew the field to 2,340 hulks and 7.7 s frames in one fight (RC8-27). BridgeForge's duel meter logs average frame time before and after the first destruction and peak hulks and ships, and the probe stops the combat at 400 hulks or a 2 s frame. Measured on one hull mod: 658 ms frames and 722 ships with the faulty jar, 18.9 ms and 8 ships fixed. This is a ready regression for the per-revival performance gate (idea 4).
- **Unattended runs need the launcher clicked** (it waits for a click since 2026-10-11) and the mission list located by unique mission id; otherwise a run silently plays the wrong mission.
- **Parse JSON leniently** (RC8's org.json accepts comments, bare words and tabs in strings); SPW's lenient-JSON fix reference applies.

## Hardening execution checkpoint — 2026-10-19

The roadmap review selected local cache resilience as the first implementation slice of the hardening proposals above. Other proposals remain open; this checkpoint does not claim cross-repository integration or new live-game validation.

- [x] **Atomic JAR-index cache writes.** Serialize to a unique sibling temporary file, close it, then replace the destination with `os.replace`. A failed replacement preserves the previous cache and attempts temporary-file cleanup. Cache I/O remains best-effort. This prevents partial-file visibility; it does not promise power-loss durability or merge simultaneous writers.
- [x] **Validate disposable cache entries.** Invalid UTF-8 and malformed JSON become misses. Entries must contain lists of strings for both class names and package prefixes; malformed entries are discarded individually so valid peers remain reusable.
- [ ] **Extend atomic output to report artifacts.** Centralize sibling-file replacement for CLI, inventory, analysis, and interchange outputs. Test failed writes preserve previous artifacts and propagate failures for required reports. Keep this separate from best-effort cache behavior.
- [ ] **Review archive bounds at actual content-read sites.** Current JAR ownership indexing reads entry names only, without decompressing class bodies. Add bounded reads wherever archive payload ingestion is introduced; do not claim payload limits protect central-directory parsing.

Validation: `py -3 -m unittest discover -s tests` passed all 148 tests, including four new cache regressions for invalid encoding, mixed valid/malformed entries, failed replacement, and complete-file replacement. One agent used; no installation, mod, or save changes.
