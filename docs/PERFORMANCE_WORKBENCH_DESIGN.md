# Starsector Performance Workbench

## Purpose and boundary

The Starsector Performance Workbench (SPW) is an independent, offline, runtime-profiling and benchmarking utility. It answers: **what is slow, when, and with what evidence?** Its pipeline is:

```text
inventory → capture → normalize → attribute → analyze → compare → report
```

SPW does not modernize, rewrite, patch, decompile, or otherwise modify mods. Those are Bridgeforge responsibilities. Bridgeforge does not profile performance. Either program must remain useful when the other is absent.

SPW's primary operating mode is a **low-overhead companion profiler**: a minimal collector runs inside the profiled Starsector process, and all heavy parsing, attribution, analysis, visualization, and reporting happen afterward in the external SPW process. A user should be able to attach SPW to an ordinary, unscripted play session — at a capture level they choose — and get useful evidence without first authoring a benchmark scenario. Controlled benchmark battles (a scripted, reproducible scenario run) are a later, optional mode layered on top of this architecture, not the primary design point; see "Capture levels" and the roadmap below.

## Design inputs from the local projects and installation

This design incorporates patterns observed in the two existing local projects:

- **Project Forge / VoidSmith:** source inputs are untrusted and read-only; parsing, normalized data, provenance, adapters, validation, analysis, and export stay separate; unknown data is explicit rather than guessed.
- **Project Go / SSMT:** use explicit user-selected paths, immutable source / generated-output boundaries, checkpointed workflows, Java/Gradle packaging, and clear diagnostics instead of silent repair.
- **Installed mod ecosystem:** the inspected installation contains 155 mod directories, 119 directories with JARs, 94 with Java source, and 138 JAR files. A large majority of parseable declarations target the 0.98 release-candidate corridor, while dependency declarations range from none to several per mod. Directory and archive coexistence, duplicate versions, nonstandard metadata, and bundled shared libraries must be normal inputs—not exceptional failures.

No game or mod data, names, extracted entity lists, captures, or benchmarks are part of this repository or the distributable application. SPW reads them only at runtime and writes results to an explicit local output directory.

## Safety and privacy contract

- Never change Starsector, a mod, its JARs, game settings, JVM launch configuration, or save files.
- Treat mod paths and metadata as untrusted input. Resolve and validate every path before reading it.
- Do not execute mod code to inventory it. Static ownership analysis must not class-load mod classes.
- A capture is opt-in and bounded by a selected launch profile, duration, output directory, and size policy.
- A report states evidence and confidence. It must never claim a mod caused a slowdown solely because its name appears in a stack.
- Default reports are local. Export/redaction is explicit because profiles can expose local paths and mod names.

## Core model

```text
Starsector installation
  ├─ environment fingerprint
  ├─ selected launch profile / runtime capability stack
  ├─ mod inventory + JAR ownership index
  └─ optional JFR capture
             ↓
event normalizer → stack-frame resolver → attribution engine
             ↓
CPU / blocking / allocation / GC / memory / startup analyzers
             ↓
baseline comparison + PERFORMANCE_REPORT.md / performance-report.json
```

### Key records

| Record | Required fields |
| --- | --- |
| `EnvironmentFingerprint` | schema version, Starsector build, executable/launcher, Java vendor/version/build/path, JVM arguments, heap/direct-memory limits, GC/JIT/module flags, agents, OS/GPU/driver, enabled-mod configuration hash, and timestamps |
| `CoreIntegrity` | selected core/launcher/native file hashes, baseline-catalog version, known modifications, unknown differences, and confidence |
| `RuntimeCapability` | capability ID/version/state, detector evidence, parsed configuration, thread/package classifiers enabled, and unsupported assumptions |
| `ModInventoryEntry` | opaque local ID, relative root, metadata parse status, content hash policy, JAR paths |
| `JarOwnershipEntry` | normalized JAR path, owner candidates, package prefixes, class index status, confidence |
| `CaptureDescriptor` | capture type, capture level (`PASSIVE` / `STANDARD` / `DEEP_DIAGNOSTIC`), JVM/JFR settings, start/stop, duration, size, incomplete reason |
| `CollectorOverheadEstimate` | capture level, collector CPU time, collector allocation (Deep Diagnostic only), measurement method, paired-baseline reference, confidence |
| `Attribution` | event/stack evidence, mod/JAR/package/class candidates, confidence, ambiguity reason |
| `BenchmarkRun` (optional mode) | scenario manifest hash, run settings, summary percentiles, environment snapshot hash |

