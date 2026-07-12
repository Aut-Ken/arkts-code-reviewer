from __future__ import annotations

from arkts_code_reviewer.code_analysis.models import CodeFacts
from arkts_code_reviewer.feature_routing.config import load_feature_config
from arkts_code_reviewer.feature_routing.matcher import (
    active_dimension_ids,
    active_tag_ids,
)

_FEATURE_CONFIG = load_feature_config()


def derive_tags(facts: CodeFacts) -> set[str]:
    return active_tag_ids(facts, _FEATURE_CONFIG)


def trigger_dimensions(tags: set[str]) -> list[str]:
    return active_dimension_ids(tags, _FEATURE_CONFIG)
