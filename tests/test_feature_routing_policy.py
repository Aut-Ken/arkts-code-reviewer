from __future__ import annotations

from pathlib import Path

from arkts_code_reviewer.code_analysis.file_analysis_models import (
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.feature_routing.config import load_feature_config
from arkts_code_reviewer.feature_routing.engine import FeatureRouter
from arkts_code_reviewer.feature_routing.models import UnitFeatureProfile

_TAG_BLOCKS = (
    """  - id: has_active
    status: Active
    description: Active exact and routing signal
    triggers:
      any_api: [active.run]""",
    """  - id: has_deprecated
    status: Deprecated
    description: Deprecated signal that must never be emitted
    triggers:
      any_api: [deprecated.run]""",
    """  - id: has_draft
    status: Draft
    description: Draft signal that is observable only through shadow output
    triggers:
      any_api: [draft.run]""",
)

_QUESTION_BLOCKS = (
    """  - id: RQ-active-always
    title: Active always-bound question
    status: Active
    always_bind: true
    triggers:
      any_tag: []""",
    """  - id: RQ-active-conditional
    title: Active conditional question
    status: Active
    always_bind: false
    triggers:
      any_tag: [has_active]""",
    """  - id: RQ-deprecated-always
    title: Deprecated always-bound question
    status: Deprecated
    always_bind: true
    triggers:
      any_tag: []""",
    """  - id: RQ-deprecated-conditional
    title: Deprecated conditional question
    status: Deprecated
    always_bind: false
    triggers:
      any_tag: [has_deprecated]""",
    """  - id: RQ-draft-always
    title: Draft always-bound question
    status: Draft
    always_bind: true
    triggers:
      any_tag: []""",
    """  - id: RQ-draft-from-active
    title: Draft question triggered by an Active tag
    status: Draft
    always_bind: false
    triggers:
      any_tag: [has_active]""",
    """  - id: RQ-draft-from-draft
    title: Draft question triggered by a Draft tag
    status: Draft
    always_bind: false
    triggers:
      any_tag: [has_draft]""",
)

_DIMENSION_BLOCKS = (
    """  - id: DIM-01
    title: Disabled conditional
    status: Active
    always_check: false
    retrieval_policy: disabled
    triggers:
      any_tag: [has_active]""",
    """  - id: DIM-02
    title: Disabled always-check
    status: Active
    always_check: true
    retrieval_policy: disabled
    triggers:
      any_tag: []""",
    """  - id: DIM-03
    title: Signal-required conditional
    status: Active
    always_check: false
    retrieval_policy: signal_required
    triggers:
      any_tag: [has_active]""",
    """  - id: DIM-04
    title: Signal-required always-check
    status: Active
    always_check: true
    retrieval_policy: signal_required
    triggers:
      any_tag: [has_active]""",
    """  - id: DIM-05
    title: Always-retrieved conditional
    status: Active
    always_check: false
    retrieval_policy: always
    triggers:
      any_tag: [has_active]""",
    """  - id: DIM-06
    title: Always-retrieved always-check
    status: Active
    always_check: true
    retrieval_policy: always
    triggers:
      any_tag: []""",
    """  - id: DIM-07
    title: Draft always-check shadow dimension
    status: Draft
    always_check: true
    retrieval_policy: disabled
    triggers:
      any_tag: []""",
    """  - id: DIM-08
    title: Draft conditional from Active tag
    status: Draft
    always_check: false
    retrieval_policy: disabled
    triggers:
      any_tag: [has_active]""",
    """  - id: DIM-09
    title: Draft conditional from Draft tag
    status: Draft
    always_check: false
    retrieval_policy: disabled
    triggers:
      any_tag: [has_draft]""",
    """  - id: DIM-10
    title: Deprecated dimension
    status: Deprecated
    always_check: true
    retrieval_policy: always
    triggers:
      any_tag: [has_deprecated]""",
)


def _write_policy_config(
    root: Path,
    *,
    reverse_definitions: bool = False,
) -> tuple[Path, Path]:
    root.mkdir()
    tags = tuple(reversed(_TAG_BLOCKS)) if reverse_definitions else _TAG_BLOCKS
    questions = (
        tuple(reversed(_QUESTION_BLOCKS))
        if reverse_definitions
        else _QUESTION_BLOCKS
    )
    dimensions = (
        tuple(reversed(_DIMENSION_BLOCKS))
        if reverse_definitions
        else _DIMENSION_BLOCKS
    )
    tags_path = root / "tags.yaml"
    dimensions_path = root / "dimensions.yaml"
    tags_path.write_text(
        "schema_version: tag-config-v1\n"
        "version: policy-tags-v1\n"
        "tags:\n"
        + "\n".join(tags)
        + "\n",
        encoding="utf-8",
    )
    dimensions_path.write_text(
        "schema_version: dimension-config-v1\n"
        "version: policy-dimensions-v1\n"
        "review_questions:\n"
        + "\n".join(questions)
        + "\n"
        "dimensions:\n"
        + "\n".join(dimensions)
        + "\n",
        encoding="utf-8",
    )
    return tags_path, dimensions_path


def _router(tmp_path: Path) -> FeatureRouter:
    tags_path, dimensions_path = _write_policy_config(tmp_path / "policy")
    return FeatureRouter(load_feature_config(tags_path, dimensions_path))


def _scope(
    unit_id: str,
    *,
    exact_apis: tuple[str, ...] = (),
    hint_apis: tuple[str, ...] = (),
    source_suffix: str = "a",
) -> UnitFactScope:
    return UnitFactScope(
        unit_id=unit_id,
        source_ref_id=f"code-source:sha256:{source_suffix * 64}",
        unit_exact=ScopedFacts(apis=exact_apis),
        file_hints=ScopedFacts(apis=hint_apis),
    )


def _route_matrix(
    profile: UnitFeatureProfile,
) -> dict[str, tuple[bool, bool, bool, str]]:
    routes = profile.dimension_routes
    return {
        route.dimension_id: (
            route.review_enabled,
            route.retrieval_enabled,
            route.routing_enabled,
            route.signal_scope,
        )
        for route in routes
    }


def test_tag_statuses_are_fail_closed_into_active_shadow_and_omitted(
    tmp_path: Path,
) -> None:
    profile = _router(tmp_path).route(
        [
            _scope(
                "unit:statuses",
                exact_apis=("active.run", "deprecated.run", "draft.run"),
                hint_apis=("active.run", "deprecated.run", "draft.run"),
            )
        ]
    ).units[0]

    assert profile.exact_tags == ("has_active",)
    assert profile.routing_tags == ("has_active",)
    assert profile.shadow_exact_tags == ("has_draft",)
    assert profile.shadow_routing_tags == ("has_draft",)
    assert tuple(
        (match.tag_id, match.status, match.scope) for match in profile.tag_matches
    ) == (
        ("has_active", "Active", "file_hint"),
        ("has_active", "Active", "unit_exact"),
        ("has_draft", "Draft", "file_hint"),
        ("has_draft", "Draft", "unit_exact"),
    )
    assert "has_deprecated" not in str(profile.to_dict())


def test_draft_dimensions_are_shadow_only_and_require_exact_signals(
    tmp_path: Path,
) -> None:
    result = _router(tmp_path).route(
        [
            _scope("unit:active-exact", exact_apis=("active.run",)),
            _scope(
                "unit:hints-only",
                hint_apis=("active.run", "draft.run"),
                source_suffix="b",
            ),
            _scope(
                "unit:draft-exact",
                exact_apis=("draft.run",),
                source_suffix="c",
            ),
            _scope("unit:no-signal", source_suffix="d"),
        ]
    )
    profiles = {profile.unit_id: profile for profile in result.units}

    assert profiles["unit:active-exact"].shadow_dimensions == ("DIM-07", "DIM-08")
    assert profiles["unit:draft-exact"].shadow_dimensions == ("DIM-07", "DIM-09")
    assert profiles["unit:hints-only"].shadow_dimensions == ("DIM-07",)
    assert profiles["unit:no-signal"].shadow_dimensions == ("DIM-07",)
    assert all(
        not set(profile.shadow_dimensions).intersection(profile.dimensions)
        for profile in result.units
    )
    assert "DIM-10" not in str(result.to_dict())


def test_review_question_statuses_bind_only_to_their_governed_channel(
    tmp_path: Path,
) -> None:
    result = _router(tmp_path).route(
        [
            _scope("unit:active", exact_apis=("active.run",)),
            _scope("unit:draft", exact_apis=("draft.run",), source_suffix="b"),
            _scope(
                "unit:deprecated",
                exact_apis=("deprecated.run",),
                source_suffix="c",
            ),
        ]
    )
    profiles = {profile.unit_id: profile for profile in result.units}

    assert profiles["unit:active"].review_question_ids == (
        "RQ-active-always",
        "RQ-active-conditional",
    )
    assert profiles["unit:active"].shadow_review_question_ids == (
        "RQ-draft-always",
        "RQ-draft-from-active",
    )
    assert profiles["unit:draft"].review_question_ids == ("RQ-active-always",)
    assert profiles["unit:draft"].shadow_review_question_ids == (
        "RQ-draft-always",
        "RQ-draft-from-draft",
    )
    assert profiles["unit:deprecated"].review_question_ids == ("RQ-active-always",)
    assert profiles["unit:deprecated"].shadow_review_question_ids == (
        "RQ-draft-always",
    )
    assert tuple(
        (binding.primary_unit_id, binding.review_question_id)
        for binding in result.question_bindings
    ) == (
        ("unit:active", "RQ-active-always"),
        ("unit:active", "RQ-active-conditional"),
        ("unit:deprecated", "RQ-active-always"),
        ("unit:draft", "RQ-active-always"),
    )
    assert "RQ-deprecated" not in str(result.to_dict())


def test_retrieval_policy_matrix_separates_review_retrieval_and_routing(
    tmp_path: Path,
) -> None:
    result = _router(tmp_path).route(
        [
            _scope("unit:exact", exact_apis=("active.run",)),
            _scope("unit:hint", hint_apis=("active.run",), source_suffix="b"),
            _scope("unit:none", source_suffix="c"),
        ]
    )
    profiles = {profile.unit_id: profile for profile in result.units}

    assert _route_matrix(profiles["unit:exact"]) == {
        "DIM-01": (True, False, False, "unit_exact"),
        "DIM-02": (True, False, False, "none"),
        "DIM-03": (True, True, True, "unit_exact"),
        "DIM-04": (True, True, True, "unit_exact"),
        "DIM-05": (True, True, True, "unit_exact"),
        "DIM-06": (True, True, True, "none"),
    }
    assert _route_matrix(profiles["unit:hint"]) == {
        "DIM-01": (False, False, False, "file_hint"),
        "DIM-02": (True, False, False, "none"),
        "DIM-03": (False, False, True, "file_hint"),
        "DIM-04": (True, False, True, "file_hint"),
        "DIM-05": (False, True, True, "file_hint"),
        "DIM-06": (True, True, True, "none"),
    }
    assert _route_matrix(profiles["unit:none"]) == {
        "DIM-01": (False, False, False, "none"),
        "DIM-02": (True, False, False, "none"),
        "DIM-03": (False, False, False, "none"),
        "DIM-04": (True, False, False, "none"),
        "DIM-05": (False, True, True, "none"),
        "DIM-06": (True, True, True, "none"),
    }

    assert profiles["unit:exact"].dimensions == (
        "DIM-01",
        "DIM-02",
        "DIM-03",
        "DIM-04",
        "DIM-05",
        "DIM-06",
    )
    assert profiles["unit:exact"].retrieval_dimensions == (
        "DIM-03",
        "DIM-04",
        "DIM-05",
        "DIM-06",
    )
    assert profiles["unit:hint"].dimensions == ("DIM-02", "DIM-04", "DIM-06")
    assert profiles["unit:hint"].retrieval_dimensions == ("DIM-05", "DIM-06")
    assert profiles["unit:hint"].routing_dimensions == (
        "DIM-03",
        "DIM-04",
        "DIM-05",
        "DIM-06",
    )


def test_policy_output_is_deterministic_across_definition_and_scope_order(
    tmp_path: Path,
) -> None:
    forward_paths = _write_policy_config(tmp_path / "forward")
    reverse_paths = _write_policy_config(
        tmp_path / "reverse",
        reverse_definitions=True,
    )
    forward_config = load_feature_config(*forward_paths)
    reverse_config = load_feature_config(*reverse_paths)
    alpha = _scope("unit:alpha", exact_apis=("active.run",))
    zeta = _scope("unit:zeta", exact_apis=("draft.run",), source_suffix="b")

    forward = FeatureRouter(forward_config).route([alpha, zeta])
    reverse = FeatureRouter(reverse_config).route([zeta, alpha])

    assert forward_config.fingerprint == reverse_config.fingerprint
    assert forward == reverse
    assert forward.to_dict() == reverse.to_dict()
    assert tuple(profile.unit_id for profile in forward.units) == (
        "unit:alpha",
        "unit:zeta",
    )
