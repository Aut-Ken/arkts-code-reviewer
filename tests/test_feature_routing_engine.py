from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import (
    ArktsTreeSitterParser,
)
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    CodeSourceRef,
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import ArktsFileAnalysisParser
from arkts_code_reviewer.code_analysis.models import CodeFacts, FileHunk
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.unit_facts import project
from arkts_code_reviewer.feature_routing import DEFAULT_DIMENSIONS_PATH, DEFAULT_TAGS_PATH
from arkts_code_reviewer.feature_routing.config import load_feature_config
from arkts_code_reviewer.feature_routing.engine import FeatureRouter
from arkts_code_reviewer.feature_routing.matcher import match_signal_pairs
from arkts_code_reviewer.feature_routing.models import (
    FeatureRoutingResult,
    ReviewQuestionBinding,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_NODE_MODULE = (
    REPO_ROOT
    / "sidecars"
    / "arkts-parser"
    / "node_modules"
    / "tree-sitter-arkts"
    / "package.json"
)
IMPORT_IDENTITY = "@ohos.net.connection#default"
TYPE_IMPORT_IDENTITY = "@kit.CoreFileKit#ReadOptions"
UNUSED_IMPORT_IDENTITY = "@ohos.net.socket#default"


def _source_ref(suffix: str) -> str:
    return f"code-source:sha256:{suffix * 64}"


def _scope(
    unit_id: str,
    *,
    source_suffix: str = "a",
    exact: ScopedFacts | None = None,
    hints: ScopedFacts | None = None,
    diagnostics: tuple[str, ...] = (),
) -> UnitFactScope:
    exact_facts = ScopedFacts() if exact is None else exact
    return UnitFactScope(
        unit_id=unit_id,
        source_ref_id=_source_ref(source_suffix),
        unit_exact=exact_facts,
        file_hints=exact_facts if hints is None else hints,
        diagnostics=diagnostics,  # type: ignore[arg-type]
    )


def _import_use_router(
    tmp_path: Path,
    *,
    status: str = "Active",
    identities: tuple[str, ...] = (IMPORT_IDENTITY,),
) -> FeatureRouter:
    assert identities == tuple(sorted(set(identities)))
    rendered_identities = ", ".join(f"'{identity}'" for identity in identities)
    tags_path = tmp_path / "tags.yaml"
    dimensions_path = tmp_path / "dimensions.yaml"
    tags_source = DEFAULT_TAGS_PATH.read_text(encoding="utf-8").replace(
        "schema_version: tag-config-v1\n",
        "schema_version: tag-config-v2\n",
        1,
    )
    tags_path.write_text(
        tags_source
        + "\n"
        + "  - id: has_import_domain\n"
        + f"    status: {status}\n"
        + "    description: Test-only canonical import-use domain\n"
        + "    triggers:\n"
        + f"      any_import_use: [{rendered_identities}]\n",
        encoding="utf-8",
    )
    dimensions_path.write_text(
        DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return FeatureRouter(load_feature_config(tags_path, dimensions_path))


def test_default_config_is_versioned_and_cross_referenced() -> None:
    config = load_feature_config()

    assert config.tag_config.version == "tags-v1"
    assert config.dimension_config.version == "dimensions-v1"
    assert config.fingerprint.startswith("feature-config:sha256:")
    assert len(config.tags_by_id) == 24
    assert list(config.dimensions_by_id) == [f"DIM-{index:02d}" for index in range(1, 13)]
    assert "RQ-correctness" in config.review_questions_by_id


def test_router_fixes_timer_cleanup_and_rejects_unrelated_on_api() -> None:
    result = FeatureRouter().route(
        [
            _scope(
                "unit:cleanup",
                exact=ScopedFacts(apis=("clearInterval",)),
                hints=ScopedFacts(apis=("SDK.on", "clearInterval")),
            ),
            _scope(
                "unit:sdk",
                exact=ScopedFacts(apis=("SDK.on",)),
                hints=ScopedFacts(apis=("SDK.on", "clearInterval")),
            ),
        ]
    )

    cleanup, sdk = result.units
    assert cleanup.unit_id == "unit:cleanup"
    assert cleanup.exact_tags == ("has_timer",)
    assert cleanup.routing_tags == ("has_timer",)
    assert "DIM-06" in cleanup.dimensions
    assert "DIM-06" in cleanup.retrieval_dimensions
    assert "RQ-resource" in cleanup.review_question_ids
    assert sdk.exact_tags == ()
    assert sdk.routing_tags == ("has_timer",)
    assert "DIM-06" not in sdk.dimensions
    assert "DIM-06" not in sdk.retrieval_dimensions
    assert "DIM-06" in sdk.routing_dimensions
    assert sdk.review_question_ids == ("RQ-correctness",)


def test_lifecycle_attributes_do_not_fabricate_interaction() -> None:
    result = FeatureRouter().route(
        [
            _scope(
                "unit:click",
                exact=ScopedFacts(attributes=("onClick",)),
            ),
            _scope(
                "unit:lifecycle",
                exact=ScopedFacts(
                    components=("Image",),
                    attributes=("onAppear", "onError"),
                ),
            ),
        ]
    )

    click, lifecycle = result.units
    assert click.exact_tags == ("has_interactive_component",)
    assert "DIM-08" in click.dimensions
    assert lifecycle.exact_tags == ("has_image",)
    assert "DIM-08" not in lifecycle.dimensions


def test_exact_and_file_hint_routes_remain_physically_separate() -> None:
    result = FeatureRouter().route(
        [
            _scope(
                "unit:navigation",
                source_suffix="b",
                exact=ScopedFacts(apis=("router.pushUrl",)),
                hints=ScopedFacts(apis=("http.request", "router.pushUrl")),
            )
        ]
    )
    profile = result.units[0]

    assert profile.exact_tags == ("has_navigation",)
    assert profile.routing_tags == ("has_navigation", "has_network")
    assert "DIM-11" not in profile.dimensions
    assert "DIM-11" not in profile.retrieval_dimensions
    assert "DIM-11" in profile.routing_dimensions
    security = next(
        route for route in profile.dimension_routes if route.dimension_id == "DIM-11"
    )
    assert security.signal_scope == "file_hint"
    assert security.matched_exact_tags == ()
    assert security.matched_routing_tags == ("has_network",)
    assert result.mr_dimensions == tuple(
        sorted({*profile.dimensions, *profile.routing_dimensions})
    )


def test_same_source_units_keep_distinct_exact_facts_and_shared_file_hints() -> None:
    shared_hints = ScopedFacts(
        components=("Image",),
        apis=("http.request", "setInterval"),
    )
    result = FeatureRouter().route(
        [
            _scope(
                "unit:image",
                exact=ScopedFacts(components=("Image",)),
                hints=shared_hints,
            ),
            _scope(
                "unit:timer",
                exact=ScopedFacts(apis=("setInterval",)),
                hints=shared_hints,
            ),
        ]
    )
    profiles = {profile.unit_id: profile for profile in result.units}

    assert profiles["unit:image"].exact_tags == ("has_image",)
    assert profiles["unit:timer"].exact_tags == ("has_timer",)
    assert profiles["unit:image"].routing_tags == (
        "has_image",
        "has_network",
        "has_timer",
    )
    assert profiles["unit:image"].routing_tags == profiles["unit:timer"].routing_tags
    assert all(
        "RQ-network" not in profile.review_question_ids
        for profile in profiles.values()
    )


def test_distinct_sources_never_share_file_hints() -> None:
    result = FeatureRouter().route(
        [
            _scope(
                "unit:network",
                exact=ScopedFacts(apis=("http.request",)),
                hints=ScopedFacts(apis=("http.request",)),
            ),
            _scope(
                "unit:image",
                source_suffix="b",
                exact=ScopedFacts(components=("Image",)),
                hints=ScopedFacts(components=("Image",)),
            ),
        ]
    )
    profiles = {profile.unit_id: profile for profile in result.units}

    assert profiles["unit:network"].routing_tags == ("has_network",)
    assert profiles["unit:image"].routing_tags == ("has_image",)
    assert "has_image" not in profiles["unit:network"].routing_tags
    assert "has_network" not in profiles["unit:image"].routing_tags


def test_resource_reference_has_an_explainable_activation_trace() -> None:
    result = FeatureRouter().route(
        [
            _scope(
                "unit:resource",
                exact=ScopedFacts(resource_references=("app.string.title",)),
            )
        ]
    )
    profile = result.units[0]
    match = next(match for match in profile.tag_matches if match.tag_id == "has_resource_ref")

    assert profile.exact_tags == ("has_resource_ref",)
    assert [signal.to_dict() for signal in match.signals] == [
        {"kind": "resource_references", "value": "app.string.title"}
    ]
    assert "DIM-10" in profile.dimensions
    assert "RQ-internationalization" in profile.review_question_ids


def test_import_use_is_exact_only_and_has_an_explainable_trace(tmp_path: Path) -> None:
    router = _import_use_router(tmp_path)

    profile = router.route(
        [
            _scope(
                "unit:import-use",
                exact=ScopedFacts(import_uses=(IMPORT_IDENTITY,)),
                hints=ScopedFacts(import_uses=(IMPORT_IDENTITY,)),
            )
        ]
    ).units[0]

    assert profile.exact_tags == ("has_import_domain",)
    assert profile.routing_tags == ()
    assert profile.shadow_routing_tags == ()
    match = next(match for match in profile.tag_matches if match.tag_id == "has_import_domain")
    assert match.scope == "unit_exact"
    assert [signal.to_dict() for signal in match.signals] == [
        {"kind": "import_uses", "value": IMPORT_IDENTITY}
    ]


def test_import_use_requires_full_identity_and_draft_stays_shadow(
    tmp_path: Path,
) -> None:
    router = _import_use_router(tmp_path, status="Draft")
    definition = router.config.tags_by_id["has_import_domain"]

    assert match_signal_pairs(
        definition,
        ScopedFacts(import_uses=(IMPORT_IDENTITY,)),
    ) == ()
    assert match_signal_pairs(
        definition,
        ScopedFacts(import_uses=("connectionAlias",)),
        include_owner_aware_import_uses=True,
    ) == ()
    assert match_signal_pairs(
        definition,
        ScopedFacts(import_uses=(IMPORT_IDENTITY + ".extra",)),
        include_owner_aware_import_uses=True,
    ) == ()
    assert match_signal_pairs(
        definition,
        ScopedFacts(import_uses=(IMPORT_IDENTITY,)),
        include_owner_aware_import_uses=True,
    ) == (("import_uses", IMPORT_IDENTITY),)
    assert match_signal_pairs(
        definition,
        CodeFacts(path="src/legacy.ets"),
        include_owner_aware_import_uses=True,
    ) == ()

    profile = router.route(
        [_scope("unit:draft-import", exact=ScopedFacts(import_uses=(IMPORT_IDENTITY,)))]
    ).units[0]
    assert profile.exact_tags == ()
    assert profile.shadow_exact_tags == ("has_import_domain",)
    assert profile.shadow_routing_tags == ()
    assert "has_import_domain" not in profile.review_question_ids

    binding_only = router.route(
        [
            _scope(
                "unit:binding-only",
                exact=ScopedFacts(import_bindings=(IMPORT_IDENTITY,)),
            )
        ]
    ).units[0]
    assert binding_only.exact_tags == ()
    assert binding_only.shadow_exact_tags == ()


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
def test_parser_import_use_routes_only_the_owning_review_unit(tmp_path: Path) -> None:
    source = """import connectionAlias from '@ohos.net.connection'
import { ReadOptions as FileReadOptions } from '@kit.CoreFileKit'
import unusedSocket from '@ohos.net.socket'
function good() {
  connectionAlias.getAllNets()
}
function shadowed(connectionAlias: object) {
  connectionAlias.getAllNets()
}
function typeOnly(value: FileReadOptions) {}
"""
    source_ref = CodeSourceRef.inline("src/pages/Network.ets", source)
    parsed = ArktsFileAnalysisParser(ArktsTreeSitterParser()).parse_file(
        source_ref,
        source,
    )
    scopes = []
    assert not any(
        occurrence.kind == "import_use"
        and occurrence.canonical_name == UNUSED_IMPORT_IDENTITY
        for occurrence in parsed.analysis.fact_occurrences
    )
    for changed_line in (3, 5, 8, 10):
        built = ReviewUnitBuilder().build_file_result(
            source_ref.path,
            source,
            parsed.compatibility_facts,
            "diff",
            [FileHunk(new_start=changed_line, new_lines=1)],
            source_ref_id=source_ref.source_ref_id,
        )
        assert len(built.units) == 1
        scopes.append(project(parsed.analysis, built.units[0]))

    assert scopes[0].unit_exact.import_uses == ()
    assert scopes[0].unit_exact.import_bindings == ()
    assert scopes[1].unit_exact.import_uses == (IMPORT_IDENTITY,)
    assert scopes[2].unit_exact.import_uses == ()
    assert scopes[3].unit_exact.import_uses == (TYPE_IMPORT_IDENTITY,)

    profiles = {
        profile.unit_id: profile
        for profile in _import_use_router(
            tmp_path,
            identities=tuple(
                sorted(
                    (
                        IMPORT_IDENTITY,
                        TYPE_IMPORT_IDENTITY,
                        UNUSED_IMPORT_IDENTITY,
                    )
                )
            ),
        ).route(scopes).units
    }
    import_only, good, shadowed, type_only = (
        profiles[scope.unit_id] for scope in scopes
    )
    assert import_only.exact_tags == ()
    assert good.exact_tags == ("has_import_domain",)
    assert shadowed.exact_tags == ()
    assert type_only.exact_tags == ("has_import_domain",)


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
def test_named_and_namespace_aliases_keep_canonical_import_identity(
    tmp_path: Path,
) -> None:
    named_identity = "@kit.CoreFileKit#fileIo"
    namespace_identity = "./helpers#*"
    source = """import * as helpers from './helpers'
import { fileIo as fs } from '@kit.CoreFileKit'
function namespaceUse() { helpers.run() }
function namedUse() { fs.openSync('x') }
"""
    source_ref = CodeSourceRef.inline("src/pages/NetworkAliases.ets", source)
    parsed = ArktsFileAnalysisParser(ArktsTreeSitterParser()).parse_file(
        source_ref,
        source,
    )
    scopes = []
    for changed_line in (3, 4):
        built = ReviewUnitBuilder().build_file_result(
            source_ref.path,
            source,
            parsed.compatibility_facts,
            "diff",
            [FileHunk(new_start=changed_line, new_lines=1)],
            source_ref_id=source_ref.source_ref_id,
        )
        assert len(built.units) == 1
        scopes.append(project(parsed.analysis, built.units[0]))

    assert scopes[0].unit_exact.import_uses == (namespace_identity,)
    assert scopes[1].unit_exact.import_uses == (named_identity,)

    identities = tuple(sorted((named_identity, namespace_identity)))
    result = _import_use_router(tmp_path, identities=identities).route(scopes)
    profiles = {profile.unit_id: profile for profile in result.units}
    namespace_profile = profiles[scopes[0].unit_id]
    named_profile = profiles[scopes[1].unit_id]
    assert "has_import_domain" in namespace_profile.exact_tags
    assert "has_file_io" not in namespace_profile.exact_tags
    assert "has_import_domain" in named_profile.exact_tags
    assert "has_file_io" in named_profile.exact_tags


def test_router_output_is_deterministic_across_input_order() -> None:
    alpha = _scope("unit:alpha", exact=ScopedFacts(apis=("setTimeout",)))
    zeta = _scope("unit:zeta", source_suffix="b", exact=ScopedFacts(apis=("http.request",)))
    router = FeatureRouter()

    forward = router.route([alpha, zeta])
    reverse = router.route([zeta, alpha])

    assert forward == reverse
    assert forward.to_dict() == reverse.to_dict()
    assert [profile.unit_id for profile in forward.units] == ["unit:alpha", "unit:zeta"]


def test_scope_diagnostics_propagate_without_promoting_file_hints() -> None:
    result = FeatureRouter().route(
        [
            _scope(
                "unit:fallback",
                exact=ScopedFacts(),
                hints=ScopedFacts(apis=("http.request",)),
                diagnostics=("unit_owner_unresolved",),
            )
        ]
    )
    profile = result.units[0]

    assert profile.exact_tags == ()
    assert profile.routing_tags == ("has_network",)
    assert profile.diagnostics == ("unit_owner_unresolved",)
    assert profile.review_question_ids == ("RQ-correctness",)


def test_profile_and_result_models_reject_internally_inconsistent_forgery() -> None:
    result = FeatureRouter().route(
        [_scope("unit:timer", exact=ScopedFacts(apis=("setInterval",)))]
    )
    profile = result.units[0]

    with pytest.raises(ValueError, match="activation trace"):
        replace(profile, exact_tags=("fabricated_tag",))
    with pytest.raises(ValueError, match="mr_dimensions"):
        replace(result, mr_dimensions=("DIM-99",))
    with pytest.raises(ValueError, match="question_bindings"):
        FeatureRoutingResult.create(
            feature_config_version=result.feature_config_version,
            tags_config_version=result.tags_config_version,
            dimensions_config_version=result.dimensions_config_version,
            units=result.units,
            mr_dimensions=result.mr_dimensions,
            question_bindings=(
                ReviewQuestionBinding(profile.unit_id, "RQ-forged"),
            ),
        )


def test_router_rejects_duplicate_unit_id() -> None:
    scope = _scope("unit:duplicate")
    with pytest.raises(ValueError, match="duplicate unit_id"):
        FeatureRouter().route([scope, scope])


def test_empty_route_is_a_versioned_result_and_invalid_containers_fail_closed() -> None:
    result = FeatureRouter().route([])

    assert result.units == ()
    assert result.mr_dimensions == ()
    assert result.question_bindings == ()
    assert result.feature_config_version.startswith("feature-config:sha256:")
    with pytest.raises(ValueError, match="must be a sequence"):
        FeatureRouter().route(iter(()))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must use FeatureConfig"):
        FeatureRouter(object())  # type: ignore[arg-type]


def test_result_replay_rejects_a_self_consistent_result_from_other_facts() -> None:
    timer = _scope("unit:replay", exact=ScopedFacts(apis=("setInterval",)))
    image = _scope("unit:replay", exact=ScopedFacts(components=("Image",)))
    result = FeatureRouter().route([image])

    with pytest.raises(ValueError, match="does not replay"):
        result.validate_replay([timer])


def test_feature_router_imports_before_code_analysis_in_a_fresh_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from arkts_code_reviewer.feature_routing.engine import FeatureRouter; "
                "from arkts_code_reviewer.code_analysis.analyzer import CodeAnalyzer; "
                "assert FeatureRouter and CodeAnalyzer"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("units", (object(),), "invalid profiles"),
        ("question_bindings", (object(),), "invalid type"),
    ],
)
def test_result_rejects_malformed_graph_elements(
    field: str,
    value: tuple[object, ...],
    message: str,
) -> None:
    result = FeatureRouter().route([_scope("unit:strict")])

    with pytest.raises(ValueError, match=message):
        replace(result, **{field: value})


def test_profile_rejects_malformed_graph_elements() -> None:
    profile = FeatureRouter().route([_scope("unit:strict")]).units[0]

    with pytest.raises(ValueError, match="TagMatch values"):
        replace(profile, tag_matches=(object(),))
    with pytest.raises(ValueError, match="DimensionRoute values"):
        replace(profile, dimension_routes=(object(),))


def test_profile_and_result_identities_and_diagnostics_fail_closed() -> None:
    result = FeatureRouter().route([_scope("unit:identity")])
    profile = result.units[0]

    with pytest.raises(ValueError, match="profile_id"):
        replace(profile, profile_id="feature-profile:sha256:" + ("0" * 64))
    with pytest.raises(ValueError, match="identity"):
        replace(
            result,
            feature_routing_id="feature-routing:sha256:" + ("0" * 64),
        )
    with pytest.raises(ValueError, match="unknown codes"):
        replace(result, diagnostics=("forged_result_diagnostic",))
