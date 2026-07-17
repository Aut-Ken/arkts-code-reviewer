from __future__ import annotations

import io
from copy import deepcopy
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from arkts_code_reviewer.feature_routing import (
    DEFAULT_DIMENSIONS_PATH,
    DEFAULT_TAGS_PATH,
    load_default_feature_config,
    load_feature_config,
)
from arkts_code_reviewer.hybrid_analysis import (
    DEFAULT_AI_TAG_CONTRACTS_PATH,
    DEFAULT_AI_TAG_PROMPT_PATH,
    AITagContractCatalog,
    FullTaxonomyRequestBuilder,
    active_tag_registry_fingerprint,
    build_ai_tag_model_view,
    build_full_taxonomy_request,
    default_ai_tag_model_policy,
    load_ai_tag_contract_catalog,
    load_ai_tag_prompt,
    load_default_ai_tag_contract_catalog,
    load_default_ai_tag_prompt,
    project_ai_tag_contract_views,
    reduce_unit_comparison,
    seal_ai_tag_contract_catalog,
    seal_ai_tag_execution_outcome,
    seal_ai_tag_model_policy,
    seal_hybrid_feature_analysis_result,
    seal_review_unit_analysis_card,
    validate_catalog_against_feature_config,
    verify_full_taxonomy_request,
    verify_hybrid_chain_with_trusted_request,
)


def _hash_id(prefix: str, marker: str) -> str:
    return f"{prefix}:sha256:{marker * 64}"


def _fact_set() -> dict[str, object]:
    return {
        "apis": [],
        "components": [],
        "decorators": [],
        "attributes": [],
        "symbols": [],
        "syntax": [],
        "calls": [],
        "import_bindings": [],
        "import_uses": [],
        "field_reads": [],
        "field_writes": [],
        "string_literals": [],
        "resource_references": [],
    }


def _card(*, marker: str = "1", feature_fingerprint: str | None = None):  # type: ignore[no-untyped-def]
    feature_config = load_default_feature_config()
    return seal_review_unit_analysis_card(
        {
            "schema_version": "review-unit-analysis-card-v1",
            "unit_id": f"unit:Index.load:{marker}",
            "source_ref_id": _hash_id("code-source", marker),
            "feature_profile_id": _hash_id("feature-profile", "e"),
            "feature_routing_id": _hash_id("feature-routing", "f"),
            "context_plan_id": _hash_id("context-plan", "a"),
            "source_role": "head",
            "unit_kind": "method",
            "unit_symbol": "Index.load",
            "owner_summary": {
                "resolution": "resolved",
                "unit_owner": {
                    "kind": "declaration",
                    "ref_id": _hash_id("declaration", "c"),
                    "owner_kind": "method",
                    "qualified_name": "Index.load",
                    "quality": "exact",
                },
                "enclosing_owner": {
                    "kind": "declaration",
                    "ref_id": _hash_id("declaration", "d"),
                    "owner_kind": "struct",
                    "qualified_name": "Index",
                    "quality": "exact",
                },
                "owner_roles": ["arkui_custom_component"],
                "diagnostics": [],
            },
            "code": {
                "mode": "full_unit",
                "text": "load() {\n  this.refresh();\n}",
                "line_start": 10,
                "line_end": 12,
                "changed_line_numbers": [11],
                "truncated": False,
            },
            "change_atom_ids": [_hash_id("change-atom", "2")],
            "exact_occurrence_ids": [],
            "owner_context_occurrence_ids": [],
            "owner_context_declaration_ids": [],
            "unit_fact_diagnostics": [],
            "facts": {"unit_exact": _fact_set(), "file_hints": _fact_set()},
            "static_tags": {"exact": [], "routing": [], "matches": []},
            "quality": {
                "parser_layer": "L1",
                "error_nodes": 0,
                "missing_nodes": 0,
                "context_degraded": False,
                "unit_owner_unresolved": False,
            },
            "available_context_refs": [],
            "code_token_budget": 2400,
            "feature_config_fingerprint": (
                feature_config.fingerprint
                if feature_fingerprint is None
                else feature_fingerprint
            ),
            "context_policy_fingerprint": _hash_id("analysis-context-policy", "4"),
        }
    )


