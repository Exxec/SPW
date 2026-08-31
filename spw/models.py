from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1.0"


@dataclass
class Finding:
    id: str
    category: str
    severity: str
    confidence: str
    explanation: str
    file: str | None = None
    evidence: list[str] = field(default_factory=list)


@dataclass
class FingerprintResult:
    """Aggregates everything collected for one `spw inventory` run."""

    installation_path: Path
    java_executable: Path | None = None
    java: dict[str, Any] = field(default_factory=dict)
    target_jvm: dict[str, Any] = field(default_factory=dict)
    gpus: dict[str, Any] = field(default_factory=dict)
    configured_jvm_arguments: list[str] = field(default_factory=list)
    enabled_mod_order: list[str] | None = None
    mods: list[dict[str, Any]] = field(default_factory=list)
    jar_ownership: list[dict[str, Any]] = field(default_factory=list)
    shared_package_prefixes: dict[str, list[str]] = field(default_factory=dict)
    package_prefix_index: dict[str, list[str]] = field(default_factory=dict)
    class_index: dict[str, list[str]] = field(default_factory=dict)
    core_integrity: dict[str, Any] = field(default_factory=dict)
    runtime_capabilities: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def add(self, **kwargs: Any) -> None:
        self.findings.append(Finding(**kwargs))

    def _findings_dicts(self, categories: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        findings = self.findings if categories is None else [f for f in self.findings if f.category in categories]
        return [asdict(finding) for finding in findings]

    def environment_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "installation_path": str(self.installation_path),
            "java": self.java,
            # `java` is the *tooling* JDK (the executable passed as
            # `--java`, fingerprinted via its own `release` file/`-version`
            # output). `target_jvm` -- only populated after an attach-mode
            # capture, via `jcmd <pid> VM.version` -- is the JVM actually
            # running the profiled process, which can differ from the
            # tooling JDK (e.g. a Mikohime-managed Java 27/28 setup
            # attached to from a different JDK on the operator's PATH).
            # Comparability and reporting must not conflate the two.
            "target_jvm": self.target_jvm,
            "gpus": self.gpus,
            "configured_jvm_arguments": self.configured_jvm_arguments,
            "enabled_mod_order": self.enabled_mod_order,
            "mod_count": len(self.mods),
            # The full, unfiltered list -- kept here for PERFORMANCE_REPORT.md
            # and existing consumers. mod-ownership.json/core-integrity.json
            # additionally carry their own category-filtered subset, so a
            # reader grepping one of those files for "what's wrong here"
            # finds it there too, not only under a filename that doesn't
            # suggest it holds their category.
            "findings": self._findings_dicts(),
        }

    def mod_ownership_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "mods": self.mods,
            "jar_ownership": self.jar_ownership,
            "shared_package_prefixes": self.shared_package_prefixes,
            "package_prefix_index": self.package_prefix_index,
            "class_index": self.class_index,
            "findings": self._findings_dicts(("mod-inventory", "jar-ownership")),
        }

    def findings_dict(self) -> dict[str, Any]:
        """All findings, unfiltered by category.

        For contexts with no inventory stage to fold findings into (e.g.
        the standalone `capture` subcommand has no `environment.json` of
        its own) -- otherwise findings raised there would have nowhere to
        land and be silently discarded.
        """

        return {"schema_version": SCHEMA_VERSION, "findings": self._findings_dicts()}

    def core_integrity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            **self.core_integrity,
            "findings": self._findings_dicts(("core-integrity",)),
        }

    def runtime_capabilities_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "capabilities": self.runtime_capabilities,
        }
