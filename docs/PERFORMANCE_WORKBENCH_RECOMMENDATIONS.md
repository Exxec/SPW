# Recommendations for additional roadmap features

These are candidate additions to [PERFORMANCE_WORKBENCH_ROADMAP.md](PERFORMANCE_WORKBENCH_ROADMAP.md), not committed scope. Each includes the gap it addresses and why it fits SPW's evidence-first, offline, non-destructive contract. None of these should be started before the numbered roadmap they extend.

## 1. Driver/GPU version as a comparability-gate variable

The environment fingerprint already records GPU and graphics-driver identifiers (design doc, "Environment fingerprinting"), but the comparability gate's list of material variables (design doc, "Comparability gate") does not mention them. A driver update between two runs is a common, silent cause of an apparent regression that has nothing to do with a mod or a Java change.

**Why:** Without this, a user could get a `COMPARABLE` verdict across a driver update and wrongly blame a mod.
**Fits at:** V0.8, as an addition to the existing materiality list — no new subsystem required, just widening a check that already has the data.

## 2. Native crash log ingestion as a lightweight capture type

Starsector/JVM crashes (`hs_err_pid*.log`) are common in a modded install and carry useful evidence — the faulting frame, loaded native libraries, thread list — without requiring a deliberate JFR session. A user who just crashed wants a report of what was true at that moment, not a request to reproduce it under a profiler.

**Why:** Extends SPW's value to the failure case, not just the "things feel slow" case, using data the JVM already wrote to disk.
**Fits at:** alongside V0.4 (thread/lock analysis reuses the same parsing) as an optional, independent capture type — never as a substitute for JFR, since a crash log is a single snapshot, not a timeline.

## 3. Baseline-catalog freshness warning

Core-integrity classification (design doc, "Core-integrity classification") depends entirely on a version-matched baseline catalog. If a user keeps reusing an old catalog against a newer Starsector build than the one it targets, results should not silently degrade to `BASELINE_UNAVAILABLE` without comment — the report should say explicitly that a newer catalog is expected and none was found.

**Why:** Cheap to add, and it turns a quiet gap into an actionable message instead of a confidence cliff the user has to notice on their own.
**Fits at:** V0.1/V0.2 hardening — it is a report-language change, not a new detector.

## 4. Cached JAR class-index reuse keyed by content hash

A populated installation was observed at 138 JAR files across 119 directories. Re-hashing and re-indexing every JAR on every run wastes time the moment SPW is used iteratively (before/after a mod change, or across repeated capture sessions). A local, disposable cache keyed by JAR content hash — never shipped, never covering game/mod content itself, just the index — would keep repeat runs fast.

**Why:** This is about SPW's own usability, not the profiling model; skip it until repeat-run friction is actually reported.
**Fits at:** any time after V0.5, once the JAR ownership index shape has stabilized enough that a cache format is worth committing to.

**Update:** now measured, not just estimated — a real 155-mod, 138-JAR installation produces a 2.8&nbsp;MB `mod-ownership.json` with ~27,000 `class_index` entries. Re-scanning that on every `spw inventory`/`spw diagnose` run is real, now-quantified cost, not a hypothetical.

## 5. Save-complexity fingerprint alongside a capture

Late-game slowdowns in Starsector are frequently tied to campaign-layer growth (colony count, fleet count, market count) rather than combat rendering. Recording a small, explicit, safe summary (counts only, never save content) alongside a capture would let later analysis phases correlate "this run was slow" with "this save had N colonies" without ever reading or exporting save data.

**Why:** A large share of real-world complaints are late-save-specific; without this signal the workbench can misattribute a campaign-layer cost to a mod or rendering path.
**Fits at:** V0.6/V0.7, once allocation/GC analysis and benchmark mode exist to consume the correlation.

## 6. Community/third-party runtime-capability detector plugin points

The design's runtime capability stack (`RuntimeAdapter` layers) is deliberately open-ended — new launchers and JVM distributions appear in the Starsector modding community faster than any one maintainer can add fingerprints for. A narrow extension point (a detector contract plus fixtures, not a general plugin system) would let the community contribute new adapters without touching SPW's core.

**Why:** Keeps the "generic first, named adapters only with real evidence" discipline (design doc, "Alternate-JDK detection") while not gating every new launcher on a core release.
**Fits at:** after V0.2 ships with its initial detector set and the `RuntimeAdapter` contract has run against real-world variety.

## 7. Local static HTML viewer for `performance-report.json`

A non-technical mod user is more likely to open a double-clickable local HTML file than to read `PERFORMANCE_REPORT.md` or `performance-report.json` directly. A strictly local, no-network, generated-from-the-JSON viewer would widen the audience without changing what SPW measures or claims.

**Why:** Report language and evidence quality (design doc, "Report language") stay identical; this only changes presentation.
**Fits at:** late — after V0.8, once report content is stable enough that a viewer isn't chasing a moving schema.

## 8. Scenario-manifest recorder for combat benchmark mode

V0.7 calls for "an explicit, user-created scenario manifest." Hand-authoring one is a barrier. Recording an actual play session's reproducible inputs (seed, settings, scripted sequence) into a manifest — rather than asking users to write one from scratch — would make benchmark mode more likely to actually get used.

**Why:** A benchmark mode nobody can author scenarios for doesn't get exercised; this directly targets adoption of V0.7.
**Fits at:** V0.7, as a companion tool to the manifest format, not a replacement for the explicit-manifest requirement.

## 9. Tick-indexed bucketing of CPU/allocation samples