def _default_builder() -> FullTaxonomyRequestBuilder:
    return FullTaxonomyRequestBuilder.default()


def _catalog_payload(catalog: AITagContractCatalog) -> dict[str, object]:
    return catalog.model_dump(mode="json", exclude={"catalog_fingerprint"})


def _policy_payload() -> dict[str, object]:
    return default_ai_tag_model_policy().model_dump(
        mode="json",
        exclude={"model_policy_fingerprint"},
    )


def _dump_yaml(path: Path, payload: object) -> None:
    output = io.StringIO()
    yaml = YAML()
    yaml.dump(payload, output)
    path.write_text(output.getvalue(), encoding="utf-8")


def test_default_catalog_prompt_and_policy_are_frozen_development_contracts() -> None:
    config = load_default_feature_config()
    catalog = load_default_ai_tag_contract_catalog()
    prompt = load_default_ai_tag_prompt()
    policy = default_ai_tag_model_policy()
    active_tag_ids = tuple(
        tag_id
        for tag_id, definition in config.tags_by_id.items()
        if definition.status == "Active"
    )

    assert catalog.qualification == "development_not_qualified"
    assert catalog.active_tag_count == 24
    assert tuple(item.tag_id for item in catalog.contracts) == active_tag_ids
    assert catalog.source_taxonomy.active_registry_fingerprint == (
        active_tag_registry_fingerprint(config)
    )
    assert all(item.definition for item in catalog.contracts)
    assert all(item.inclusions for item in catalog.contracts)
    assert all(item.exclusions for item in catalog.contracts)
    assert all(item.hard_negatives for item in catalog.contracts)
    assert catalog.catalog_fingerprint == (
        "ai-tag-contract-catalog:sha256:"
        "012a816eb593bccced36860c589beb227fffbe89f3f9405bedf6bab8a3d8fc55"
    )
    assert prompt.prompt_hash == (
        "sha256:20c70d5d6bd90bbaf376205f88a33d87510bbaf2dfb61557de6ddf6e368a40e5"
    )
    assert policy.model_policy_fingerprint == (
        "ai-tag-policy:sha256:"
        "b82e8dc43cff2a92bad3159bbeb5f6ba35e3dbdd6ed878dbdc37cbff0fc801ae"
    )
    assert policy.dispatch_mode == "disabled_no_budget_no_approval"
    assert policy.user_payload_renderer_version == "ai-tag-user-payload-renderer-v1"
    assert policy.wire_output_contract_version == "ai-tag-wire-output-v1"
    assert "direct_unit_semantic_evidence" in prompt.text
    assert "global fail-closed condition" in prompt.text
    assert "strictly increasing" in prompt.text
    assert "DIM-" not in prompt.text
    assert "RQ-" not in prompt.text
    lifecycle = next(
        item for item in catalog.contracts if item.tag_id == "has_lifecycle"
    )
    lifecycle_text = "\n".join(
        (
            lifecycle.definition,
            *lifecycle.inclusions,
            *lifecycle.exclusions,
            *lifecycle.hard_negatives,
        )
    )
    for required_boundary in (
        "@Component",
        "@ComponentV2",
        "@Entry",
        "aboutToAppear",
        "aboutToDisappear",
        "onPageShow",
        "onPageHide",
        "onBackPress",
        "@CustomDialog",
        "onReady",
    ):
        assert required_boundary in lifecycle_text


