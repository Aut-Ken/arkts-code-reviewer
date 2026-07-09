from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SampleEntry:
    category: str
    path: str

    @property
    def sample_id(self) -> str:
        return f"{self.category}/{Path(self.path).name}"


def load_manifest(manifest_path: Path) -> list[SampleEntry]:
    data: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [
        SampleEntry(category=item["category"], path=item["path"].replace("\\", "/"))
        for item in data["samples"]
    ]


def select_samples(
    samples: list[SampleEntry],
    *,
    category: str | None = None,
    sample_id: str | None = None,
    limit: int | None = None,
) -> list[SampleEntry]:
    selected = samples
    if category:
        selected = [sample for sample in selected if sample.category == category]
    if sample_id:
        selected = [
            sample
            for sample in selected
            if sample.sample_id == sample_id or sample.path == sample_id or sample_id in sample.path
        ]
    if limit is not None:
        selected = selected[:limit]
    return selected