Now that the SPW Tick Marker mod makes real `com.spw.TickBoundary` events possible (see the roadmap's "`DEEP_DIAGNOSTIC` tick-boundary event" section), `tick_analysis.py` only reports tick-level stats (count, average/max duration, stalls) in isolation. The natural next step is correlating *other* DEEP_DIAGNOSTIC evidence — `jdk.ExecutionSample`, `jdk.ObjectAllocationSample` — against the nearest preceding tick index, so a report can say "tick 4,213 was 6x the average duration, and here is what was sampled during it" instead of a reader having to cross-reference two separate JSON files by timestamp themselves.

**Why:** This is the actual payoff the tick-boundary event exists for; today it only proves the plumbing works, not that it answers "what happened during the slow tick."
**Fits at:** directly on top of the now-implemented tick-boundary event, once it has been validated against a real, live Starsector session — building the correlation logic before confirming the event fires correctly in-game would be premature.

## 10. Thread identity by OS/Java thread id, not name alone

`cpu_thread_analysis.py`'s thread-lifecycle tracking (`still_running_at_capture_end`) and `attribution.py`'s per-thread breakdown both key on `javaName`/`osName` strings. A long play session with pooled or restarted worker threads sharing a name (a real pattern: Java thread pools commonly reuse names like `pool-1-thread-1`) could make a thread that actually ended look "still running" if a same-named thread starts later, or blend two different threads' samples into one bucket.

**Why:** Low-severity today (it only affects the readability of a summary count, not a safety-relevant conclusion), but worth fixing before leaning on thread-level attribution for anything more consequential.
**Fits at:** a small, self-contained refinement to V0.4/V0.5 — key by `(osThreadId, javaName)` or `javaThreadId` where available, falling back to name only when an id is absent.

## 11. Proactive Tick Marker mod detection during inventory

`spw inventory` currently has no way to tell a user "the SPW Tick Marker mod isn't installed, so `DEEP_DIAGNOSTIC` won't include tick-boundary data" until after they've already run a capture and gotten an empty `tick-analysis.json`. Since inventory already parses every mod's `mod_info.json`, it already has enough information to check for `spw_tick_marker`'s id among the discovered (and separately, the enabled) mods.

**Why:** Turns a silent, easy-to-miss capability gap into an actionable finding at the point the user is most likely to act on it — before capturing, not after.
**Fits at:** a small addition to `mod_inventory.py`/`runtime_capability.py`, any time after the Tick Marker mod itself ships.

## 12. Findings split by the JSON file they actually describe

`environment.json` currently embeds every finding SPW produces — mod-inventory, jar-ownership, core-integrity, capture, overhead, benchmark — regardless of category, because `FingerprintResult.environment_dict()` is the one place all findings get serialized. A reader grepping `core-integrity.json` for "what's wrong here" won't find `baseline-catalog-unavailable` there; it's only in `environment.json`, whose name doesn't suggest it holds core-integrity findings at all.

**Why:** Purely an information-architecture inconsistency (nothing crashes or is lost — `PERFORMANCE_REPORT.md` always shows the full list), but it undermines the "each artifact is independently inspectable" intent behind having separate JSON files at all.
**Fits at:** a schema-affecting change, so it should happen deliberately (e.g. alongside the `shared-schema` work in "Bridgeforge interoperability") rather than as an incidental fix — group findings by `category` and either split them across the relevant files or add a category index.

## 13. Surface *why* zero JFR events were read, not just that there were none

`jfr_events.read_events` returns an empty list on any failure — missing tool, corrupt recording, or a `jfr print --json` call that simply timed out (the 180-second timeout is a real risk for a large `DEEP_DIAGNOSTIC` recording over many minutes of play, since verbose per-event JSON with full stack traces can be far larger than the recording file itself). Every analyzer built on top of it can only report "0 samples available," which reads identically whether the level genuinely disabled that event or the read itself failed.

**Why:** A user who captured 20 minutes at `DEEP_DIAGNOSTIC` and gets an empty CPU report has no way to tell "the game was idle" from "the analysis silently timed out" — exactly the kind of ambiguity the design's evidence-and-limitations discipline is meant to prevent.
**Fits at:** a `read_events` signature change (returning a status/limitation alongside the list) that touches every analyzer built on it, so batch it with other V0.4–V0.6 hardening rather than doing it piecemeal.

## 14. Distinguish the tooling JDK from the target JVM in attach mode

Done — see the progress log's 2026-08-31 "Target JVM fingerprinting" entries. Kept here for the record: attach mode originally only ever fingerprinted the `--java`/`--jcmd` executable (the tooling JDK), never the JVM actually running the attached process, which is a real gap for Mikohime/alternate-JDK setups where the two differ.

## 15. A real flame graph in the HTML viewer

The static HTML viewer's CPU/allocation sections (now sortable tables, see the 2026-08-31 progress-log entry) still only show a flat top-N list of the *single hottest frame* per sample. A flame graph — the standard way to read a profile, and the thing JDK Mission Control has that SPW doesn't — needs the full call stack per sample aggregated into a tree, not just the top frame, plus SVG/canvas rendering logic to draw it.

**Why:** This is the single biggest remaining readability gap against JMC identified when the user asked "is this better than the bundled Java profiler, are results easier to interpret?" — SPW wins on Starsector-specific context (mod attribution, comparability gating) but loses on general profiling ergonomics; a flame graph would close most of that gap using data SPW already collects (`jfr_events.stack_class_names` already walks the full stack, it's just not retained past the top frame in the current aggregation).
**Fits at:** a genuine, standalone piece of work — full-stack aggregation in `cpu_thread_analysis.py`/`allocation_gc_analysis.py` plus a hand-rolled SVG renderer in `report_viewer.py` (no external charting library, per the viewer's own no-network/no-external-script constraint). Not a "cheap win"; scope it as its own item rather than folding it into unrelated work.