def test_projection_is_closed_full_24_model_view() -> None:
    config = load_default_feature_config()
    views = project_ai_tag_contract_views(
        load_default_ai_tag_contract_catalog(),
        config,
    )

    assert len(views) == 24
    assert tuple(item.tag_id for item in views) == tuple(sorted(config.tags_by_id))
    expected_keys = {
        "schema_version",
        "tag_id",
        "definition",
        "inclusions",
        "exclusions",
        "hard_negatives",
        "contract_fingerprint",
    }
    assert all(set(item.model_dump(mode="json")) == expected_keys for item in views)
    visible_text = "\n".join(
        str(item.model_dump(mode="json")) for item in views
    )
    assert "any_api" not in visible_text
    assert "trigger" not in visible_text.lower()
    assert "DIM-" not in visible_text
    assert "RQ-" not in visible_text


def test_full_taxonomy_request_builder_is_deterministic_and_rebuild_verified() -> None:
    card = _card()
    model_view = build_ai_tag_model_view(card=card)
    builder = _default_builder()

    request = builder.build(card=card, model_view=model_view)
    rebuilt = build_full_taxonomy_request(card=card, model_view=model_view)

    assert request == rebuilt
    assert request.required_tag_count == 24
    assert request.taxonomy_delivery_mode == "full_single"
    assert len(request.tag_contract_views) == 24
    assert request.prompt_hash == builder.prompt.prompt_hash
    assert request.model_policy_fingerprint == (
        builder.model_policy.model_policy_fingerprint
    )
    assert request.active_taxonomy_fingerprint == (
        "ai-tag-taxonomy:sha256:"
        "9df212b59c076c52fda572e0d54e96e4251273e60d4033298eaf4527ea5ddb0e"
    )
    assert request.request_id == (
        "ai-tag-request:sha256:"
        "e177ec728a08e2652f934b711c7b1cbc97c6706e5ccd3c128d54f8dcb10d5f79"
    )
    verify_full_taxonomy_request(
        request,
        card=card,
        model_view=model_view,
        feature_config=builder.feature_config,
        catalog=builder.catalog,
        prompt=builder.prompt,
        model_policy=builder.model_policy,
    )


def test_trusted_rebuild_rejects_self_consistent_forged_inputs(tmp_path: Path) -> None:
    card = _card()
    model_view = build_ai_tag_model_view(card=card)
    trusted = _default_builder()

    catalog_payload = _catalog_payload(trusted.catalog)
    contracts = deepcopy(catalog_payload["contracts"])
    assert isinstance(contracts, list)
    assert isinstance(contracts[0], dict)
    contracts[0]["definition"] = f"{contracts[0]['definition']} 候选变体。"
    catalog_payload["contracts"] = contracts
    forged_catalog = seal_ai_tag_contract_catalog(catalog_payload)
    forged_catalog_request = FullTaxonomyRequestBuilder(
        feature_config=trusted.feature_config,
        catalog=forged_catalog,
        prompt=trusted.prompt,
        model_policy=trusted.model_policy,
    ).build(card=card, model_view=model_view)

    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text(f"{trusted.prompt.text}\n", encoding="utf-8")
    forged_prompt = load_ai_tag_prompt(prompt_path)
    forged_prompt_request = FullTaxonomyRequestBuilder(
        feature_config=trusted.feature_config,
        catalog=trusted.catalog,
        prompt=forged_prompt,
        model_policy=trusted.model_policy,
    ).build(card=card, model_view=model_view)

    policy_payload = _policy_payload()
    policy_payload["policy_version"] = "deepseek-tag-policy-v1-disabled-variant"
    forged_policy = seal_ai_tag_model_policy(policy_payload)
    forged_policy_request = FullTaxonomyRequestBuilder(
        feature_config=trusted.feature_config,
        catalog=trusted.catalog,
        prompt=trusted.prompt,
        model_policy=forged_policy,
    ).build(card=card, model_view=model_view)

    for forged in (
        forged_catalog_request,
        forged_prompt_request,
        forged_policy_request,
    ):
        with pytest.raises(ValueError, match="trusted-input deterministic rebuild"):
            verify_full_taxonomy_request(
                forged,
                card=card,
                model_view=model_view,
                feature_config=trusted.feature_config,
                catalog=trusted.catalog,
                prompt=trusted.prompt,
                model_policy=trusted.model_policy,
            )


