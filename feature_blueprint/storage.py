from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from feature_blueprint.models import AppConfig, EvidenceRecord, EvidenceType


@dataclass(frozen=True)
class RunPaths:
    root: Path
    evidence: Path
    documentation: Path
    api: Path
    browser: Path
    network: Path
    screenshots: Path

    @classmethod
    def create(cls, config: AppConfig) -> "RunPaths":
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        root = config.output.root_directory / f"{config.project.name}-{timestamp}"
        paths = cls(
            root=root,
            evidence=root / "evidence",
            documentation=root / "evidence" / "documentation",
            api=root / "evidence" / "api",
            browser=root / "evidence" / "browser",
            network=root / "evidence" / "network",
            screenshots=root / "evidence" / "screenshots",
        )
        for path in (
            paths.root,
            paths.evidence,
            paths.documentation,
            paths.api,
            paths.browser,
            paths.network,
            paths.screenshots,
        ):
            path.mkdir(parents=True, exist_ok=True)
        resolved = config.model_dump(mode="json")
        (root / "input.yaml").write_text(
            yaml.safe_dump(resolved, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return paths


class EvidenceStore:
    def __init__(self, paths: RunPaths) -> None:
        self.paths = paths
        self.records: list[EvidenceRecord] = []
        self.warnings: list[str] = []

    def add(
        self,
        evidence_type: EvidenceType,
        source: str,
        summary: str,
        artifact_path: Path,
    ) -> str:
        evidence_id = f"ev-{len(self.records) + 1:04d}"
        try:
            artifact = str(artifact_path.relative_to(self.paths.root))
        except ValueError:
            artifact = str(artifact_path)
        record = EvidenceRecord(
            id=evidence_id,
            type=evidence_type,
            source=source,
            summary=summary[:2_000],
            artifact=artifact,
            collected_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        self.records.append(record)
        return evidence_id

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    @property
    def valid_ids(self) -> set[str]:
        return {record.id for record in self.records}

    def save_index(self) -> None:
        payload = {
            "records": [record.model_dump(mode="json") for record in self.records],
            "warnings": self.warnings,
        }
        write_json(self.paths.evidence / "index.json", payload)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

