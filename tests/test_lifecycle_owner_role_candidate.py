from __future__ import annotations

import os
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
    DeclarationOccurrence,
    ExactRange,
    FactOccurrence,
    FileAnalysis,
    OwnerRef,
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
)
from arkts_code_reviewer.code_analysis.models import FileHunk, SourceSpan
from arkts_code_reviewer.code_analysis.review_unit_contract import (
    declaration_unit_id,
)
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
from arkts_code_reviewer.code_analysis.unit_facts import project
from arkts_code_reviewer.feature_routing.config import (
    DEFAULT_DIMENSIONS_PATH,
    FeatureConfig,
    load_default_feature_config,
    load_feature_config,
)
from arkts_code_reviewer.feature_routing.engine import (
    FeatureRouter,
    derive_active_tags,
)
from arkts_code_reviewer.feature_routing.matcher import active_tag_ids
from arkts_code_reviewer.feature_routing.models import (
    FEATURE_ROUTING_SCHEMA_VERSION,
    FEATURE_ROUTING_V2_SCHEMA_VERSION,
    FEATURE_ROUTING_V3_SCHEMA_VERSION,
    FeatureRoutingResult,
    FileSymbolLeafFeatureSignal,
    SignalScope,
    UnitFeatureProfile,
    UnitSymbolLeafOwnerRoleFeatureSignal,
)
from arkts_code_reviewer.feature_routing.owner_context import (
    OwnerAwareRoutingInput,
    derive_unit_owner_context,
)

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_TAGS = (
    ROOT / "tests/fixtures/feature_routing/tag_config_lifecycle_owner_role_shadow_v1.yaml"
)
V3_TAGS = ROOT / "tests/fixtures/feature_routing/tag_config_lifecycle_symbol_leaf_shadow_v1.yaml"
V2_TAGS = ROOT / "tests/fixtures/feature_routing/tag_config_relational_store_api_shadow_v1.yaml"
SIDECAR_NODE_MODULE = ROOT / "sidecars/arkts-parser/node_modules/tree-sitter-arkts/package.json"
EXPECTED_DEFAULT_FINGERPRINT = (
    "feature-config:sha256:bb241e9bdc54a9e6418e6be03a04593b8cf854838aec4d8644faa624eff7ae9c"
)
EXPECTED_CANDIDATE_FINGERPRINT = (
    "feature-config:sha256:844418e3d7938c816fd3b64b62cdae3d1753d286d50a6a103406838ed6db01e7"
)


def _candidate() -> FeatureConfig:
    return load_feature_config(CANDIDATE_TAGS, DEFAULT_DIMENSIONS_PATH)


def _scope(symbols: tuple[str, ...] = ()) -> UnitFactScope:
    facts = ScopedFacts(symbols=symbols)
    return UnitFactScope(
        unit_id="unit:owner-input-contract",
        source_ref_id=f"code-source:sha256:{'a' * 64}",
        unit_exact=facts,
        file_hints=facts,
        diagnostics=(),
    )


def _owner_inputs(
    source: str,
    *,
    path: str,
    changed_lines: tuple[int, ...] | None = None,
) -> tuple[FileAnalysis, tuple[OwnerAwareRoutingInput, ...]]:
    source_ref = CodeSourceRef.inline(path, source)
    parsed = ArktsFileAnalysisParser(ArktsTreeSitterParser()).parse_file(
        source_ref,
        source,
    )
    builder = ReviewUnitBuilder()
    units = []
    if changed_lines is None:
        built = builder.build_file_result(
            source_ref.path,
            source,
            parsed.compatibility_facts,
            "full",
            [],
            source_ref_id=source_ref.source_ref_id,
        )
        units.extend(built.units)
    else:
        for changed_line in changed_lines:
            built = builder.build_file_result(
                source_ref.path,
                source,
                parsed.compatibility_facts,
                "diff",
                [FileHunk(new_start=changed_line, new_lines=1)],
                source_ref_id=source_ref.source_ref_id,
            )
            assert len(built.units) == 1
            units.extend(built.units)
    assert units
    return parsed.analysis, tuple(
        OwnerAwareRoutingInput(
            scope=project(parsed.analysis, unit),
            unit=unit,
            file_analysis=parsed.analysis,
        )
        for unit in units
    )


def _profiles_by_symbol(
    result: FeatureRoutingResult,
    inputs: tuple[OwnerAwareRoutingInput, ...],
) -> dict[str, UnitFeatureProfile]:
    units = {item.scope.unit_id: item.unit.unit_symbol for item in inputs}
    return {units[profile.unit_id]: profile for profile in result.units}