An ownership result can be `EXACT`, `LIKELY`, `AMBIGUOUS`, `UNOWNED`, or `UNKNOWN`. Shared library frames must not be automatically attributed to the mod that happens to call them.

## Environment fingerprinting is mandatory

Every capture starts by creating an immutable `EnvironmentFingerprint`. It is embedded by hash into every JFR descriptor, analysis, and comparison; an incomplete fingerprint is a limitation, not a reason to silently omit context. Fingerprinting runs once per session, before any level-specific collection begins, and is unaffected by the selected capture level: even a `PASSIVE` capture gets a full fingerprint, since it is a one-time, negligible-cost step relative to sustained event collection.

The collector records only information needed to interpret measurements:

```text
Starsector version and selected executable/launcher
actual JVM path, vendor, version, build, and arguments
heap, direct-memory, GC, JIT/compiler, preview/module-access, and native JVM flags
Java and instrumentation agents, native libraries, class path/module path
operating system, GPU, and graphics-driver identifiers
enabled-mod order, metadata/JAR ownership status, and configurable local hashes
Fast Rendering installation/version/launch mode/vmparams when detected
selected core, launcher, vmparams, and native-library hashes
```

The fingerprint has redaction controls for export: local paths and mod names may be replaced with stable local aliases, while hashes and comparability fields remain useful.

### Core-integrity classification

Hashing alone does not establish that a file is modified. SPW compares selected files against a versioned, explicitly identified baseline catalog only when that catalog exactly matches the detected Starsector build. Results are `MATCHES_BASELINE`, `KNOWN_MODIFICATION`, `UNKNOWN_DIFFERENCE`, `BASELINE_UNAVAILABLE`, or `UNREADABLE`.

For example, a Fast Rendering installation can be classified as a known modification only when its adapter has direct fingerprint evidence. Otherwise it remains an unknown difference. This preserves the distinction between evidence and inference.

### Runtime capability stack

Runtime behavior is modeled as composable layers—not a mutually exclusive profile for every combination. An environment can contain a base game, a Java runtime, a launcher configuration, Fast Rendering, an FR resource cache, a prepatcher, JVM flags, and other instrumentation simultaneously.

Detectors centralize runtime-specific assumptions instead of scattering launcher, thread, or package special cases through analyzers:

```text
RuntimeAdapter
├─ VanillaJava17
├─ GenericAlternateJDK
├─ MikohimeConfiguration
├─ FastRendering
├─ FastRenderingResourceCache
├─ StarsectorPrepatcher
└─ UnknownModifiedRuntime
```

Each capability may identify launch files, parse approved configuration files, label known runtime threads/package prefixes, and issue compatibility warnings. It cannot change raw event data or suppress unrecognized threads. Capabilities are independently detected and retained with their evidence; `UnknownModifiedRuntime` is added whenever a safe assumption is unavailable.

The initial collector is deliberately explicit:

```text
EnvironmentCollector
├─ JavaDetector
├─ JVMArgumentParser
├─ MikohimeDetector
├─ FastRenderingDetector
├─ PrepatcherDetector
├─ GameCoreHasher
└─ ModInventory
```

`JavaDetector` fingerprints the actual JDK from its `release` file when available, rather than trusting a folder name. It records fields such as `IMPLEMENTOR`, `IMPLEMENTOR_VERSION`, `JAVA_RUNTIME_VERSION`, `JAVA_VERSION`, `JVM_VARIANT`, `OS_ARCH`, `OS_NAME`, and `IMAGE_TYPE`, alongside direct runtime output if an explicitly selected executable may be queried safely.