def test_trusted_request_is_mandatory_in_high_level_hybrid_closure() -> None:
    card = _card()
    model_view = build_ai_tag_model_view(card=card)
    trusted = _default_builder()
    request = trusted.build(card=card, model_view=model_view)
    outcome = seal_ai_tag_execution_outcome(
        {
            "schema_version": "ai-tag-execution-outcome-v1",
            "analysis_run_id": _hash_id("ai-tag-run", "8"),
            "card_id": card.card_id,
            "model_view_id": model_view.model_view_id,
            "request_id": request.request_id,
            "status": "skipped_budget",
            "result_id": None,
            "reason_code": "budget_exhausted",
            "attempt_count": 0,
            "budget_snapshot_id": _hash_id("ai-budget-snapshot", "9"),
        }
    )
    states = [
        {
            "tag_id": tag_id,
            "static_exact_decision": "unknown",
            "static_routing_decision": "unknown",
            "ai_unit_decision": None,
            "unit_comparison_status": reduce_unit_comparison("unknown", None),
        }
        for tag_id in trusted.feature_config.tags_by_id
    ]
    hybrid = seal_hybrid_feature_analysis_result(
        {
            "schema_version": "hybrid-feature-analysis-result-v1",
            "unit_id": card.unit_id,
            "card_id": card.card_id,
            "ai_execution_outcome_id": outcome.outcome_id,
            "ai_result_id": None,
            "tag_states": states,
            "diagnostics": [],
        }
    )

    verify_hybrid_chain_with_trusted_request(
        hybrid,
        card=card,
        model_view=model_view,
        request=request,
        outcome=outcome,
        result=None,
        feature_config=trusted.feature_config,
        catalog=trusted.catalog,
        prompt=trusted.prompt,
        model_policy=trusted.model_policy,
    )

    forged_payload = _catalog_payload(trusted.catalog)
    forged_contracts = deepcopy(forged_payload["contracts"])
    assert isinstance(forged_contracts, list)
    assert isinstance(forged_contracts[0], dict)
    forged_contracts[0]["definition"] = "自洽但不受信任的语义合同。"
    forged_payload["contracts"] = forged_contracts
    forged_catalog = seal_ai_tag_contract_catalog(forged_payload)
    forged_request = FullTaxonomyRequestBuilder(
        feature_config=trusted.feature_config,
        catalog=forged_catalog,
        prompt=trusted.prompt,
        model_policy=trusted.model_policy,
    ).build(card=card, model_view=model_view)

    with pytest.raises(ValueError, match="trusted-input deterministic rebuild"):
        verify_hybrid_chain_with_trusted_request(
            hybrid,
            card=card,
            model_view=model_view,
            request=forged_request,
            outcome=outcome,
            result=None,
            feature_config=trusted.feature_config,
            catalog=trusted.catalog,
            prompt=trusted.prompt,
            model_policy=trusted.model_policy,
        )


def test_builder_rejects_card_model_view_and_feature_config_mismatch() -> None:
    card = _card(marker="1")
    other_card = _card(marker="9")
    other_view = build_ai_tag_model_view(card=other_card)

    with pytest.raises(ValueError, match="does not reference the supplied Analysis Card"):
        _default_builder().build(card=card, model_view=other_view)

    drifted_card = _card(
        marker="8",
        feature_fingerprint=_hash_id("feature-config", "8"),
    )
    drifted_view = build_ai_tag_model_view(card=drifted_card)
    with pytest.raises(ValueError, match="feature config differs"):
        _default_builder().build(card=drifted_card, model_view=drifted_view)