def _lifecycle_signals(
    profile: UnitFeatureProfile,
    scope: SignalScope,
) -> list[dict[str, object]]:
    for match in profile.tag_matches:
        if match.tag_id == "has_lifecycle" and match.scope == scope:
            return [signal.to_dict() for signal in match.signals]
    return []


@pytest.mark.parametrize(
    "modules",
    [
        (
            "arkts_code_reviewer.feature_routing.config",
            "arkts_code_reviewer.feature_routing.owner_context",
            "arkts_code_reviewer.feature_routing.models",
            "arkts_code_reviewer.feature_routing.engine",
        ),
        (
            "arkts_code_reviewer.feature_routing.engine",
            "arkts_code_reviewer.feature_routing.models",
            "arkts_code_reviewer.feature_routing.owner_context",
            "arkts_code_reviewer.feature_routing.config",
        ),
    ],
)
def test_owner_routing_modules_import_without_cycles(modules: tuple[str, ...]) -> None:
    script = "\n".join(f"import {module}" for module in modules)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_v4_config_is_frozen_and_maps_lifecycle_roles_explicitly() -> None:
    base = load_default_feature_config()
    candidate = _candidate()
    lifecycle = candidate.tags_by_id["has_lifecycle"].triggers

    assert base.fingerprint == EXPECTED_DEFAULT_FINGERPRINT
    assert candidate.tag_config.schema_version == "tag-config-v4"
    assert candidate.tag_config.version == "tags-lifecycle-owner-role-shadow-v1"
    assert candidate.fingerprint == EXPECTED_CANDIDATE_FINGERPRINT
    assert candidate.dimension_config == base.dimension_config
    assert [
        tag_id
        for tag_id in base.tags_by_id
        if base.tags_by_id[tag_id] != candidate.tags_by_id[tag_id]
    ] == ["has_lifecycle"]
    assert [
        (item.symbol_leaf, item.owner_role)
        for item in lifecycle.any_unit_symbol_leaf_with_owner_role
    ] == [
        ("aboutToAppear", "arkui_custom_component"),
        ("aboutToDisappear", "arkui_custom_component"),
        ("onBackPress", "arkui_router_page"),
        ("onPageHide", "arkui_router_page"),
        ("onPageShow", "arkui_router_page"),
    ]
    assert lifecycle.any_file_symbol_leaf == (
        "aboutToAppear",
        "aboutToDisappear",
        "onBackPress",
        "onPageHide",
        "onPageShow",
        "onReady",
    )
    assert lifecycle.any_symbol == ()
    assert lifecycle.any_symbol_leaf == ()


def test_v4_public_routes_require_owner_inputs_but_v1_to_v3_stay_compatible() -> None:
    candidate = _candidate()
    scope = _scope(("Host.aboutToAppear",))

    with pytest.raises(ValueError, match="route_owner_aware_shadow"):
        FeatureRouter(candidate).route([scope])
    with pytest.raises(ValueError, match="route_owner_aware_shadow"):
        active_tag_ids(scope.unit_exact, candidate)
    with pytest.raises(ValueError, match="route_owner_aware_shadow"):
        derive_active_tags(scope.unit_exact, candidate)

    legacy_configs = (
        (load_default_feature_config(), FEATURE_ROUTING_SCHEMA_VERSION),
        (
            load_feature_config(V2_TAGS, DEFAULT_DIMENSIONS_PATH),
            FEATURE_ROUTING_SCHEMA_VERSION,
        ),
        (
            load_feature_config(V3_TAGS, DEFAULT_DIMENSIONS_PATH),
            FEATURE_ROUTING_V2_SCHEMA_VERSION,
        ),
    )
    for config, expected_schema in legacy_configs:
        assert FeatureRouter(config).route([scope]).schema_version == expected_schema
        assert isinstance(active_tag_ids(scope.unit_exact, config), set)


