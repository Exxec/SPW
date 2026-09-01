# Starsector Performance Workbench (SPW)

An independent, offline runtime-profiling and benchmarking workbench for Starsector installations. SPW answers *what is slow, when, and with what evidence?* It never modifies Starsector, a mod, JVM launch configuration, or save files.

SPW is a separate program from Bridgeforge (a legacy-mod modernization scanner, developed in its own separate repository) — the two do not share a codebase or a runtime dependency; either must remain useful with the other absent. See [docs/USAGE_GUIDE.md](docs/USAGE_GUIDE.md) for a full walkthrough of every command, [docs/PERFORMANCE_WORKBENCH_DESIGN.md](docs/PERFORMANCE_WORKBENCH_DESIGN.md) for the full design, [docs/PERFORMANCE_WORKBENCH_ROADMAP.md](docs/PERFORMANCE_WORKBENCH_ROADMAP.md) for implementation status, and [docs/PERFORMANCE_WORKBENCH_PROGRESS_LOG.md](docs/PERFORMANCE_WORKBENCH_PROGRESS_LOG.md) for a detailed log of what's been built and validated.

Licensed under the GNU General Public License v3.0 (or later) — see [LICENSE](LICENSE).

## Run

Requires Python 3.10+ and a JDK with `jcmd`/`jfr` (used to control and read back Java Flight Recorder).

The fastest path is the V1.0 pipeline, which chains every stage below into one command:

```powershell
py -3 -m spw diagnose C:\path\to\Starsector --output .\artifacts --attach-pid <pid> --level STANDARD --duration 60 --java "C:\path\to\jdk\bin\java.exe"
```

This writes `inventory/`, `capture/`, and `analysis/` subdirectories, a top-level `DIAGNOSIS_REPORT.md`, and `reproducibility.json` (invocation, timestamp, artifact hashes). Each stage is also available on its own:

```powershell
# Fingerprint the installation and its mods (read-only).
py -3 -m spw inventory C:\path\to\Starsector --output .\artifacts

# Capture a JFR recording at an explicit level (PASSIVE / STANDARD / DEEP_DIAGNOSTIC).
py -3 -m spw capture --attach-pid <pid> --level STANDARD --duration 60 --java "C:\path\to\jdk\bin\java.exe" --output .\artifacts

# Normalize, attribute, and analyze an already-captured recording.
py -3 -m spw analyze .\artifacts\profile.jfr --mod-ownership .\artifacts\mod-ownership.json --java "C:\path\to\jdk\bin\java.exe" --output .\artifacts

# Apply the comparability gate to two prior artifact directories.
py -3 -m spw compare .\before-artifacts .\after-artifacts --output .\comparison

# Optional: run an explicit, user-authored benchmark scenario manifest.
py -3 -m spw benchmark .\scenario.json --output .\benchmark

# Turn an observed launch-mode capture's own command line into a reusable scenario manifest.
py -3 -m spw record-scenario .\artifacts\capture-descriptor.json --name my-scenario --output .\scenario.json

# Parse JVM/native crash logs (hs_err_pid*.log) found in a directory -- no capture required.
py -3 -m spw crash-logs C:\path\to\Starsector --output .\artifacts

# Render a single, self-contained local HTML summary of any prior output directory.
py -3 -m spw view .\artifacts
```

Capture is opt-in and explicit: SPW never launches or attaches to a process without being told to. Pass `--estimate-overhead` to `capture`/`diagnose` to get a `CollectorOverheadEstimate` from a short paired `PASSIVE`-vs-selected-level measurement. Pass `--extra-detectors-dir <path>` to `inventory`/`diagnose` to run third-party runtime-capability detectors (`.py` files exposing `DETECTORS`/`detect()`) from a directory you name, alongside the built-in ones.

## The SPW Tick Marker mod (optional, `DEEP_DIAGNOSTIC` only)

`spw/agent-mod` is an ordinary Starsector mod, not part of the `spw` Python package, that emits a `com.spw.TickBoundary` JFR event once per campaign tick. It uses only the public `EveryFrameScript`/`ModPlugin` modding API — it does not instrument, patch, or otherwise touch Starsector's own classes.

To use it: copy `releases/spw-tick-marker/` (or extract `releases/spw-tick-marker.zip`) into the target installation's `mods/` directory (so `mods/spw-tick-marker/mod_info.json` exists) and enable `spw_tick_marker` like any other mod. This is a prebuilt, runtime-only copy (`mod_info.json` + the compiled jar, no source) meant to be dropped in directly; `spw/agent-mod/` remains the source of truth if you need to rebuild it. A `DEEP_DIAGNOSTIC` capture will then include tick-boundary events; `spw analyze` reports them in `tick-analysis.json` regardless of whether the mod is present (`ticks_available: false` with an explanatory limitation, not an error, when it isn't installed or enabled).

To rebuild the jar after editing its source (requires a local Starsector install for `starfarer.api.jar`):

```powershell
javac -cp "C:\path\to\Starsector\starsector-core\starfarer.api.jar" -d build --release 17 spw\agent-mod\src\com\spw\tickmarker\*.java
jar --create --file spw\agent-mod\jars\spw-tick-marker.jar -C build .
```

After rebuilding, regenerate `releases/` from the updated `spw/agent-mod/` (copy `mod_info.json` and `jars/spw-tick-marker.jar` into `releases/spw-tick-marker/`, then re-zip it) so the prebuilt copy doesn't drift from source.

## Safety

- The installation and mod directories are read-only inputs; SPW never writes into them.
- A capture only starts against an explicitly identified process ID or an explicitly supplied launch command.
- If JFR cannot be enabled on the target JVM, SPW reports the limitation instead of substituting fake data.
- All output is written to an explicit `--output` directory local to the machine.

## Status

V0.1 through V1.0 all have a first working implementation, including the `DEEP_DIAGNOSTIC` tick-boundary event and its tick-indexed sample correlation, GPU/driver fingerprinting, native crash-log ingestion, a local HTML report viewer, cached JAR indexing, and a third-party detector extension point — see [docs/PERFORMANCE_WORKBENCH_ROADMAP.md](docs/PERFORMANCE_WORKBENCH_ROADMAP.md) and [docs/PERFORMANCE_WORKBENCH_RECOMMENDATIONS.md](docs/PERFORMANCE_WORKBENCH_RECOMMENDATIONS.md) for what is genuinely evidence-backed today versus an explicitly-surfaced limitation (save-complexity fingerprinting remains intentionally scoped down; rendering-thread identification and Fast Rendering Resource Cache/Prepatcher detection were both upgraded from generic pattern-matching to real, documented-layout fingerprints after live validation, see below).

**Both live-Starsector validation passes have happened.** SPW attached to and captured from a real running Starsector process, with the Tick Marker mod installed and enabled by the actual game (previously only ever exercised against a synthetic Java test harness). Pass 1 (near-vanilla, isolating first-load risk) fired 9,377 real `com.spw.TickBoundary` events correctly and caught a real gap — rendering-thread identification by name alone missed the actual dominant render thread (a JVM-default-named thread doing 86% of the real work), now fixed with a second, stack-content-based signal. Pass 2 (the real ~154-mod install, full `DEEP_DIAGNOSTIC`) pushed 15,946 execution samples with zero read failures, correctly attributed CPU samples across ~50 distinct real mods, and correctly flagged `NOT_COMPARABLE` when compared against pass 1 for three simultaneous real changes (Java 27→28, JVM arguments, and the mod set) rather than risking a false conclusion. See the progress log for full detail on both.

Run the test suite with:

```powershell
py -3 -m unittest discover -s tests
```
