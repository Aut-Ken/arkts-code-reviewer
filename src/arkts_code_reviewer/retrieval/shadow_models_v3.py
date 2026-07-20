from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.models import SourceRef
from arkts_code_reviewer.retrieval.models import ApplicabilityResult, IndexOrigin
from arkts_code_reviewer.retrieval.request_v2 import candidate_dimension_ids_for_ai_tags

RETRIEVAL_SHADOW_RESULT_SCHEMA_VERSION: Literal["retrieval-shadow-result-v1"] = (
    "retrieval-shadow-result-v1"
)

ShadowPoolId = Literal[
    "formal_exact",
    "file_hint",
    "text_keyword",
    "ai_inferred",
    "semantic_vector",
]
ShadowArmId = Literal["static_vector", "hybrid"]
ShadowMatchKind = Literal[
    "rule_id",
    "api",
    "component",
    "decorator",
    "tag",
    "keyword",
    "vector",
]
ShadowMatchScope = Literal[
    "unit_exact",
    "file_hint",
    "text_keyword",
    "ai_inferred",
    "semantic",
]
ShadowSelectionStatus = Literal[
    "selected",
    "token_budget",
    "result_limit",
    "context_dispatch_blocked",
]
ShadowDiagnosticCode = Literal[
    "applicability_unknown",
    "budget_exhausted",
    "context_dispatch_blocked",
    "embedding_unavailable",
    "empty_result",
    "parser_degraded",
]
FormalExecutionStatus = Literal["valid_result", "invalid_output", "unavailable"]

SHADOW_POOL_ORDER: tuple[ShadowPoolId, ...] = (
    "formal_exact",
    "file_hint",
    "text_keyword",
    "ai_inferred",
    "semantic_vector",
)

_HASH = r"[0-9a-f]{64}"
_RESULT_ID_PATTERN = rf"^retrieval-shadow-result:sha256:{_HASH}$"
_REQUEST_V3_ID_PATTERN = rf"^retrieval-request-v3:sha256:{_HASH}$"
_REQUEST_V1_ID_PATTERN = rf"^retrieval-request:sha256:{_HASH}$"
_EVIDENCE_PACK_ID_PATTERN = rf"^evidence-pack:sha256:{_HASH}$"
_INDEX_VERSION_PATTERN = rf"^knowledge-index:sha256:{_HASH}$"
_SOURCE_BUNDLE_ID_PATTERN = rf"^source-bundle:sha256:{_HASH}$"
_PROFILE_ID_PATTERN = rf"^feature-profile:sha256:{_HASH}$"
_HYBRID_ID_PATTERN = rf"^hybrid-analysis-v2:sha256:{_HASH}$"
_OUTCOME_ID_PATTERN = rf"^ai-tag-outcome-v2:sha256:{_HASH}$"
_RESULT_V2_ID_PATTERN = rf"^ai-tag-result-v2:sha256:{_HASH}$"
_SUBJECT_ID_PATTERN = rf"^ai-tag-trusted-execution-subject:sha256:{_HASH}$"
_ATTESTATION_ID_PATTERN = rf"^ai-tag-trusted-runner-attestation:sha256:{_HASH}$"
_RETRIEVAL_CONFIG_ID_PATTERN = rf"^retrieval-config:sha256:{_HASH}$"
_SHADOW_POLICY_ID_PATTERN = rf"^retrieval-shadow-policy:sha256:{_HASH}$"
_KNOWLEDGE_BUILD_ID_PATTERN = (
    rf"^(?:published-knowledge|evaluation-knowledge|retrieval-fixture):sha256:{_HASH}$"
)