def test_v3_normalized_signal_models_reject_non_symbol_kinds() -> None:
    with pytest.raises(ValueError, match="requires kind=symbols"):
        FileSymbolLeafFeatureSignal(
            kind="apis",  # type: ignore[arg-type]
            value="Host.aboutToAppear",
            operator="any_file_symbol_leaf",
            normalized_value="aboutToAppear",
        )
    with pytest.raises(ValueError, match="requires kind=symbols"):
        UnitSymbolLeafOwnerRoleFeatureSignal(
            kind="apis",  # type: ignore[arg-type]
            value="Host.aboutToAppear",
            operator="any_unit_symbol_leaf_with_owner_role",
            normalized_value="aboutToAppear",
            owner_role="arkui_custom_component",
            symbol_occurrence_id=f"occurrence:{'a' * 64}",
            direct_owner_declaration_id=f"declaration:{'b' * 64}",
            enclosing_owner_declaration_id=f"declaration:{'c' * 64}",
            role_evidence_occurrence_ids=(f"occurrence:{'d' * 64}",),
        )


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
def test_owner_aware_e2e_routes_only_approved_lifecycle_owner_pairs() -> None:
    source = """@Component
struct ComponentOnly {
  aboutToAppear(): void {}
  onPageShow(): void {}
  onReady(): void {}
  build() {}
}
@Entry
@Component
struct Page {
  aboutToAppear(): void {}
  onPageShow(): void {}
  build() {}
}
class Helper {
  aboutToAppear(): void {}
}
@CustomDialog
struct Dialog {
  aboutToAppear(): void {}
  build() {}
}
"""
    candidate = _candidate()
    router = FeatureRouter(candidate)
    _, full_inputs = _owner_inputs(source, path="src/LifecycleOwners.ets")

    result = router.route_owner_aware_shadow(full_inputs)
    result.validate_owner_aware_replay(full_inputs, candidate)
    profiles = _profiles_by_symbol(result, full_inputs)

    assert result.schema_version == FEATURE_ROUTING_V3_SCHEMA_VERSION
    assert set(profiles) == {"ComponentOnly", "Dialog", "Helper", "Page"}

    component = profiles["ComponentOnly"]
    page = profiles["Page"]
    helper = profiles["Helper"]
    dialog = profiles["Dialog"]
    assert "has_lifecycle" in component.exact_tags
    assert {
        (signal["normalized_value"], signal["owner_role"])
        for signal in _lifecycle_signals(component, "unit_exact")
    } == {("aboutToAppear", "arkui_custom_component")}
    assert "has_lifecycle" in page.exact_tags
    assert {
        (signal["normalized_value"], signal["owner_role"])
        for signal in _lifecycle_signals(page, "unit_exact")
    } == {
        ("aboutToAppear", "arkui_custom_component"),
        ("onPageShow", "arkui_router_page"),
    }
    assert "has_lifecycle" not in helper.exact_tags
    assert "owner_context_role_unresolved" in helper.diagnostics
    assert "has_lifecycle" not in dialog.exact_tags
    assert "owner_context_role_unresolved" in dialog.diagnostics

    for profile in profiles.values():
        assert "has_lifecycle" in profile.routing_tags
        file_signals = _lifecycle_signals(profile, "file_hint")
        assert any(
            signal["operator"] == "any_file_symbol_leaf"
            and signal["normalized_value"] == "onReady"
            and "owner_role" not in signal
            for signal in file_signals
        )

    exact_signal = _lifecycle_signals(page, "unit_exact")[0]
    assert exact_signal["operator"] == "any_unit_symbol_leaf_with_owner_role"
    assert str(exact_signal["symbol_occurrence_id"]).startswith("occurrence:")
    assert str(exact_signal["direct_owner_declaration_id"]).startswith("declaration:")
    assert str(exact_signal["enclosing_owner_declaration_id"]).startswith("declaration:")
    assert (
        exact_signal["direct_owner_declaration_id"]
        != exact_signal["enclosing_owner_declaration_id"]
    )
    assert all(
        str(occurrence_id).startswith("occurrence:")
        for occurrence_id in exact_signal["role_evidence_occurrence_ids"]
    )

    _, method_inputs = _owner_inputs(
        source,
        path="src/LifecycleOwners.ets",
        changed_lines=(3, 4, 5, 11, 12, 16, 20),
    )
    method_result = router.route_owner_aware_shadow(method_inputs)
    method_profiles = _profiles_by_symbol(method_result, method_inputs)
    assert {
        symbol: "has_lifecycle" in profile.exact_tags for symbol, profile in method_profiles.items()
    } == {
        "ComponentOnly.aboutToAppear": True,
        "ComponentOnly.onPageShow": False,
        "ComponentOnly.onReady": False,
        "Dialog.aboutToAppear": False,
        "Helper.aboutToAppear": False,
        "Page.aboutToAppear": True,
        "Page.onPageShow": True,
    }


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
def test_recovered_evidence_abstains_locally_without_suppressing_exact_sibling() -> None:
    source = """@Component
struct RecoveredHost {
  aboutToAppear(): void {}
  aboutToDisappear(): void {}
  build() {}
}
"""
    analysis, full_inputs = _owner_inputs(source, path="src/RecoveredHost.ets")
    recovered_method = next(
        declaration
        for declaration in analysis.declarations
        if declaration.qualified_name == "RecoveredHost.aboutToDisappear"
    )
    recovered_analysis = replace(
        analysis,
        declarations=tuple(
            replace(declaration, quality="recovered")
            if declaration.declaration_id == recovered_method.declaration_id
            else declaration
            for declaration in analysis.declarations
        ),
        fact_occurrences=tuple(
            replace(occurrence, quality="recovered", provenance="recovered")
            if occurrence.kind == "symbol"
            and occurrence.owner_ref == OwnerRef("declaration", recovered_method.declaration_id)
            else occurrence
            for occurrence in analysis.fact_occurrences
        ),
    )
    coarse = full_inputs[0]
    coarse_input = OwnerAwareRoutingInput(
        scope=project(recovered_analysis, coarse.unit),
        unit=coarse.unit,
        file_analysis=recovered_analysis,
    )
    coarse_profile = FeatureRouter(_candidate()).route_owner_aware_shadow([coarse_input]).units[0]

    assert "has_lifecycle" in coarse_profile.exact_tags
    assert "owner_context_recovered" in coarse_profile.diagnostics
    assert {
        signal["normalized_value"] for signal in _lifecycle_signals(coarse_profile, "unit_exact")
    } == {"aboutToAppear"}

    _, method_inputs = _owner_inputs(
        source,
        path="src/RecoveredHost.ets",
        changed_lines=(4,),
    )
    recovered_unit = method_inputs[0].unit
    recovered_input = OwnerAwareRoutingInput(
        scope=project(recovered_analysis, recovered_unit),
        unit=recovered_unit,
        file_analysis=recovered_analysis,
    )
    recovered_profile = (
        FeatureRouter(_candidate()).route_owner_aware_shadow([recovered_input]).units[0]
    )

    assert "has_lifecycle" not in recovered_profile.exact_tags
    assert "has_lifecycle" in recovered_profile.routing_tags
    assert "owner_context_recovered" in recovered_profile.diagnostics


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
def test_review_unit_identity_offsets_must_match_owner_declaration() -> None:
    source = """@Component
struct OffsetHost {
  aboutToAppear(): void {}
  build() {}
}
"""
    analysis, inputs = _owner_inputs(source, path="src/OffsetHost.ets")
    original = inputs[0]
    declaration = next(
        item
        for item in analysis.declarations
        if item.declaration_id == original.unit.owner_ref.ref_id
    )
    bad_start = declaration.exact_range.start_offset_utf16 + 1
    bad_end = declaration.exact_range.end_offset_utf16
    bad_unit_id = declaration_unit_id(
        original.unit.file,
        original.unit.unit_kind,
        original.unit.unit_symbol,
        original.unit.source_span.start_line,
        original.unit.source_span.end_line,
        start_offset_utf16=bad_start,
        end_offset_utf16=bad_end,
    )
    bad_unit = replace(
        original.unit,
        unit_id=bad_unit_id,
        identity_start_offset_utf16=bad_start,
        identity_end_offset_utf16=bad_end,
    )
    bad_input = OwnerAwareRoutingInput(
        scope=replace(original.scope, unit_id=bad_unit_id),
        unit=bad_unit,
        file_analysis=analysis,
    )
    context = derive_unit_owner_context(analysis, bad_unit)
    profile = FeatureRouter(_candidate()).route_owner_aware_shadow([bad_input]).units[0]

    assert context.evidence == ()
    assert context.diagnostics == ("owner_context_unit_unresolved",)
    assert "has_lifecycle" not in profile.exact_tags
    assert "has_lifecycle" in profile.routing_tags


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
@pytest.mark.parametrize(
    ("decorators", "expected_diagnostic"),
    [
        ("@Component\n@ComponentV2", "owner_context_role_ambiguous"),
        ("@CustomDialog\n@Component", "owner_context_role_ambiguous"),
    ],
)
def test_ambiguous_or_custom_dialog_owner_roles_abstain(
    decorators: str,
    expected_diagnostic: str,
) -> None:
    source = f"""{decorators}
struct AmbiguousOwner {{
  aboutToAppear(): void {{}}
  build() {{}}
}}
"""
    _, inputs = _owner_inputs(source, path="src/AmbiguousOwner.ets")
    profile = FeatureRouter(_candidate()).route_owner_aware_shadow(inputs).units[0]

    assert "has_lifecycle" not in profile.exact_tags
    assert "has_lifecycle" in profile.routing_tags
    assert expected_diagnostic in profile.diagnostics


