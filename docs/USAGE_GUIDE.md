# SPW Usage Guide

A practical, task-oriented walkthrough of the `spw` CLI. For *why* it's built this way, see [PERFORMANCE_WORKBENCH_DESIGN.md](PERFORMANCE_WORKBENCH_DESIGN.md); for what's implemented versus deferred, see [PERFORMANCE_WORKBENCH_ROADMAP.md](PERFORMANCE_WORKBENCH_ROADMAP.md).

## Requirements

- Python 3.10+
- A JDK whose `bin/` contains `jcmd` and `jfr` (the same JDK Starsector's `java` executable ships with, or points at, usually works). SPW shells out to these — it does not read JFR files with a bundled parser.
- Windows is the only platform this has been exercised on so far (GPU/driver fingerprinting in particular is Windows-only via PowerShell CIM).

Install in editable mode from the repo root:

```powershell
py -3 -m pip install -e .
```

This registers an `spw` console script; every example below also works as `py -3 -m spw ...` without installing.

## The five things SPW can tell you

1. **What's actually installed** (`inventory`) — mods, their declared vs. actual compatibility, Java runtime details, Fast Rendering/Mikohime/Prepatcher presence, GPU/driver info, core-file integrity against a baseline.
2. **What happened during a play session** (`capture` + `analyze`) — CPU samples, allocation pressure, GC pauses, per-thread activity, attributed to specific mod jars where the evidence supports it.
3. **Whether two runs are honestly comparable** (`compare`) — a gate that refuses to let you draw "mod X is slow" conclusions from two runs that also differ in JVM flags, enabled mods, or GPU driver state.
4. **What crashed** (`crash-logs`) — parses `hs_err_pid*.log` files Starsector/the JVM already writes, no capture needed.
5. **All of the above in one command** (`diagnose`) — the pipeline most people should reach for first.

## Quick start: one-shot diagnosis

```powershell
spw diagnose "C:\path\to\Starsector" --output .\artifacts --attach-pid <pid> --level STANDARD --duration 60 --java "C:\path\to\jdk\bin\java.exe"
```

What this does, in order:
1. Fingerprints the installation (`inventory/`).
2. Attaches to the already-running game process `<pid>` and records a 60-second JFR capture at `STANDARD` detail (`capture/`).
3. Normalizes and analyzes the recording, attributing samples to mods where possible (`analysis/`).
4. Writes `DIAGNOSIS_REPORT.md` (human-readable summary) and `reproducibility.json` (exact invocation, timestamp, and hashes of every artifact, so a report can be checked against its inputs later).

Finding `<pid>`: on Windows, `Get-Process java | Select-Object Id,Path` while Starsector is running, or Task Manager's Details tab.

To also compare this run against a previous one, add `--compare-with .\prior-artifacts --experiment-variable enabled_mod_set_or_order` (see [Comparing two runs](#4-comparing-two-runs-honestly) below for why the second flag matters).

## Step by step

### 1. Inventory (read-only, no capture)

```powershell
spw inventory "C:\path\to\Starsector" --output .\artifacts
```

Never launches or attaches to anything; only reads files and (unless `--no-execute-java` is passed) runs `java -version`-style and GPU-inventory queries. Useful on its own when you just want a fingerprint — e.g. before/after installing a mod, or to attach to a bug report.

Options worth knowing:
- `--no-execute-java` — skip every external command (Java version probing, GPU/driver queries) and rely on static evidence only. Use this if you want a guaranteed side-effect-free run, at the cost of a less complete fingerprint.
- `--baseline-catalog` / `--starsector-build` — point core-integrity checking at a specific versioned catalog of known-good core files, so modified/replaced core files are flagged with evidence rather than guessed at.
- `--extra-detectors-dir <path>` — load additional runtime-capability detectors from `.py` files in this directory (each exposing a `DETECTORS` list or a `detect()` function) alongside the built-in ones. For custom environment checks specific to your setup.

### 2. Capture (explicit and opt-in — this is the only command that touches a running JVM)

Two mutually exclusive modes:

**Attach to an already-running game:**
```powershell
spw capture --attach-pid 12345 --level STANDARD --duration 60 --java "C:\...\java.exe" --output .\artifacts
```

**Launch the game yourself and capture from the start:**
```powershell
spw capture --java "C:\...\java.exe" --output .\artifacts -- -jar starfarer.jar
```
(everything after `--` is passed through as the launch command; `--` is required so flag-like launch arguments aren't parsed as `spw` flags.)

Capture levels (`--level`):
- `PASSIVE` — lowest overhead, coarse sampling. Safe to leave on during normal play.
- `STANDARD` (default) — the everyday choice: execution samples, allocation samples, GC events, thread activity.
- `DEEP_DIAGNOSTIC` — everything in `STANDARD` plus, if the [SPW Tick Marker mod](#the-tick-marker-mod-deep_diagnostic-only) is installed and enabled, a `com.spw.TickBoundary` event per campaign tick, enabling tick-indexed correlation of stalls. Highest overhead; use for targeted investigation, not routine play.

`--estimate-overhead` (attach mode only) runs a short paired `PASSIVE`-vs-selected-level measurement first and reports a `CollectorOverheadEstimate`, so you know how much the act of measuring is itself costing you before trusting the real capture's numbers.

Output includes a `capture-descriptor.json` recording exactly how the capture was taken (level, duration, attach vs. launch, the command line if launched) — this is what `record-scenario` later reads.

### 3. Analyze

```powershell
spw analyze .\artifacts\profile.jfr --mod-ownership .\artifacts\mod-ownership.json --java "C:\...\java.exe" --output .\artifacts
```

Pure external-process work — no game or JVM interaction. Reads the `.jfr` file (via `jfr print --json`), normalizes event schemas, and if given `--mod-ownership` (produced by a prior `inventory` run), attributes CPU/allocation samples to the mod jar that most plausibly owns them, using an evidence-ranked scheme: exact class-to-jar match, then package-prefix match, then "runtime/core," then "unknown" — never a confident-sounding guess dressed up as certainty.

If the capture was `DEEP_DIAGNOSTIC` with the Tick Marker mod active, this also writes `tick-analysis.json`, correlating stalls to specific campaign ticks. Without the mod, it still writes that file with `ticks_available: false` and an explanation, rather than silently omitting it.

### 4. Comparing two runs honestly

```powershell
spw compare .\before-artifacts .\after-artifacts --output .\comparison --experiment-variable enabled_mod_set_or_order
```

This is the piece that stops "I changed something and it feels faster" from becoming a false conclusion. SPW checks both artifact sets against a list of **material variables** (Java major version, JVM arguments, enabled mod set/order, Fast Rendering state, core-integrity state, capture settings, GPU/driver state) and classifies the comparison as:
- `COMPARABLE` — no material variable differs.
- `PARTIALLY_CONTROLLED` — you declared exactly one intentional change via `--experiment-variable`, and it's the only thing that differs.
- `NOT_COMPARABLE` — something you didn't declare also changed; the comparison output says what, so you can decide whether to trust it anyway or re-run with that variable controlled.

Always pass `--experiment-variable` when you know what you changed — otherwise an intentional change registers as unexplained drift.

### 5. Crash log parsing

```powershell
spw crash-logs "C:\path\to\Starsector" --output .\artifacts
```

Non-recursive scan of a directory for `hs_err_pid*.log` files (the JVM writes these itself on a native crash — no capture, no attach required). Extracts JVM/build info, the faulting thread, and the native stack, so a crash can be triaged without reproducing it live.

### 6. Turning a capture into a repeatable scenario

```powershell
spw record-scenario .\artifacts\capture-descriptor.json --name my-scenario --output .\scenario.json
```

If you launched Starsector yourself through `spw capture` (launch mode, not attach mode), the resulting `capture-descriptor.json` has the exact command line used. This subcommand turns that into a scenario manifest you can re-run later with `spw benchmark`, so an interesting session becomes reproducible instead of a one-off.

```powershell
spw benchmark .\scenario.json --output .\benchmark
```

Benchmark mode is deliberately optional and secondary — SPW's primary purpose is measuring real play, not synthetic battles. `--frame-time-samples <file>` accepts an optional file of one per-frame duration (seconds) per line, if you're feeding it external frame-time data.

### 7. Viewing results

```powershell
spw view .\artifacts
```

Renders a single self-contained HTML file (default: `report.html` inside the artifacts directory; override with `--output`) summarizing whatever's in that directory — inventory, capture, analysis, or a full `diagnose` run. No server, no external assets; open it directly in a browser.

## The Tick Marker mod (`DEEP_DIAGNOSTIC` only)

`spw/agent-mod` is an ordinary Starsector mod — not part of the `spw` Python package — built entirely on the public `EveryFrameScript`/`ModPlugin` modding API. It emits one `com.spw.TickBoundary` JFR event per campaign tick. It does not instrument, patch, decompile, or otherwise touch Starsector's own classes.

To use it:
1. Copy `releases/spw-tick-marker/` (or extract `releases/spw-tick-marker.zip`) into the target installation's `mods/` directory, so `mods/spw-tick-marker/mod_info.json` exists. This is a prebuilt, runtime-only copy (`mod_info.json` + the compiled jar, no source); `spw/agent-mod/` remains the source of truth if you need to rebuild it.
2. Enable `spw_tick_marker` from the in-game mod manager like any other mod.
3. Run a `DEEP_DIAGNOSTIC`-level capture. `spw analyze` will report tick-indexed correlation in `tick-analysis.json`.

Without the mod installed/enabled, `DEEP_DIAGNOSTIC` captures still work — `tick-analysis.json` just reports `ticks_available: false` with an explanation, not an error.

To rebuild the jar after editing its Java source (requires a local Starsector install for `starfarer.api.jar`):
```powershell
javac -cp "C:\path\to\Starsector\starsector-core\starfarer.api.jar" -d build --release 17 spw\agent-mod\src\com\spw\tickmarker\*.java
jar --create --file spw\agent-mod\jars\spw-tick-marker.jar -C build .
```

## Safety notes

- SPW treats the installation and mod directories as **read-only inputs**; it never writes into them (the Tick Marker mod is copied there manually by you, as an ordinary mod install — SPW itself doesn't do it).
- A capture only ever starts against a process ID or launch command you explicitly supply. SPW never auto-discovers or attaches to a game on its own.
- If JFR can't be enabled on the target JVM, SPW reports that limitation rather than fabricating data.
- All output goes to the `--output` directory you name — nothing is written elsewhere.

## Troubleshooting

- **`jcmd`/`jfr` not found** — pass `--java` pointing at the JDK's `java.exe`; SPW looks for `jcmd`/`jfr` alongside it. If your Starsector install bundles its own JRE without those tools, point at a separate full JDK instead — the capture still targets the game's JVM via `--attach-pid`.
- **`NOT_COMPARABLE` result you didn't expect** — read the specific material variable(s) it names in the output; something changed between runs (commonly: mod set, JVM args, or GPU driver) that you didn't declare via `--experiment-variable`.
- **`ticks_available: false`** — the Tick Marker mod isn't installed/enabled in the captured run, or the capture wasn't `DEEP_DIAGNOSTIC`. Everything else in the analysis is still valid.
- **Capture file looks empty/truncated right after capture ends** — this shouldn't happen (SPW polls for a stable file before returning), but if a JFR file is manually cut off mid-write, re-run the capture rather than trying to salvage a partial recording.

## Running the test suite

```powershell
py -3 -m unittest discover -s tests
```
