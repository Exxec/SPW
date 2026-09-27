# Sister repository interchange design (2026-09-27)

Status: proposed. SPW remains an independent read-only profiler. BridgeForge and VoidSmith import only explicit local artifacts; neither becomes a runtime dependency.

## Versioned performance export for BridgeForge

SPW should publish one documented `performance-report.json` schema from `diagnose`/`analyze`, derived from its existing analysis and `reproducibility.json` artifacts. Required fields: schema version, SPW version, capture level, source artifact hashes, install/environment fingerprint, mod identity with enabled state, attribution confidence, sample count, CPU metric and unit, startup metric and unit, and explicit unavailable/ambiguous reasons. No invented per-mod startup or CPU value is allowed. A comparison export must carry SPW's `compare` result and changed variables; BridgeForge may describe a delta only when SPW says the captures are comparable.

Build a neutral synthetic export fixture and schema/version tests. Test missing metrics, ambiguous class ownership, corrupted hashes, and incomparable captures. Keep paths local and omit game/mod content from fixtures. BridgeForge's existing `spw_bridge.py` can then replace its guessed key aliases for this schema while retaining legacy imports as unverified.

## Mod identity inventory for BridgeForge and VoidSmith

Publish a small versioned, optional JSON inventory from SPW's existing read-only inventory. Record selected install fingerprint, observed relative mod directory, declared ID/version, enabled/disabled state with evidence source, metadata and JAR hashes, duplicate-ID groups, and parse warnings. A directory path alone is not portable identity. Consumers may join only on unambiguous ID plus root/hash; changed installs, renamed-disabled metadata, stale enabled lists, and duplicate IDs must remain visible. Consumers keep their own scanners and work without this export.

Acceptance uses a neutral synthetic install covering duplicate IDs, disabled metadata rename, stale `enabled_mods.json`, missing metadata, and changed JAR bytes. No real mod or game data enters the repository.