@pytest.mark.skipif(
    not SIDECAR_NODE_MODULE.is_file(),
    reason="ArkTS tree-sitter sidecar dependencies are not installed",
)
def test_nested_class_method_does_not_inherit_component_role_from_outer_struct() -> None:
    source = """@Component
struct Outer {
  build() {}
  // class Nested {
  //   aboutToAppear(): void {}
  // }
}
"""
    analysis, inputs = _owner_inputs(source, path="src/Outer.ets")
    coarse = inputs[0]
    outer = next(
        declaration
        for declaration in analysis.declarations
        if declaration.qualified_name == "Outer"
    )
    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line.encode("utf-16-le")) // 2)

    nested_range = ExactRange(
        start_line=4,
        end_line=6,
        start_offset_utf16=line_starts[3] + 2,
        end_offset_utf16=line_starts[6] - 1,
    )
    nested = DeclarationOccurrence.create(
        source_ref_id=analysis.source_ref.source_ref_id,
        kind="class",
        name="Nested",
        qualified_name="Outer.Nested",
        span=SourceSpan(4, 6),
        exact_range=nested_range,
        parent_id=outer.declaration_id,
    )
    method_range = ExactRange(
        start_line=5,
        end_line=5,
        start_offset_utf16=line_starts[4] + 2,
        end_offset_utf16=line_starts[5] - 1,
    )
    nested_method = DeclarationOccurrence.create(
        source_ref_id=analysis.source_ref.source_ref_id,
        kind="method",
        name="aboutToAppear",
        qualified_name="Outer.Nested.aboutToAppear",
        span=SourceSpan(5, 5),
        exact_range=method_range,
        parent_id=nested.declaration_id,
    )
    token_start = line_starts[4] + source.splitlines()[4].index("aboutToAppear")
    symbol_range = ExactRange(
        start_line=5,
        end_line=5,
        start_offset_utf16=token_start,
        end_offset_utf16=token_start + len("aboutToAppear"),
    )
    nested_symbol = FactOccurrence.create(
        source_ref_id=analysis.source_ref.source_ref_id,
        kind="symbol",
        name="aboutToAppear",
        canonical_name="Outer.Nested.aboutToAppear",
        span=SourceSpan(5, 5),
        exact_range=symbol_range,
        owner_ref=OwnerRef("declaration", nested_method.declaration_id),
    )
    declarations = tuple(
        sorted(
            (*analysis.declarations, nested, nested_method),
            key=lambda item: (
                item.span.start_line,
                item.exact_range.start_offset_utf16,
                item.span.end_line,
                item.exact_range.end_offset_utf16,
                item.kind,
                item.qualified_name,
                item.declaration_id,
            ),
        )
    )
    occurrences = tuple(
        sorted(
            (*analysis.fact_occurrences, nested_symbol),
            key=lambda item: (
                item.span.start_line,
                item.exact_range.start_offset_utf16,
                item.span.end_line,
                item.exact_range.end_offset_utf16,
                item.kind,
                item.canonical_name or item.name,
                item.occurrence_id,
            ),
        )
    )
    forged = FileAnalysis.create(
        source_ref=analysis.source_ref,
        parser_version=analysis.parser_version,
        parser_quality=analysis.parser_quality,
        file_hints=analysis.file_hints,
        declarations=declarations,
        review_regions=analysis.review_regions,
        fact_occurrences=occurrences,
        diagnostics=analysis.diagnostics,
    )
    scope = project(forged, coarse.unit)
    owner_context = derive_unit_owner_context(forged, coarse.unit)
    result = FeatureRouter(_candidate()).route_owner_aware_shadow(
        [
            OwnerAwareRoutingInput(
                scope=scope,
                unit=coarse.unit,
                file_analysis=forged,
            )
        ]
    )

    assert set(scope.unit_exact.symbols) == {
        "Outer",
        "Outer.Nested.aboutToAppear",
        "Outer.build",
    }
    assert not any(
        evidence.symbol == "Outer.Nested.aboutToAppear" for evidence in owner_context.evidence
    )
    assert "has_lifecycle" not in result.units[0].exact_tags
