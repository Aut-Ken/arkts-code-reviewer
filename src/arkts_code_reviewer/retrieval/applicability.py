from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from arkts_code_reviewer.knowledge.models import Applicability
from arkts_code_reviewer.retrieval.models import TargetPlatform

ApplicabilityDecision = Literal["applicable", "unknown", "excluded"]


@dataclass(frozen=True)
class ApplicabilityEvaluation:
    decision: ApplicabilityDecision
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.decision not in {"applicable", "unknown", "excluded"}:
            raise ValueError("unsupported applicability decision")
        if list(self.reasons) != sorted(set(self.reasons)):
            raise ValueError("applicability reasons must be sorted and unique")


def evaluate_applicability(
    applicability: Applicability,
    target: TargetPlatform,
) -> ApplicabilityEvaluation:
    if not isinstance(applicability, Applicability):
        raise TypeError("applicability must use Applicability")
    if not isinstance(target, TargetPlatform):
        raise TypeError("target must use TargetPlatform")

    excluded: set[str] = set()
    unknown: set[str] = set()
    if applicability.min_api_level is not None:
        if target.api_level is None:
            unknown.add("api_level_unknown")
        elif target.api_level < applicability.min_api_level:
            excluded.add("api_level_below_minimum")
    if applicability.max_api_level is not None:
        if target.api_level is None:
            unknown.add("api_level_unknown")
        elif target.api_level > applicability.max_api_level:
            excluded.add("api_level_above_maximum")
    if applicability.releases:
        if target.release is None:
            unknown.add("release_unknown")
        elif target.release not in applicability.releases:
            excluded.add("release_not_applicable")
    if applicability.language_modes:
        if target.language_mode is None:
            unknown.add("language_mode_unknown")
        elif target.language_mode not in applicability.language_modes:
            excluded.add("language_mode_not_applicable")
    for name, required, available in (
        ("permission", applicability.permissions, target.permissions),
        (
            "system_capability",
            applicability.system_capabilities,
            target.system_capabilities,
        ),
    ):
        if not required:
            continue
        if available is None:
            unknown.add(f"{name}_set_unknown")
        elif not set(required).issubset(available):
            excluded.add(f"{name}_missing")

    if excluded:
        return ApplicabilityEvaluation("excluded", tuple(sorted(excluded)))
    if unknown:
        return ApplicabilityEvaluation("unknown", tuple(sorted(unknown)))
    return ApplicabilityEvaluation("applicable", ())


__all__ = [
    "ApplicabilityDecision",
    "ApplicabilityEvaluation",
    "evaluate_applicability",
]