_BUILD_PREFIX_BY_ORIGIN: dict[IndexOrigin, str] = {
    "publication": "published-knowledge:",
    "evaluation_fixture": "evaluation-knowledge:",
    "golden_fixture": "retrieval-fixture:",
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _parse_sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _validate_strings(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise ValueError(f"{context} must contain non-empty trimmed strings")
    if any(ord(character) < 32 or ord(character) == 127 for value in values for character in value):
        raise ValueError(f"{context} must not contain control characters")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _canonical_hash(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _pool_sort_key(pool: ShadowPoolId) -> int:
    return SHADOW_POOL_ORDER.index(pool)


class ShadowEvidenceMatchV3(_FrozenModel):
    kind: ShadowMatchKind
    value: Annotated[str, Field(min_length=1)]
    scope: ShadowMatchScope

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Shadow Evidence match value must be trimmed text")
        return value

    @model_validator(mode="after")
    def validate_scope_kind_pair(self) -> Self:
        allowed: dict[ShadowMatchScope, set[ShadowMatchKind]] = {
            "unit_exact": {"rule_id", "api", "component", "decorator", "tag"},
            "file_hint": {"tag"},
            "text_keyword": {"keyword"},
            "ai_inferred": {"tag"},
            "semantic": {"vector"},
        }
        if self.kind not in allowed[self.scope]:
            raise ValueError("Shadow Evidence match kind cannot use this scope")
        return self


class ShadowPoolCandidateV3(_FrozenModel):
    pool: ShadowPoolId
    rank: Annotated[int, Field(ge=1)]
    rule_id: Annotated[str, Field(min_length=1)]
    path_score: float
    matched_by: tuple[ShadowEvidenceMatchV3, ...]
    applicability: ApplicabilityResult
    formal_dimension_overlap: Annotated[int, Field(ge=0)]
    authority_priority: Annotated[int, Field(ge=0)]

    @field_validator("matched_by", mode="before")
    @classmethod
    def parse_matches(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Shadow Pool Candidate matches")

    @field_validator("rule_id")
    @classmethod
    def validate_rule_id(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Shadow Pool Candidate rule ID must be trimmed text")
        return value

    @field_validator("path_score")
    @classmethod
    def validate_path_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Shadow Pool Candidate path score must be finite")
        return value

    @model_validator(mode="after")
    def validate_pool_scope(self) -> Self:
        expected_scope: dict[ShadowPoolId, ShadowMatchScope] = {
            "formal_exact": "unit_exact",
            "file_hint": "file_hint",
            "text_keyword": "text_keyword",
            "ai_inferred": "ai_inferred",
            "semantic_vector": "semantic",
        }
        keys = tuple((item.kind, item.scope, item.value) for item in self.matched_by)
        if not keys or keys != tuple(sorted(set(keys))):
            raise ValueError("Shadow Pool Candidate matches must be sorted and unique")
        if any(item.scope != expected_scope[self.pool] for item in self.matched_by):
            raise ValueError("Shadow Pool Candidate scope differs from its pool")
        return self


class ShadowCandidatePoolV3(_FrozenModel):
    pool: ShadowPoolId
    rrf_weight: Annotated[float, Field(gt=0)]
    candidates: tuple[ShadowPoolCandidateV3, ...]

    @field_validator("candidates", mode="before")
    @classmethod
    def parse_candidates(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Shadow Candidate Pool candidates")

    @field_validator("rrf_weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Shadow Candidate Pool weight must be finite")
        return value

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        ranks = tuple(item.rank for item in self.candidates)
        rule_ids = tuple(item.rule_id for item in self.candidates)
        if ranks != tuple(range(1, len(self.candidates) + 1)):
            raise ValueError("Shadow Candidate Pool ranks must be contiguous")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Shadow Candidate Pool cannot repeat a Clause")
        if any(item.pool != self.pool for item in self.candidates):
            raise ValueError("Shadow Candidate Pool contains another pool")
        expected_order = tuple(
            sorted(
                self.candidates,
                key=lambda item: (
                    -item.path_score,
                    -len(item.matched_by),
                    -item.formal_dimension_overlap,
                    item.applicability == "unknown",
                    -item.authority_priority,
                    item.rule_id,
                ),
            )
        )
        if self.candidates != expected_order:
            raise ValueError("Shadow Candidate Pool order differs from its ranking policy")
        return self


class ShadowRankContributionV3(_FrozenModel):
    pool: ShadowPoolId
    pool_rank: Annotated[int, Field(ge=1)]
    rrf_weight: Annotated[float, Field(gt=0)]
    rrf_contribution: Annotated[float, Field(gt=0)]

    @field_validator("rrf_weight", "rrf_contribution")
    @classmethod
    def validate_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Shadow rank contribution values must be finite")
        return value


class ShadowRankedClauseV3(_FrozenModel):
    rank: Annotated[int, Field(ge=1)]
    rule_id: Annotated[str, Field(min_length=1)]
    rule_type: Annotated[str, Field(min_length=1)]
    status: Literal["Draft", "Baselined"]
    text: Annotated[str, Field(min_length=1)]
    heading_path: tuple[str, ...]
    parent_context: str | None = None
    dimension_ids: tuple[str, ...]
    tags: tuple[str, ...]
    apis: tuple[str, ...]
    components: tuple[str, ...]
    decorators: tuple[str, ...]
    domains: tuple[str, ...]
    source_ref: SourceRef
    matched_by: tuple[ShadowEvidenceMatchV3, ...]
    contributions: tuple[ShadowRankContributionV3, ...]
    applicability: ApplicabilityResult
    rrf_score: Annotated[float, Field(gt=0)]
    authority_priority: Annotated[int, Field(ge=0)]
    formal_dimension_overlap: Annotated[int, Field(ge=0)]
    token_count: Annotated[int, Field(ge=1)]
    selection_status: ShadowSelectionStatus

    @field_validator(
        "heading_path",
        "dimension_ids",
        "tags",
        "apis",
        "components",
        "decorators",
        "domains",
        "matched_by",
        "contributions",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Shadow Ranked Clause collections")

    @field_validator("rrf_score")
    @classmethod
    def validate_rrf_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Shadow Ranked Clause RRF score must be finite")
        return value

    @model_validator(mode="after")
    def validate_clause(self) -> Self:
        for values, context in (
            (self.dimension_ids, "dimension_ids"),
            (self.tags, "tags"),
            (self.apis, "apis"),
            (self.components, "components"),
            (self.decorators, "decorators"),
            (self.domains, "domains"),
        ):
            _validate_strings(values, f"ShadowRankedClauseV3.{context}")
        match_keys = tuple((item.kind, item.scope, item.value) for item in self.matched_by)
        if not match_keys or match_keys != tuple(sorted(set(match_keys))):
            raise ValueError("Shadow Ranked Clause matches must be sorted and unique")
        contribution_keys = tuple(_pool_sort_key(item.pool) for item in self.contributions)
        if not contribution_keys or contribution_keys != tuple(sorted(set(contribution_keys))):
            raise ValueError("Shadow rank contributions must use canonical unique pool order")
        if round(sum(item.rrf_contribution for item in self.contributions), 8) != (self.rrf_score):
            raise ValueError("Shadow Ranked Clause score must equal its pool contributions")
        return self


class ShadowDiagnosticV3(_FrozenModel):
    code: ShadowDiagnosticCode
    detail: Annotated[str, Field(min_length=1)]
    rule_id: str | None = None

    @field_validator("detail", "rule_id")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is not None and (
            not value
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("Shadow diagnostic text must be non-empty and single-line")
        return value


class ShadowArmResultV3(_FrozenModel):
    arm: ShadowArmId
    rrf_k: Annotated[int, Field(ge=1)]
    result_limit: Annotated[int, Field(ge=1)]
    token_budget: Annotated[int, Field(ge=1)]
    used_tokens: Annotated[int, Field(ge=0)]
    formal_dimension_ids: tuple[str, ...]
    covered_dimension_ids: tuple[str, ...]
    uncovered_dimension_ids: tuple[str, ...]
    ranked_clauses: tuple[ShadowRankedClauseV3, ...]
    selected_rule_ids: tuple[str, ...]
    diagnostics: tuple[ShadowDiagnosticV3, ...]

    @field_validator(
        "formal_dimension_ids",
        "covered_dimension_ids",
        "uncovered_dimension_ids",
        "ranked_clauses",
        "selected_rule_ids",
        "diagnostics",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Shadow Arm collections")

    @model_validator(mode="after")
    def validate_arm(self) -> Self:
        for values, context in (
            (self.formal_dimension_ids, "formal_dimension_ids"),
            (self.covered_dimension_ids, "covered_dimension_ids"),
            (self.uncovered_dimension_ids, "uncovered_dimension_ids"),
        ):
            _validate_strings(values, f"ShadowArmResultV3.{context}")
        ranks = tuple(item.rank for item in self.ranked_clauses)
        rule_ids = tuple(item.rule_id for item in self.ranked_clauses)
        if ranks != tuple(range(1, len(self.ranked_clauses) + 1)):
            raise ValueError("Shadow Arm ranks must be contiguous")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Shadow Arm cannot repeat a Clause")
        expected_selected = tuple(
            item.rule_id for item in self.ranked_clauses if item.selection_status == "selected"
        )
        if self.selected_rule_ids != expected_selected:
            raise ValueError("Shadow Arm selected rules differ from Clause statuses")
        if self.used_tokens != sum(
            item.token_count for item in self.ranked_clauses if item.selection_status == "selected"
        ):
            raise ValueError("Shadow Arm used token count differs from selected Clauses")
        if self.used_tokens > self.token_budget:
            raise ValueError("Shadow Arm exceeds its Unit token budget")

        expected_statuses: dict[str, ShadowSelectionStatus] = {}
        selected_rules: set[str] = set()
        expected_used_tokens = 0

        def select(clause: ShadowRankedClauseV3) -> bool:
            nonlocal expected_used_tokens
            if clause.rule_id in selected_rules:
                return False
            if expected_statuses.get(clause.rule_id) == "token_budget":
                return False
            if len(selected_rules) >= self.result_limit:
                expected_statuses.setdefault(clause.rule_id, "result_limit")
                return False
            if expected_used_tokens + clause.token_count > self.token_budget:
                expected_statuses[clause.rule_id] = "token_budget"
                return False
            selected_rules.add(clause.rule_id)
            expected_statuses[clause.rule_id] = "selected"
            expected_used_tokens += clause.token_count
            return True

        for dimension_id in self.formal_dimension_ids:
            for clause in self.ranked_clauses:
                if dimension_id in clause.dimension_ids and clause.rule_id not in selected_rules:
                    if select(clause):
                        break
        for clause in self.ranked_clauses:
            select(clause)

        if any(
            clause.selection_status != expected_statuses[clause.rule_id]
            for clause in self.ranked_clauses
        ):
            raise ValueError("Shadow Arm Clause selection differs from frozen budget policy")
        if self.used_tokens != expected_used_tokens:
            raise ValueError("Shadow Arm used token count differs from frozen selection")
        if any(
            clause.formal_dimension_overlap
            != len(set(clause.dimension_ids).intersection(self.formal_dimension_ids))
            for clause in self.ranked_clauses
        ):
            raise ValueError("Shadow Clause formal Dimension overlap is inconsistent")

        formal = set(self.formal_dimension_ids)
        if set(self.covered_dimension_ids).intersection(self.uncovered_dimension_ids):
            raise ValueError("Shadow Arm covered and uncovered Dimensions overlap")
        if set((*self.covered_dimension_ids, *self.uncovered_dimension_ids)) != formal:
            raise ValueError("Shadow Arm coverage must partition formal Dimensions")
        expected_covered = tuple(
            sorted(
                formal.intersection(
                    dimension_id
                    for clause in self.ranked_clauses
                    if clause.rule_id in selected_rules
                    for dimension_id in clause.dimension_ids
                )
            )
        )
        if self.covered_dimension_ids != expected_covered:
            raise ValueError("Shadow Arm coverage differs from selected Clauses")
        if self.uncovered_dimension_ids != tuple(sorted(formal - set(expected_covered))):
            raise ValueError("Shadow Arm uncovered Dimensions differ from selected Clauses")
        diagnostic_keys = tuple(
            (item.code, item.rule_id or "", item.detail) for item in self.diagnostics
        )
        if diagnostic_keys != tuple(sorted(set(diagnostic_keys))):
            raise ValueError("Shadow Arm diagnostics must be sorted and unique")
        if self.arm == "static_vector" and any(
            contribution.pool == "ai_inferred"
            for clause in self.ranked_clauses
            for contribution in clause.contributions
        ):
            raise ValueError("Static-vector arm cannot contain AI rank contribution")
        if self.arm == "static_vector" and any(
            match.scope == "ai_inferred"
            for clause in self.ranked_clauses
            for match in clause.matched_by
        ):
            raise ValueError("Static-vector arm cannot contain AI match provenance")
        return self


class ShadowUnitComparisonV3(_FrozenModel):
    unit_id: Annotated[str, Field(min_length=1)]
    profile_id: Annotated[str, Field(pattern=_PROFILE_ID_PATTERN)]
    formal_hybrid_analysis_id: Annotated[str, Field(pattern=_HYBRID_ID_PATTERN)]
    formal_execution_outcome_id: Annotated[str, Field(pattern=_OUTCOME_ID_PATTERN)]
    formal_ai_result_id: Annotated[str | None, Field(pattern=_RESULT_V2_ID_PATTERN)]
    trusted_execution_subject_id: Annotated[str, Field(pattern=_SUBJECT_ID_PATTERN)]
    trusted_runner_attestation_id: Annotated[str, Field(pattern=_ATTESTATION_ID_PATTERN)]
    formal_execution_status: FormalExecutionStatus
    exact_tags: tuple[str, ...]
    routing_tags: tuple[str, ...]
    ai_inferred_tags: tuple[str, ...]
    tag_disagreements: tuple[str, ...]
    formal_dimension_ids: tuple[str, ...]
    routing_dimension_ids: tuple[str, ...]
    candidate_dimension_ids: tuple[str, ...]
    candidate_dimension_policy: Literal["diagnostic_only"]
    pools: tuple[ShadowCandidatePoolV3, ...]
    static_vector: ShadowArmResultV3
    hybrid: ShadowArmResultV3

    @field_validator(
        "exact_tags",
        "routing_tags",
        "ai_inferred_tags",
        "tag_disagreements",
        "formal_dimension_ids",
        "routing_dimension_ids",
        "candidate_dimension_ids",
        "pools",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Shadow Unit collections")

    @field_validator(
        "exact_tags",
        "routing_tags",
        "ai_inferred_tags",
        "tag_disagreements",
        "formal_dimension_ids",
        "routing_dimension_ids",
        "candidate_dimension_ids",
    )
    @classmethod
    def validate_string_sequences(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_strings(value, "Shadow Unit values")

    @model_validator(mode="after")
    def validate_unit(self) -> Self:
        if tuple(item.pool for item in self.pools) != SHADOW_POOL_ORDER:
            raise ValueError("Shadow Unit requires all candidate pools in canonical order")
        if self.static_vector.arm != "static_vector" or self.hybrid.arm != "hybrid":
            raise ValueError("Shadow Unit arm labels are invalid")
        if (
            self.static_vector.rrf_k != self.hybrid.rrf_k
            or self.static_vector.result_limit != self.hybrid.result_limit
            or self.static_vector.token_budget != self.hybrid.token_budget
        ):
            raise ValueError("Shadow Unit arms must share ranking and budget policy")
        if (
            self.static_vector.formal_dimension_ids != self.formal_dimension_ids
            or self.hybrid.formal_dimension_ids != self.formal_dimension_ids
        ):
            raise ValueError("Shadow Unit arms must use only formal Dimensions")
        ai_pool = self.pools[SHADOW_POOL_ORDER.index("ai_inferred")]
        ai_values = {match.value for item in ai_pool.candidates for match in item.matched_by}
        if not ai_values.issubset(self.ai_inferred_tags):
            raise ValueError("AI pool contains a Tag outside verified AI positives")
        formal_pool = self.pools[SHADOW_POOL_ORDER.index("formal_exact")]
        formal_tag_values = {
            match.value
            for item in formal_pool.candidates
            for match in item.matched_by
            if match.kind == "tag"
        }
        if not formal_tag_values.issubset(self.exact_tags):
            raise ValueError("Formal pool contains a Tag outside static exact Tags")
        hint_pool = self.pools[SHADOW_POOL_ORDER.index("file_hint")]
        hint_values = {match.value for item in hint_pool.candidates for match in item.matched_by}
        if not hint_values.issubset(set(self.routing_tags) - set(self.exact_tags)):
            raise ValueError("File-hint pool contains a Tag outside routing-only Tags")
        if self.formal_ai_result_id is None:
            if self.formal_execution_status == "valid_result":
                raise ValueError("valid Formal execution status requires a Result")
            if self.ai_inferred_tags or self.tag_disagreements or ai_pool.candidates:
                raise ValueError("Formal execution without Result cannot carry AI signals")
        elif self.formal_execution_status != "valid_result":
            raise ValueError("Formal Result requires valid execution status")
        if not set(self.formal_dimension_ids).issubset(self.routing_dimension_ids):
            raise ValueError("Formal Dimensions must be a subset of routing Dimensions")
        feature_config = load_default_feature_config()
        all_tags = (
            *self.exact_tags,
            *self.routing_tags,
            *self.ai_inferred_tags,
            *self.tag_disagreements,
        )
        if not set(all_tags).issubset(feature_config.tags_by_id):
            raise ValueError("Shadow Unit contains unregistered Tags")
        if set(self.ai_inferred_tags).intersection(self.tag_disagreements):
            raise ValueError("Shadow AI-positive Tags and disagreements must be disjoint")
        if not set(self.tag_disagreements).issubset(self.exact_tags):
            raise ValueError("Shadow disagreements must be static exact Tags")
        if self.candidate_dimension_ids != candidate_dimension_ids_for_ai_tags(
            self.ai_inferred_tags
        ):
            raise ValueError("Shadow candidate Dimensions must rebuild from AI-positive Tags")
        all_dimensions = (
            *self.formal_dimension_ids,
            *self.routing_dimension_ids,
            *self.candidate_dimension_ids,
        )
        if not set(all_dimensions).issubset(feature_config.dimensions_by_id):
            raise ValueError("Shadow Unit contains unregistered Dimensions")
        if any(
            feature_config.dimensions_by_id[dimension_id].retrieval_policy == "disabled"
            for dimension_id in all_dimensions
        ):
            raise ValueError("Shadow Unit contains disabled Dimensions")

        candidates_by_pool = {
            pool.pool: {candidate.rule_id: candidate for candidate in pool.candidates}
            for pool in self.pools
        }
        pools_by_id = {pool.pool: pool for pool in self.pools}
        for arm in (self.static_vector, self.hybrid):
            active_pools: tuple[ShadowPoolId, ...] = (
                (
                    "formal_exact",
                    "file_hint",
                    "text_keyword",
                    "semantic_vector",
                )
                if arm.arm == "static_vector"
                else SHADOW_POOL_ORDER
            )
            expected_rule_ids = set().union(
                *(set(candidates_by_pool[pool]) for pool in active_pools)
            )
            if {clause.rule_id for clause in arm.ranked_clauses} != expected_rule_ids:
                raise ValueError("Shadow Arm Clauses differ from its candidate pools")

            clauses_by_rule = {clause.rule_id: clause for clause in arm.ranked_clauses}
            for rule_id in expected_rule_ids:
                clause = clauses_by_rule[rule_id]
                present = tuple(
                    (pool, candidates_by_pool[pool][rule_id])
                    for pool in active_pools
                    if rule_id in candidates_by_pool[pool]
                )
                expected_contributions = tuple(
                    (
                        pool,
                        candidate.rank,
                        pools_by_id[pool].rrf_weight,
                        round(
                            pools_by_id[pool].rrf_weight / (arm.rrf_k + candidate.rank),
                            8,
                        ),
                    )
                    for pool, candidate in present
                )
                actual_contributions = tuple(
                    (
                        contribution.pool,
                        contribution.pool_rank,
                        contribution.rrf_weight,
                        contribution.rrf_contribution,
                    )
                    for contribution in clause.contributions
                )
                if actual_contributions != expected_contributions:
                    raise ValueError(
                        "Shadow rank contribution differs from pool rank/weight/RRF formula"
                    )
                expected_matches = tuple(
                    sorted(
                        {
                            (match.kind, match.scope, match.value): match
                            for _, candidate in present
                            for match in candidate.matched_by
                        }.values(),
                        key=lambda match: (match.kind, match.scope, match.value),
                    )
                )
                if clause.matched_by != expected_matches:
                    raise ValueError("Shadow Clause provenance differs from candidate pools")
                applicability = {candidate.applicability for _, candidate in present}
                if len(applicability) != 1 or clause.applicability not in applicability:
                    raise ValueError("Shadow Clause applicability differs from candidate pools")
                if clause.formal_dimension_overlap != max(
                    candidate.formal_dimension_overlap for _, candidate in present
                ):
                    raise ValueError("Shadow Clause Dimension overlap differs from candidate pools")
                if clause.authority_priority != max(
                    candidate.authority_priority for _, candidate in present
                ):
                    raise ValueError("Shadow Clause authority differs from candidate pools")

            def path_score(rule_id: str, pool: ShadowPoolId) -> float:
                candidate = candidates_by_pool[pool].get(rule_id)
                return -1.0 if candidate is None else candidate.path_score

            expected_order = tuple(
                sorted(
                    expected_rule_ids,
                    key=lambda rule_id: (
                        -clauses_by_rule[rule_id].rrf_score,
                        rule_id not in candidates_by_pool["formal_exact"],
                        -path_score(rule_id, "formal_exact"),
                        -path_score(rule_id, "semantic_vector"),
                        -path_score(rule_id, "file_hint"),
                        -path_score(rule_id, "text_keyword"),
                        -path_score(rule_id, "ai_inferred"),
                        clauses_by_rule[rule_id].applicability == "unknown",
                        -clauses_by_rule[rule_id].authority_priority,
                        rule_id,
                    ),
                )
            )
            if tuple(clause.rule_id for clause in arm.ranked_clauses) != expected_order:
                raise ValueError("Shadow Arm ranking differs from the frozen fusion policy")
        return self


class RetrievalShadowResultV3(_FrozenModel):
    schema_version: Literal["retrieval-shadow-result-v1"]
    result_id: Annotated[str, Field(pattern=_RESULT_ID_PATTERN)]
    verified_request_id: Annotated[str, Field(pattern=_REQUEST_V3_ID_PATTERN)]
    v1_control_request_id: Annotated[str, Field(pattern=_REQUEST_V1_ID_PATTERN)]
    v1_control_evidence_pack_id: Annotated[
        str,
        Field(pattern=_EVIDENCE_PACK_ID_PATTERN),
    ]
    base_retrieval_config_fingerprint: Annotated[
        str,
        Field(pattern=_RETRIEVAL_CONFIG_ID_PATTERN),
    ]
    shadow_policy_fingerprint: Annotated[
        str,
        Field(pattern=_SHADOW_POLICY_ID_PATTERN),
    ]
    index_version: Annotated[str, Field(pattern=_INDEX_VERSION_PATTERN)]
    index_origin: IndexOrigin
    knowledge_build_id: Annotated[str, Field(pattern=_KNOWLEDGE_BUILD_ID_PATTERN)]
    source_bundle_id: Annotated[str, Field(pattern=_SOURCE_BUNDLE_ID_PATTERN)]
    embedding_version: str | None = None
    formal_attestation_ids: tuple[str, ...]
    execution_mode: Literal["shadow"]
    formal_use_scope: Literal["hybrid_retrieval_shadow_only"]
    authority_status: Literal["serialized_audit_only"]
    evidence_qualification_status: Literal["not_qualified"]
    production_qualified: Literal[False]
    user_visible: Literal[False]
    prompt_eligible: Literal[False]
    finding_evidence_eligible: Literal[False]
    downstream_use: Literal["audit_and_blind_evaluation_only"]
    degraded: bool
    units: tuple[ShadowUnitComparisonV3, ...]

    @field_validator("formal_attestation_ids", "units", mode="before")
    @classmethod
    def parse_sequences(cls, value: object) -> tuple[object, ...]:
        return _parse_sequence(value, "Retrieval Shadow Result collections")

    @field_validator("embedding_version")
    @classmethod
    def validate_embedding_version(cls, value: str | None) -> str | None:
        if value is not None and (
            not value or value != value.strip() or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Shadow embedding version must be trimmed text")
        return value

    @classmethod
    def create(
        cls,
        *,
        verified_request_id: str,
        v1_control_request_id: str,
        v1_control_evidence_pack_id: str,
        base_retrieval_config_fingerprint: str,
        shadow_policy_fingerprint: str,
        index_version: str,
        index_origin: IndexOrigin,
        knowledge_build_id: str,
        source_bundle_id: str,
        embedding_version: str | None,
        formal_attestation_ids: tuple[str, ...],
        units: tuple[ShadowUnitComparisonV3, ...],
    ) -> Self:
        ordered_units = tuple(sorted(units, key=lambda item: item.unit_id))
        degraded_codes = {
            "context_dispatch_blocked",
            "embedding_unavailable",
            "parser_degraded",
        }
        degraded = any(
            diagnostic.code in degraded_codes
            for unit in ordered_units
            for arm in (unit.static_vector, unit.hybrid)
            for diagnostic in arm.diagnostics
        )
        identity_payload: dict[str, object] = {
            "schema_version": RETRIEVAL_SHADOW_RESULT_SCHEMA_VERSION,
            "verified_request_id": verified_request_id,
            "v1_control_request_id": v1_control_request_id,
            "v1_control_evidence_pack_id": v1_control_evidence_pack_id,
            "base_retrieval_config_fingerprint": base_retrieval_config_fingerprint,
            "shadow_policy_fingerprint": shadow_policy_fingerprint,
            "index_version": index_version,
            "index_origin": index_origin,
            "knowledge_build_id": knowledge_build_id,
            "source_bundle_id": source_bundle_id,
            "embedding_version": embedding_version,
            "formal_attestation_ids": formal_attestation_ids,
            "execution_mode": "shadow",
            "formal_use_scope": "hybrid_retrieval_shadow_only",
            "authority_status": "serialized_audit_only",
            "evidence_qualification_status": "not_qualified",
            "production_qualified": False,
            "user_visible": False,
            "prompt_eligible": False,
            "finding_evidence_eligible": False,
            "downstream_use": "audit_and_blind_evaluation_only",
            "degraded": degraded,
            "units": [item.model_dump(mode="json") for item in ordered_units],
        }
        return cls(
            result_id=_canonical_hash(
                "retrieval-shadow-result",
                identity_payload,
            ),
            schema_version=RETRIEVAL_SHADOW_RESULT_SCHEMA_VERSION,
            verified_request_id=verified_request_id,
            v1_control_request_id=v1_control_request_id,
            v1_control_evidence_pack_id=v1_control_evidence_pack_id,
            base_retrieval_config_fingerprint=base_retrieval_config_fingerprint,
            shadow_policy_fingerprint=shadow_policy_fingerprint,
            index_version=index_version,
            index_origin=index_origin,
            knowledge_build_id=knowledge_build_id,
            source_bundle_id=source_bundle_id,
            embedding_version=embedding_version,
            formal_attestation_ids=formal_attestation_ids,
            execution_mode="shadow",
            formal_use_scope="hybrid_retrieval_shadow_only",
            authority_status="serialized_audit_only",
            evidence_qualification_status="not_qualified",
            production_qualified=False,
            user_visible=False,
            prompt_eligible=False,
            finding_evidence_eligible=False,
            downstream_use="audit_and_blind_evaluation_only",
            degraded=degraded,
            units=ordered_units,
        )

    def identity_payload(self) -> dict[str, object]:
        return cast(
            "dict[str, object]",
            self.model_dump(mode="json", exclude={"result_id"}),
        )

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if not self.knowledge_build_id.startswith(_BUILD_PREFIX_BY_ORIGIN[self.index_origin]):
            raise ValueError("Shadow result origin differs from knowledge build")
        unit_ids = tuple(item.unit_id for item in self.units)
        if not unit_ids or unit_ids != tuple(sorted(set(unit_ids))):
            raise ValueError("Shadow result Units must be non-empty, sorted, and unique")
        expected_attestations = tuple(item.trusted_runner_attestation_id for item in self.units)
        if self.formal_attestation_ids != expected_attestations:
            raise ValueError("Shadow result attestation coverage differs from Units")
        expected_status = "Draft" if self.index_origin == "evaluation_fixture" else "Baselined"
        if any(
            clause.status != expected_status
            for unit in self.units
            for arm in (unit.static_vector, unit.hybrid)
            for clause in arm.ranked_clauses
        ):
            raise ValueError("Shadow result Clause status differs from index origin")
        degraded_codes = {
            "context_dispatch_blocked",
            "embedding_unavailable",
            "parser_degraded",
        }
        expected_degraded = any(
            diagnostic.code in degraded_codes
            for unit in self.units
            for arm in (unit.static_vector, unit.hybrid)
            for diagnostic in arm.diagnostics
        )
        if self.degraded != expected_degraded:
            raise ValueError("Shadow result degraded flag differs from diagnostics")
        if self.result_id != _canonical_hash(
            "retrieval-shadow-result",
            self.identity_payload(),
        ):
            raise ValueError("Retrieval Shadow Result ID does not match content")
        return self


def load_retrieval_shadow_result_v3(raw: str | bytes) -> RetrievalShadowResultV3:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Retrieval Shadow Result must use UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("Retrieval Shadow Result input must be str or bytes")
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (json.JSONDecodeError, _DuplicateKeyError, ValueError) as exc:
        raise ValueError(f"invalid Retrieval Shadow Result JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid Retrieval Shadow Result JSON: top-level value must be an object")
    try:
        return RetrievalShadowResultV3.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid Retrieval Shadow Result: {exc}") from exc


__all__ = [
    "RETRIEVAL_SHADOW_RESULT_SCHEMA_VERSION",
    "SHADOW_POOL_ORDER",
    "FormalExecutionStatus",
    "RetrievalShadowResultV3",
    "ShadowArmId",
    "ShadowArmResultV3",
    "ShadowCandidatePoolV3",
    "ShadowDiagnosticCode",
    "ShadowDiagnosticV3",
    "ShadowEvidenceMatchV3",
    "ShadowMatchKind",
    "ShadowMatchScope",
    "ShadowPoolCandidateV3",
    "ShadowPoolId",
    "ShadowRankContributionV3",
    "ShadowRankedClauseV3",
    "ShadowSelectionStatus",
    "ShadowUnitComparisonV3",
    "load_retrieval_shadow_result_v3",
]