`JavaDetector` identifies the *tooling* JDK — the executable an operator points SPW at — which is not necessarily the JVM actually running the profiled Starsector process. In attach mode, `--java`/`--jcmd` only need to locate a working `jcmd`; the process being attached to may be running under a different Java entirely (the case this design explicitly targets: a Mikohime-managed Java 27/28 setup launched by a JDK other than the one on the operator's own PATH). SPW therefore additionally queries the target process directly via `jcmd <pid> VM.version` once attached, recording the result as a distinct `target_jvm` fingerprint alongside the tooling JDK's — never assuming the two are the same JVM, and surfacing both explicitly (e.g. "Profiler tooling JDK: Temurin 25" vs "Target JVM: Temurin 27+22-ea") rather than only ever reporting the tooling JDK's identity. A major-version mismatch between the two is recorded as an informational finding, not an error — it is an expected, common configuration for exactly the alternate-JDK setups this design profiles. The comparability gate's `java_major_version` variable prefers the target JVM's version when a capture provides one, falling back to the tooling JDK's for inventory-only snapshots where no target has been queried.

`MikohimeDetector` is configuration-oriented, not vendor-name-oriented. It recognizes a Mikohime-compatible configuration only from concrete launcher/configuration evidence (such as `Miko_Rouge.bat`, `Miko_Simple.txt`, or `Miko_Info.txt`) and records the parsed settings separately from the JDK identity. A modern Temurin runtime alone is therefore `GenericAlternateJDK`, not Mikohime. The detector may report configured memory, CPU, logging, large-page, Fast Rendering, resource-cache, and prepatcher settings; actual activation is confirmed independently by the corresponding detector and core-integrity evidence.

Fast Rendering warrants an adapter because its published distribution includes its own `fr.bat`, `fr.vmparams`, and Starsector-core installation path, and its public crash report shows renderer-bridge/executor work on a worker thread. The initial adapter is limited to this observed layout and evidence-based labeling; it must not claim a general rendering model or modify the installation. [Fast Rendering repository](https://github.com/Halke1986/starsector-render), [example renderer/executor stack](https://github.com/Halke1986/starsector-render/issues/1)

Alternate-JDK detection is generic first: JVM version output, executable path, launcher, layout, and VM parameters form the evidence. Named third-party kit profiles are added only after maintainable, version-specific fingerprints and fixtures exist; an unrecognized modern JDK remains fully profileable as `GenericAlternateJDK`.

### Comparability gate

Before SPW reports a delta, it compares the two environment fingerprints. It returns:

| Result | Meaning |
| --- | --- |
| `COMPARABLE` | Relevant capture, scenario, game, runtime, launcher, mod-set, and measurement settings match. |
| `PARTIALLY_CONTROLLED` | A reported delta is useful but one or more material variables changed; every changed variable is listed. |
| `NOT_COMPARABLE` | The run cannot support a direct performance-improvement claim. |

Changing Java major version, GC/JIT settings, launch mode, Fast Rendering state, core-integrity state, enabled-mod set/order, scenario, or capture settings is material by default. Users may explicitly define an A/B experiment that changes one of these variables; the report then calls it an experiment rather than attributing the result to an unrelated mod.

When several material variables change, SPW must state that causal attribution is not possible and propose a matrix of controlled runs. For example, changing Java and Fast Rendering calls for Java-17/Java-27 × FR-off/FR-on runs before reporting an isolated Java or FR effect. Prepatching, resource-cache state, and large-page configuration are independently tracked variables, not hidden implementation details.

### Configuration snapshots

Each capture writes a self-contained, local snapshot directory. This makes a later comparison reproducible even after launchers or Java installations have changed:

```text
profiles/<run-id>/
├─ profile.jfr
├─ environment.json
├─ java.json
├─ runtime-capabilities.json
├─ mods.json
├─ core-hashes.json
└─ PERFORMANCE_REPORT.md
```

The snapshot contains only locally generated metadata and the user-selected capture. It never copies game/mod sources or JDK binaries.

## Capture levels

Every capture selects one explicit level. The level controls only which JFR event categories the in-JVM collector enables, and whether it adds any custom low-overhead events; it never changes what the external SPW process is allowed to do with the resulting data.

| Level | Intent | Event set | Target overhead |
| --- | --- | --- | --- |
| `PASSIVE` | Leave running through ordinary, unscripted play with no perceptible impact. | Stock low-rate JFR events only: GC pauses, coarse CPU-load samples, thread start/stop, exceptions. No method sampling, no allocation profiling. | Near-zero; safe to leave on for an entire session. |
| `STANDARD` | Default investigative capture for "something feels slow." | Adds JDK method-sampling (execution samples), lock/blocking events, and coarse allocation-rate events at JFR's default intervals. | Low; suitable for a bounded investigation window (minutes), not indefinite play. |
| `DEEP_DIAGNOSTIC` | Targeted deep dive once `STANDARD` has narrowed a window or category. | Adds high-frequency method sampling, full allocation-profiling events, lock-contention detail, and — only at this level — the minimal custom JFR events described below. | Highest; explicitly time-bounded, never the default. |

The custom events available only at `DEEP_DIAGNOSTIC` are deliberately few and narrow — for V0.1, a simulation-tick-boundary marker emitted by the separately-installed, opt-in **SPW Tick Marker mod** (`spw/agent-mod`). It is an ordinary Starsector mod built only on the public, documented modding API (`EveryFrameScript`/`ModPlugin`), registered via `Global.getSector().addTransientScript(...)` exactly as any other mod would — never bytecode instrumentation or bytecode weaving of Starsector's own (obfuscated) classes, which the safety contract's "do not decompile" line rules out. They exist so the external analyzer can bucket stock JFR samples by game-tick without asking the collector to do that bucketing itself. Where a level's event set is satisfied entirely by stock JDK JFR events (`PASSIVE`, `STANDARD`), or the Tick Marker mod is not installed, no custom event is ever committed.

A `CaptureDescriptor` always records the selected level, and a report must state which level produced its evidence: `PASSIVE` evidence cannot support the same conclusions as `DEEP_DIAGNOSTIC` evidence.

### Profiler overhead accounting

Because the collector runs inside the profiled process, its own cost is a confound the report must account for, not ignore. Every capture records a `CollectorOverheadEstimate` alongside its `CaptureDescriptor`:

- The collector's own CPU time (its dedicated thread(s), read via the JVM's own thread-CPU-time accounting) and, at `DEEP_DIAGNOSTIC`, its own allocation footprint.
- The active capture level and the JFR event set it enabled, since overhead is primarily a function of level, not of the target application.
- Where practical, a short paired baseline: the same scenario or a fixed startup segment measured once with the collector at `PASSIVE` and once at the requested level, so the delta between them is an evidence-backed overhead figure rather than a vendor-quoted estimate.

A report's Limitations line must disclose the selected capture level and its estimated overhead whenever a conclusion could plausibly be sensitive to it (for example, a sub-millisecond frame-time claim captured at `DEEP_DIAGNOSTIC`). SPW must never present overhead-inflated timings as if they were the unobserved baseline.

## Architecture decisions

### 1. Inventory is read-only and tolerant

Parse `enabled_mods.json`, directory metadata, JAR manifests, JAR class indexes, archive candidates, and runtime/launcher details. Preserve malformed or unavailable metadata as findings with raw location evidence; do not discard the mod or invent a replacement ID. JAR ownership must support multiple candidates, because a populated installation can contain bundled duplicate libraries and multiple releases.

### 2. JFR is the initial capture mechanism

V0.1 uses Java Flight Recorder where the **actual launched JVM** supports it. SPW must detect the runtime used by Starsector rather than assuming that the system JDK is the game JVM. If JFR cannot be enabled, report the limitation and offer no fake substitute. Process attachment and JVM arguments are explicit user actions.

### 3. Attribution is evidence-ranked

Resolve a sampled frame in this order: exact class-to-JAR index, package prefix, JAR manifest/metadata, source layout evidence, then unknown. Report both the raw frame and the attribution path. Never map common dependencies (for example shared utility libraries) to one content mod without direct evidence.

### 4. The in-JVM collector stays minimal; the external process does the heavy work

The collector running inside the profiled Starsector process has exactly three jobs: enable/disable the JFR event set for the selected capture level, emit the small number of explicit custom events `DEEP_DIAGNOSTIC` requires (via the opt-in SPW Tick Marker mod, when installed), and self-measure its own resource cost (see "Profiler overhead accounting"). It performs no stack resolution, no attribution, no aggregation, no analysis, and no report or visualization rendering — those live entirely in the external SPW process, which consumes the written/streamed JFR data after (or alongside) the capture. This keeps the in-game footprint small and auditable: a user only has to trust a handful of JFR flags and, at `DEEP_DIAGNOSTIC`, one small mod built on the public modding API — never a full profiling engine, and never code that touches Starsector's own classes — running inside the game.

## Roadmap

1. **V0.1 — Environment fingerprint and JFR capture.** Detect installation, selected executable and actual runtime, JVM configuration, core integrity, enabled mods, metadata status, JAR ownership candidates, and thread inventory; emit `environment.json`, `core-integrity.json`, `mod-ownership.json`, `profile.jfr`, and a basic report. Every capture selects an explicit `PASSIVE`/`STANDARD`/`DEEP_DIAGNOSTIC` level and records a paired `CollectorOverheadEstimate`; the in-JVM collector stays limited to stock JFR events at `PASSIVE`/`STANDARD`, with the custom tick-boundary event available only at `DEEP_DIAGNOSTIC` and only when the separately-installed, opt-in SPW Tick Marker mod is enabled.
2. **V0.2 — Runtime capability detectors.** Deliver Vanilla Java 17, Generic Alternate JDK, Mikohime-compatible configuration, Fast Rendering, FR Resource Cache, StarsectorPrepatcher, and Unknown Modified Runtime detectors with evidence/provenance tests. Capabilities compose; no combination-specific profile explosion.
3. **V0.3 — Startup profiling.** Attribute launcher-to-main-menu time, class loading, resource parsing/loading, mod/dependency initialization.
4. **V0.4 — CPU and thread analysis.** Analyze sampled CPU, thread states, blocked time, locks, executor waits, and thread lifecycle.
5. **V0.5 — Mod attribution.** Harden class/JAR/package/dependency resolution and ambiguity reporting; add ownership-map regression fixtures.
6. **V0.6 — Allocation, GC, and retention analysis.** Report allocation rate, allocating frames, GC frequency/pause time, heap trends, high-water marks, and repeated-transition cleanup evidence.
7. **V0.7 — Combat benchmark mode (optional).** Use an explicit, user-created scenario manifest; report frame-time mean/median/p95/p99, spikes, CPU, GC, and allocations. This is an optional mode layered on the `PASSIVE`/`STANDARD`/`DEEP_DIAGNOSTIC` companion-profiling architecture, not a replacement for it — organic-session profiling remains SPW's primary use case.
8. **V0.8 — Baseline comparison.** Apply the comparability gate to vanilla/modded/suspect-disabled/patched runs and produce guarded delta reports.
9. **V0.9 — Rendering diagnostics.** Add advanced, optional render-thread and GL-stall observation. RenderDoc integration remains optional and best-effort.
10. **V1.0 — Diagnosis pipeline.** Deliver a repeatable inventory → capture → attribute → analyze → compare → report workflow with reproducibility metadata.

## Report language

Each conclusion contains a bottleneck category, raw evidence, attribution, confidence, and limitations. Example:

```text
Primary bottleneck: main-thread CPU
Likely owner: <local mod identifier> / <JAR>
Evidence: 31% sampled CPU across 3 compatible runs
Attribution: exact class-to-JAR index
Confidence: HIGH
Capture level: STANDARD (estimated collector overhead: <1% CPU)
Limitations: causation has not been experimentally isolated
```

## Bridgeforge interoperability

The only planned shared surface is a small, independent `shared-schema` package or copied JSON Schema documents:

```text
environment.schema.json
mod-inventory.schema.json
jar-ownership.schema.json
report-metadata.schema.json
```

SPW may emit a performance finding that Bridgeforge presents as a **modernization opportunity**. Bridgeforge may provide a patched-build identifier that SPW uses as a comparison label. Neither tool imports the other’s codebase, requires the other at runtime, or treats performance evidence as proof that a migration is behaviorally correct.