@pytest.mark.parametrize("mutation", ["missing", "unknown", "duplicate"])
def test_catalog_rejects_inexact_active_tag_coverage(
    tmp_path: Path,
    mutation: str,
) -> None:
    raw = YAML(typ="safe").load(DEFAULT_AI_TAG_CONTRACTS_PATH.read_text(encoding="utf-8"))
    contracts = raw["contracts"]
    if mutation == "missing":
        contracts.pop()
    elif mutation == "unknown":
        contracts[-1]["tag_id"] = "has_unknown"
    else:
        contracts[-1]["tag_id"] = contracts[-2]["tag_id"]
    path = tmp_path / "catalog.yaml"
    _dump_yaml(path, raw)

    with pytest.raises(ValueError, match="invalid AI Tag contract catalog"):
        load_ai_tag_contract_catalog(path)


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "version: ai-tag-contracts-v1\n",
            "version: ai-tag-contracts-v1\nversion: ai-tag-contracts-v1\n",
        ),
        (
            "    definition: 当前 ReviewUnit 显式建立、配置或执行 ArkUI 视觉动画或组件状态过渡。\n",
            "    definition: 当前 ReviewUnit 显式建立、配置或执行 ArkUI 视觉动画或组件状态过渡。\n"
            "    definition: 重复。\n",
        ),
    ],
)
def test_catalog_rejects_duplicate_yaml_keys(
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    text = DEFAULT_AI_TAG_CONTRACTS_PATH.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "catalog.yaml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(ValueError, match="unable to load AI Tag contract catalog"):
        load_ai_tag_contract_catalog(path)


def test_catalog_rejects_registry_description_drift(tmp_path: Path) -> None:
    tags_path = tmp_path / "tags.yaml"
    dimensions_path = tmp_path / "dimensions.yaml"
    tags_text = DEFAULT_TAGS_PATH.read_text(encoding="utf-8")
    tags_path.write_text(
        tags_text.replace(
            "代码使用 ArkUI 动画 API 或 transition 属性",
            "候选语义发生变化",
            1,
        ),
        encoding="utf-8",
    )
    dimensions_path.write_text(
        DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    drifted = load_feature_config(tags_path, dimensions_path)

    with pytest.raises(ValueError, match="Active registry fingerprint differs"):
        validate_catalog_against_feature_config(
            load_default_ai_tag_contract_catalog(),
            drifted,
        )


def test_catalog_registry_binding_intentionally_ignores_static_trigger_drift(
    tmp_path: Path,
) -> None:
    tags_path = tmp_path / "tags.yaml"
    dimensions_path = tmp_path / "dimensions.yaml"
    tags_text = DEFAULT_TAGS_PATH.read_text(encoding="utf-8")
    tags_path.write_text(
        tags_text.replace("any_api: [animateTo]", "any_api: [animateCandidate]", 1),
        encoding="utf-8",
    )
    dimensions_path.write_text(
        DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    drifted = load_feature_config(tags_path, dimensions_path)

    assert drifted.fingerprint != load_default_feature_config().fingerprint
    assert active_tag_registry_fingerprint(drifted) == active_tag_registry_fingerprint(
        load_default_feature_config()
    )
    validate_catalog_against_feature_config(
        load_default_ai_tag_contract_catalog(),
        drifted,
    )


def test_prompt_loader_and_model_policy_fail_closed(tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")
    nul = tmp_path / "nul.md"
    nul.write_text("json\x00", encoding="utf-8")
    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"\xff")
    symlink = tmp_path / "prompt-link.md"
    symlink.symlink_to(DEFAULT_AI_TAG_PROMPT_PATH)

    for path in (empty, nul, invalid, symlink):
        with pytest.raises(ValueError):
            load_ai_tag_prompt(path)

    policy_payload = _policy_payload()
    policy_payload["temperature"] = False
    with pytest.raises(ValueError, match="temperature must be the integer 0"):
        seal_ai_tag_model_policy(policy_payload)


def test_default_assets_exist_in_source_tree() -> None:
    assert DEFAULT_AI_TAG_CONTRACTS_PATH.is_file()
    assert DEFAULT_AI_TAG_PROMPT_PATH.is_file()
