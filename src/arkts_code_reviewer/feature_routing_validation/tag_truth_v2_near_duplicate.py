from __future__ import annotations

import json
import os
import re
import subprocess
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arkts_code_reviewer.feature_routing_validation.tag_truth_v2 import (
    bytes_hash,
    canonical_hash,
    canonical_json,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_provenance import (
    TagTruthV2ProvenanceVerification,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_review import (
    TagTruthV2Consensus,
)
from arkts_code_reviewer.feature_routing_validation.tag_truth_v2_selection import (
    BlindReviewCase,
    SelectionCase,
    TagTruthV2ReviewPacket,
    TagTruthV2Selection,
    verify_tag_truth_v2_review_packet,
)

TAG_TRUTH_V2_NEAR_DUPLICATE_POLICY_SCHEMA_VERSION = "tag-truth-near-duplicate-policy-v1"
TAG_TRUTH_V2_NEAR_DUPLICATE_SCREENING_SCHEMA_VERSION = "tag-truth-v2-near-duplicate-screening-v1"

_GIT_OBJECT_ID = r"^[0-9a-f]{40}$"
_SHA256 = r"^sha256:[0-9a-f]{64}$"
_POLICY_FINGERPRINT = r"^tag-truth-near-duplicate-policy:sha256:[0-9a-f]{64}$"
_INVENTORY_FINGERPRINT = r"^tag-truth-reference-inventory:sha256:[0-9a-f]{64}$"
_INVENTORY_SCOPE_FINGERPRINT = r"^tag-truth-reference-scope:sha256:[0-9a-f]{64}$"
_DOCUMENT_SET_FINGERPRINT = r"^tag-truth-reference-documents:sha256:[0-9a-f]{64}$"
_SCREENING_ID = r"^tag-truth-near-duplicate-screening:sha256:[0-9a-f]{64}$"
_CASE_ID = r"^case-[0-9a-f]{16}$"

ReferenceRole = Literal[
    "candidate_project",
    "exposure",
    "development_truth",
    "campaign_peer",
]
ScreeningAxis = Literal["file", "unit"]
AxisDecision = Literal["duplicate", "gray", "clear", "abstain"]
ScreeningOutcome = Literal["potential_duplicate", "review_required", "clean"]
SimilarityWorkStatus = Literal["evaluated", "abstained_resource_limit"]
SimilarityWorkBlocker = Literal[
    "selected_character_budget_exceeded",
    "unique_reference_character_budget_exceeded",
    "similarity_pair_budget_exceeded",
    "similarity_character_budget_exceeded",
    "recorded_match_budget_exceeded",
]
SimilaritySignal = Literal[
    "normalized_token_stream_equal",
    "content_containment",
    "reference_content_containment",
    "content_jaccard",
    "contiguous_token_run",
    "contiguous_reference_coverage",
    "shape_containment",
    "reference_shape_containment",
    "normalized_shape_token_stream_equal",
]

_ROLE_ORDER: Mapping[ReferenceRole, int] = {
    "candidate_project": 0,
    "exposure": 1,
    "development_truth": 2,
    "campaign_peer": 3,
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sequence(value: object, context: str) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{context} must be a sequence")
    return tuple(value)


def _sorted_unique(values: tuple[str, ...], context: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{context} must be sorted and unique")
    return values


def _single_line(value: str, context: str) -> str:
    if value != value.strip() or not value or any(ord(character) < 32 for character in value):
        raise ValueError(f"{context} must be non-empty, trimmed, and single-line")
    return value


def _relative_path(value: str, context: str) -> str:
    if (
        value != value.strip()
        or not value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{context} must be a non-empty trimmed POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{context} must be relative and cannot traverse parents")
    if path.as_posix() != value:
        raise ValueError(f"{context} must be normalized")
    return value


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={identity_field})


class RatioThreshold(_FrozenModel):
    numerator: Annotated[int, Field(ge=0)]
    denominator: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def validate_ratio(self) -> RatioThreshold:
        if self.numerator > self.denominator:
            raise ValueError("ratio threshold cannot exceed one")
        return self

    def reached(self, numerator: int, denominator: int) -> bool:
        return denominator > 0 and numerator * self.denominator >= self.numerator * denominator


class TagTruthV2NearDuplicatePolicy(_FrozenModel):
    schema_version: Literal["tag-truth-near-duplicate-policy-v1"]
    policy_fingerprint: Annotated[str, Field(pattern=_POLICY_FINGERPRINT)]
    policy_version: Literal["near-duplicate-shadow-v1"]
    approval_status: Literal["snapshot_only_not_approved"]
    reference_scope: Literal["all_tracked_utf8_text"]
    content_tokenizer_version: Literal["lexical-content-v1"]
    shape_tokenizer_version: Literal["lexical-shape-v1"]
    unicode_normalization: Literal["NFC"]
    content_shingle_width: Literal[7]
    shape_shingle_width: Literal[11]
    minimum_informative_content_shingles: Literal[32]
    hard_minimum_shared_content_shingles: Literal[32]
    content_containment_direction: Literal["bidirectional"]
    hard_content_containment: RatioThreshold
    hard_content_jaccard: RatioThreshold
    hard_contiguous_minimum_tokens: Literal[64]
    contiguous_coverage_direction: Literal["bidirectional"]
    hard_contiguous_selected_coverage: RatioThreshold
    gray_content_containment: RatioThreshold
    gray_content_jaccard: RatioThreshold
    gray_minimum_shared_content_shingles: Literal[16]
    gray_shape_containment: RatioThreshold
    gray_minimum_shared_shape_shingles: Literal[48]
    shape_containment_direction: Literal["bidirectional"]
    maximum_blob_bytes: Annotated[int, Field(ge=1024, le=16 * 1024 * 1024)]
    maximum_total_reference_bytes: Annotated[
        int,
        Field(ge=1024, le=2 * 1024 * 1024 * 1024),
    ]
    maximum_inventory_entries: Annotated[int, Field(ge=1, le=1_000_000)]
    work_estimate_version: Literal["nfc-character-work-v1"]
    maximum_selected_nfc_characters: Annotated[int, Field(ge=1, le=2_000_000_000)]
    maximum_unique_reference_nfc_characters: Annotated[
        int,
        Field(ge=1, le=2_000_000_000),
    ]
    maximum_similarity_pairs: Annotated[int, Field(ge=1, le=100_000_000)]
    maximum_similarity_pair_nfc_characters: Annotated[
        int,
        Field(ge=1, le=100_000_000_000),
    ]
    maximum_recorded_matches: Annotated[int, Field(ge=1, le=10_000_000)]
    gray_action: Literal["replace_re_review_reseal"]
    scan_failure_action: Literal["abstain"]
    calibration_truth_status: Literal["not_available"]
    calibration_truth_fingerprint: None

    @model_validator(mode="after")
    def validate_policy(self) -> TagTruthV2NearDuplicatePolicy:
        expected = canonical_hash(
            "tag-truth-near-duplicate-policy",
            _identity_payload(self, "policy_fingerprint"),
        )
        if self.policy_fingerprint != expected:
            raise ValueError("near-duplicate policy fingerprint does not match its fields")
        if (
            self.gray_content_containment.numerator * self.hard_content_containment.denominator
            > self.hard_content_containment.numerator * self.gray_content_containment.denominator
        ):
            raise ValueError("gray containment threshold cannot exceed hard containment")
        if (
            self.gray_content_jaccard.numerator * self.hard_content_jaccard.denominator
            > self.hard_content_jaccard.numerator * self.gray_content_jaccard.denominator
        ):
            raise ValueError("gray Jaccard threshold cannot exceed hard Jaccard")
        if self.maximum_recorded_matches > self.maximum_similarity_pairs:
            raise ValueError("recorded-match budget cannot exceed similarity-pair budget")
        return self


def near_duplicate_policy_payload_with_fingerprint(
    payload: Mapping[str, object],
) -> dict[str, object]:
    if "policy_fingerprint" in payload:
        raise ValueError("draft near-duplicate policy cannot contain policy_fingerprint")
    canonical = dict(payload)
    canonical["policy_fingerprint"] = canonical_hash(
        "tag-truth-near-duplicate-policy",
        canonical,
    )
    return canonical


@dataclass(frozen=True, slots=True)
class _Tokenization:
    tokens: tuple[str, ...]
    issues: tuple[str, ...]


_KEYWORDS = frozenset(
    {
        "abstract",
        "as",
        "async",
        "await",
        "break",
        "case",
        "catch",
        "class",
        "const",
        "constructor",
        "continue",
        "declare",
        "default",
        "delete",
        "do",
        "else",
        "enum",
        "export",
        "extends",
        "false",
        "finally",
        "for",
        "from",
        "function",
        "get",
        "if",
        "implements",
        "import",
        "in",
        "instanceof",
        "interface",
        "let",
        "namespace",
        "new",
        "null",
        "of",
        "override",
        "private",
        "protected",
        "public",
        "readonly",
        "return",
        "set",
        "static",
        "struct",
        "super",
        "switch",
        "this",
        "throw",
        "true",
        "try",
        "type",
        "typeof",
        "undefined",
        "var",
        "void",
        "while",
        "with",
        "yield",
    }
)

_OPERATORS = tuple(
    sorted(
        {
            ">>>=",
            "**=",
            "&&=",
            "||=",
            "??=",
            "===",
            "!==",
            ">>>",
            "<<=",
            ">>=",
            "...",
            "?.",
            "??",
            "=>",
            "==",
            "!=",
            "<=",
            ">=",
            "++",
            "--",
            "&&",
            "||",
            "**",
            "<<",
            ">>",
            "+=",
            "-=",
            "*=",
            "/=",
            "%=",
            "&=",
            "|=",
            "^=",
            "::",
        },
        key=lambda item: (-len(item), item),
    )
)


def _identifier_start(character: str) -> bool:
    return character in {"_", "$"} or unicodedata.category(character).startswith("L")


def _identifier_continue(character: str) -> bool:
    category = unicodedata.category(character)
    return _identifier_start(character) or category.startswith(("M", "N"))


def _tokenize_arkts_like(
    source: str,
    *,
    mode: Literal["lexical_content", "lexical_shape"],
) -> _Tokenization:
    text = unicodedata.normalize("NFC", source)
    tokens: list[str] = []
    issues: set[str] = set()
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                tokens.append("<unterminated-comment>")
                issues.add("unterminated_block_comment")
                break
            index = end + 2
            continue
        if character in {"'", '"'}:
            quote = character
            index += 1
            escaped = False
            terminated = False
            while index < length:
                current = text[index]
                index += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    terminated = True
                    break
                elif current in {"\n", "\r"}:
                    break
            tokens.append("<string>" if terminated else "<unterminated-string>")
            if not terminated:
                issues.add("unterminated_string_literal")
            continue
        if character == "`":
            index += 1
            body_start = index
            escaped = False
            terminated = False
            body_end = length
            while index < length:
                current = text[index]
                index += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == "`":
                    terminated = True
                    body_end = index - 1
                    break
            tokens.append("<template>" if terminated else "<unterminated-template>")
            if not terminated:
                issues.add("unterminated_template_literal")
            else:
                body = text[body_start:body_end]
                interpolation_starts = tuple(re.finditer(r"(?<!\\)\$\{", body))
                simple_interpolations = tuple(
                    re.finditer(r"(?<!\\)\$\{([^{}]*)\}", body, flags=re.DOTALL)
                )
                if len(interpolation_starts) != len(simple_interpolations):
                    issues.add("complex_template_interpolation")
                for interpolation in simple_interpolations:
                    expression = _tokenize_arkts_like(
                        interpolation.group(1),
                        mode=mode,
                    )
                    tokens.extend(
                        ("<template-expression>", *expression.tokens, "</template-expression>")
                    )
                    issues.update(expression.issues)
            continue
        if character.isdigit():
            end = index
            if text.startswith(("0x", "0X", "0b", "0B", "0o", "0O"), index):
                end += 2
                while end < length and (text[end].isalnum() or text[end] == "_"):
                    end += 1
            else:
                while end < length and (text[end].isdigit() or text[end] == "_"):
                    end += 1
                if end + 1 < length and text[end] == "." and text[end + 1].isdigit():
                    end += 1
                    while end < length and (text[end].isdigit() or text[end] == "_"):
                        end += 1
                if end < length and text[end] in {"e", "E"}:
                    exponent = end + 1
                    if exponent < length and text[exponent] in {"+", "-"}:
                        exponent += 1
                    digits_start = exponent
                    while exponent < length and (text[exponent].isdigit() or text[exponent] == "_"):
                        exponent += 1
                    if exponent > digits_start:
                        end = exponent
                if end < length and text[end] == "n":
                    end += 1
            tokens.append("<number>")
            index = end
            continue
        if _identifier_start(character):
            end = index + 1
            while end < length and _identifier_continue(text[end]):
                end += 1
            value = text[index:end]
            tokens.append(value if mode == "lexical_content" or value in _KEYWORDS else "<id>")
            index = end
            continue
        operator = next((item for item in _OPERATORS if text.startswith(item, index)), None)
        if operator is not None:
            tokens.append(operator)
            index += len(operator)
            continue
        if ord(character) < 32 or ord(character) == 127:
            tokens.append(f"<control-{ord(character):02x}>")
            issues.add("unexpected_control_character")
        else:
            tokens.append(character)
        index += 1
    return _Tokenization(tokens=tuple(tokens), issues=tuple(sorted(issues)))


def tokenize_arkts_like(
    source: str,
    *,
    mode: Literal["lexical_content", "lexical_shape"],
) -> tuple[str, ...]:
    """Return the frozen deterministic token stream used by the shadow policy."""

    return _tokenize_arkts_like(source, mode=mode).tokens


def _shingles(tokens: tuple[str, ...], width: int) -> frozenset[tuple[str, ...]]:
    if len(tokens) < width:
        return frozenset()
    return frozenset(
        tuple(tokens[index : index + width]) for index in range(len(tokens) - width + 1)
    )


@dataclass(frozen=True, slots=True)
class _SuffixAutomaton:
    transitions: tuple[Mapping[str, int], ...]
    suffix_links: tuple[int, ...]
    state_lengths: tuple[int, ...]


def _build_suffix_automaton(tokens: tuple[str, ...]) -> _SuffixAutomaton:
    transitions: list[dict[str, int]] = [{}]
    suffix_links = [-1]
    state_lengths = [0]
    last = 0
    for token in tokens:
        current = len(transitions)
        transitions.append({})
        suffix_links.append(0)
        state_lengths.append(state_lengths[last] + 1)
        predecessor = last
        while predecessor >= 0 and token not in transitions[predecessor]:
            transitions[predecessor][token] = current
            predecessor = suffix_links[predecessor]
        if predecessor < 0:
            suffix_links[current] = 0
        else:
            target = transitions[predecessor][token]
            if state_lengths[predecessor] + 1 == state_lengths[target]:
                suffix_links[current] = target
            else:
                clone = len(transitions)
                transitions.append(dict(transitions[target]))
                suffix_links.append(suffix_links[target])
                state_lengths.append(state_lengths[predecessor] + 1)
                while predecessor >= 0 and transitions[predecessor].get(token) == target:
                    transitions[predecessor][token] = clone
                    predecessor = suffix_links[predecessor]
                suffix_links[target] = clone
                suffix_links[current] = clone
        last = current
    return _SuffixAutomaton(
        transitions=tuple(transitions),
        suffix_links=tuple(suffix_links),
        state_lengths=tuple(state_lengths),
    )


def _scan_longest_contiguous_run(
    automaton: _SuffixAutomaton,
    reference: tuple[str, ...],
) -> int:
    state = 0
    current_length = 0
    longest = 0
    for token in reference:
        while state and token not in automaton.transitions[state]:
            state = automaton.suffix_links[state]
            current_length = min(current_length, automaton.state_lengths[state])
        target = automaton.transitions[state].get(token)
        if target is None:
            state = 0
            current_length = 0
            continue
        state = target
        current_length += 1
        if current_length > longest:
            longest = current_length
    return longest


def _longest_contiguous_run(selected: tuple[str, ...], reference: tuple[str, ...]) -> int:
    if not selected or not reference:
        return 0
    return _scan_longest_contiguous_run(_build_suffix_automaton(selected), reference)


class ReferenceDocument(_FrozenModel):
    role: ReferenceRole
    repository_source_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    path: str
    git_blob_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    content_sha256: Annotated[str, Field(pattern=_SHA256)]
    text: str

    @field_validator("repository_source_id")
    @classmethod
    def validate_repository_source_id(cls, value: str) -> str:
        return _single_line(value, "reference repository source ID")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value, "reference path")

    @model_validator(mode="after")
    def validate_text_hash(self) -> ReferenceDocument:
        if bytes_hash(self.text.encode("utf-8")) != self.content_sha256:
            raise ValueError("reference content SHA-256 does not match text")
        return self


class ReferenceInventorySummary(_FrozenModel):
    role: Literal["candidate_project", "exposure", "development_truth"]
    repository_source_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    scope: Literal["entire_tracked_tree", "registered_paths"]
    requested_paths: tuple[str, ...]
    requested_path_count: Annotated[int, Field(ge=0)]
    total_entry_count: Annotated[int, Field(ge=0)]
    regular_blob_count: Annotated[int, Field(ge=0)]
    unique_blob_count: Annotated[int, Field(ge=0)]
    utf8_text_count: Annotated[int, Field(ge=0)]
    binary_count: Annotated[int, Field(ge=0)]
    non_utf8_count: Annotated[int, Field(ge=0)]
    empty_text_count: Annotated[int, Field(ge=0)]
    oversize_count: Annotated[int, Field(ge=0)]
    budget_skipped_count: Annotated[int, Field(ge=0)]
    loaded_unique_blob_bytes: Annotated[int, Field(ge=0)]
    symlink_count: Annotated[int, Field(ge=0)]
    gitlink_count: Annotated[int, Field(ge=0)]
    other_nonregular_count: Annotated[int, Field(ge=0)]
    scanned_document_count: Annotated[int, Field(ge=0)]
    inventory_issues: tuple[str, ...]
    scope_entry_fingerprint: Annotated[str, Field(pattern=_INVENTORY_SCOPE_FINGERPRINT)]
    document_set_fingerprint: Annotated[str, Field(pattern=_DOCUMENT_SET_FINGERPRINT)]
    inventory_fingerprint: Annotated[str, Field(pattern=_INVENTORY_FINGERPRINT)]

    @field_validator("repository_source_id")
    @classmethod
    def validate_repository_source_id(cls, value: str) -> str:
        return _single_line(value, "inventory repository source ID")

    @field_validator("requested_paths", "inventory_issues", mode="before")
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"reference inventory {getattr(info, 'field_name', '')}")

    @field_validator("requested_paths")
    @classmethod
    def validate_requested_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _sorted_unique(value, "reference inventory requested paths")
        for path in normalized:
            _relative_path(path, "reference inventory requested path")
        return normalized

    @field_validator("inventory_issues")
    @classmethod
    def validate_issues(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, "reference inventory issues")

    @model_validator(mode="after")
    def validate_summary(self) -> ReferenceInventorySummary:
        if self.requested_path_count != len(self.requested_paths):
            raise ValueError("requested path count does not match requested paths")
        if self.scope == "entire_tracked_tree":
            if self.requested_paths:
                raise ValueError("entire-tree inventory cannot contain requested paths")
        elif not self.requested_paths or self.total_entry_count != len(self.requested_paths):
            raise ValueError("registered-path inventory must bind every requested path")
        if self.scanned_document_count != self.utf8_text_count:
            raise ValueError("scanned document count must equal UTF-8 text count")
        classified_regular = (
            self.utf8_text_count
            + self.binary_count
            + self.non_utf8_count
            + self.oversize_count
            + self.budget_skipped_count
        )
        if self.regular_blob_count != classified_regular:
            raise ValueError("regular reference entry classifications are incomplete")
        classified_entries = (
            self.regular_blob_count
            + self.symlink_count
            + self.gitlink_count
            + self.other_nonregular_count
        )
        if self.total_entry_count != classified_entries:
            raise ValueError("reference entry classifications are incomplete")
        if self.unique_blob_count > self.regular_blob_count:
            raise ValueError("unique blob count cannot exceed regular blob count")
        expected_issues: list[str] = []
        for count, issue in (
            (self.non_utf8_count, "non_utf8_entries"),
            (self.oversize_count, "oversize_entries"),
            (self.budget_skipped_count, "reference_byte_budget_exceeded"),
            (self.symlink_count, "symlink_entries"),
            (self.gitlink_count, "gitlink_entries"),
            (self.other_nonregular_count, "other_nonregular_entries"),
        ):
            if count:
                expected_issues.append(issue)
        if self.inventory_issues != tuple(sorted(expected_issues)):
            raise ValueError("reference inventory issues do not match entry counts")
        expected = canonical_hash(
            "tag-truth-reference-inventory",
            _identity_payload(self, "inventory_fingerprint"),
        )
        if self.inventory_fingerprint != expected:
            raise ValueError("reference inventory fingerprint does not match its fields")
        return self


@dataclass(frozen=True, slots=True)
class ScannedReferenceInventory:
    summary: ReferenceInventorySummary
    documents: tuple[ReferenceDocument, ...]

    def __post_init__(self) -> None:
        if len(self.documents) != self.summary.scanned_document_count:
            raise ValueError("reference documents do not match inventory summary")
        if any(document.role != self.summary.role for document in self.documents):
            raise ValueError("reference document role differs from inventory")
        if any(
            (
                document.repository_source_id,
                document.revision,
                document.tree_id,
            )
            != (
                self.summary.repository_source_id,
                self.summary.revision,
                self.summary.tree_id,
            )
            for document in self.documents
        ):
            raise ValueError("reference document provenance differs from inventory")
        if self.documents != tuple(sorted(self.documents, key=lambda item: item.path)):
            raise ValueError("reference documents must use deterministic path ordering")
        document_paths = tuple(document.path for document in self.documents)
        if len(document_paths) != len(set(document_paths)):
            raise ValueError("reference document paths must be unique")
        if self.summary.scope == "registered_paths" and not set(document_paths).issubset(
            self.summary.requested_paths
        ):
            raise ValueError("reference documents escape the requested path scope")
        document_payload = [
            {
                "role": document.role,
                "repository_source_id": document.repository_source_id,
                "revision": document.revision,
                "tree_id": document.tree_id,
                "path": document.path,
                "git_blob_id": document.git_blob_id,
                "content_sha256": document.content_sha256,
            }
            for document in self.documents
        ]
        expected = canonical_hash("tag-truth-reference-documents", document_payload)
        if self.summary.document_set_fingerprint != expected:
            raise ValueError("reference documents differ from inventory fingerprint")


ReferenceInventory = ScannedReferenceInventory


def _git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_LITERAL_PATHSPECS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def _run_git_bytes(root: Path, *arguments: str, timeout: int = 60) -> bytes:
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.commitGraph=false",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                *arguments,
            ],
            check=False,
            capture_output=True,
            timeout=timeout,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot inspect pinned Git inventory: {' '.join(arguments)}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(
            f"pinned Git inventory failed ({' '.join(arguments)}): {detail or 'git failed'}"
        )
    return completed.stdout


def _run_git_text(root: Path, *arguments: str) -> str:
    try:
        return _run_git_bytes(root, *arguments).decode("utf-8").strip()
    except UnicodeError as exc:
        raise ValueError("Git returned non-UTF-8 inventory metadata") from exc


def _resolve_git_root(root: str | Path) -> Path:
    try:
        resolved = Path(root).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Git inventory root is unavailable: {root}") from exc
    if not resolved.is_dir():
        raise ValueError("Git inventory root must be a directory")
    top = Path(_run_git_text(resolved, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != resolved:
        raise ValueError("Git inventory root must be the repository top level")
    return resolved


def _reject_git_grafts(root: Path) -> None:
    common_value = _run_git_text(root, "rev-parse", "--git-common-dir")
    common = Path(common_value)
    if not common.is_absolute():
        common = root / common
    try:
        common = common.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Git inventory common directory is unavailable") from exc
    grafts = common / "info" / "grafts"
    try:
        grafts.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError("cannot inspect Git inventory grafts") from exc
    raise ValueError("legacy Git grafts are forbidden for near-duplicate inventories")


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    mode: str
    kind: str
    object_id: str
    path: str


def _tree_entries(root: Path, revision: str) -> tuple[_TreeEntry, ...]:
    raw = _run_git_bytes(root, "ls-tree", "-r", "-z", "--full-tree", revision, timeout=120)
    entries: list[_TreeEntry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Git inventory contains an invalid tree entry") from exc
        _relative_path(path, "Git inventory path")
        if re.fullmatch(_GIT_OBJECT_ID, object_id) is None:
            raise ValueError("Git inventory contains an invalid object identity")
        entries.append(_TreeEntry(mode=mode, kind=kind, object_id=object_id, path=path))
    ordered = tuple(sorted(entries, key=lambda item: item.path))
    if len({entry.path for entry in ordered}) != len(ordered):
        raise ValueError("Git inventory contains duplicate paths")
    return ordered


def scan_pinned_git_reference_inventory(
    root: str | Path,
    *,
    role: Literal["candidate_project", "exposure", "development_truth"],
    repository_source_id: str,
    revision: str,
    expected_tree_id: str | None,
    included_paths: Sequence[str] | None,
    maximum_blob_bytes: int,
    maximum_total_reference_bytes: int = 256 * 1024 * 1024,
    maximum_inventory_entries: int = 100_000,
) -> ScannedReferenceInventory:
    """Read a deterministic reference inventory only from pinned Git objects."""

    if re.fullmatch(_GIT_OBJECT_ID, revision) is None:
        raise ValueError("reference inventory revision must be a full lowercase Git identity")
    _single_line(repository_source_id, "reference repository source ID")
    if maximum_blob_bytes < 1024:
        raise ValueError("maximum reference blob bytes must be at least 1024")
    if maximum_total_reference_bytes < 1024:
        raise ValueError("maximum total reference bytes must be at least 1024")
    if maximum_inventory_entries < 1:
        raise ValueError("maximum inventory entries must be positive")
    git_root = _resolve_git_root(root)
    _reject_git_grafts(git_root)
    resolved_revision = _run_git_text(
        git_root,
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
    )
    if resolved_revision != revision:
        raise ValueError("reference inventory revision does not resolve to its exact commit")
    tree_id = _run_git_text(git_root, "rev-parse", "--verify", f"{revision}^{{tree}}")
    if re.fullmatch(_GIT_OBJECT_ID, tree_id) is None:
        raise ValueError("reference inventory tree identity is invalid")
    if expected_tree_id is not None and tree_id != expected_tree_id:
        raise ValueError("reference inventory tree identity drift")

    all_entries = _tree_entries(git_root, revision)
    by_path = {entry.path: entry for entry in all_entries}
    if included_paths is None:
        entries = all_entries
        requested_paths: tuple[str, ...] = ()
        scope: Literal["entire_tracked_tree", "registered_paths"] = "entire_tracked_tree"
    else:
        requested_paths = tuple(
            sorted(_relative_path(path, "included reference path") for path in included_paths)
        )
        if not requested_paths or requested_paths != tuple(sorted(set(requested_paths))):
            raise ValueError("included reference paths must be non-empty and unique")
        missing = tuple(path for path in requested_paths if path not in by_path)
        if missing:
            raise ValueError(f"included reference paths are absent at pinned revision: {missing!r}")
        entries = tuple(by_path[path] for path in requested_paths)
        scope = "registered_paths"
    if len(entries) > maximum_inventory_entries:
        raise ValueError("reference inventory exceeds the frozen entry limit")

    regular_entries = tuple(
        entry for entry in entries if entry.kind == "blob" and entry.mode in {"100644", "100755"}
    )
    symlink_count = sum(entry.kind == "blob" and entry.mode == "120000" for entry in entries)
    gitlink_count = sum(entry.kind == "commit" or entry.mode == "160000" for entry in entries)
    other_nonregular_count = len(entries) - len(regular_entries) - symlink_count - gitlink_count

    raw_by_blob: dict[str, bytes | None] = {}
    budget_skipped_blobs: set[str] = set()
    loaded_unique_blob_bytes = 0
    for entry in regular_entries:
        if entry.object_id not in raw_by_blob:
            size_text = _run_git_text(git_root, "cat-file", "-s", entry.object_id)
            try:
                size = int(size_text)
            except ValueError as exc:
                raise ValueError("Git returned an invalid reference blob size") from exc
            if size < 0:
                raise ValueError("Git returned a negative reference blob size")
            if size > maximum_blob_bytes:
                raw_by_blob[entry.object_id] = None
            elif loaded_unique_blob_bytes + size > maximum_total_reference_bytes:
                raw_by_blob[entry.object_id] = None
                budget_skipped_blobs.add(entry.object_id)
            else:
                raw_by_blob[entry.object_id] = _run_git_bytes(
                    git_root,
                    "cat-file",
                    "blob",
                    entry.object_id,
                    timeout=120,
                )
                loaded_unique_blob_bytes += size

    binary_count = 0
    non_utf8_count = 0
    empty_text_count = 0
    oversize_count = 0
    budget_skipped_count = 0
    documents: list[ReferenceDocument] = []
    for entry in regular_entries:
        raw = raw_by_blob[entry.object_id]
        if raw is None:
            if entry.object_id in budget_skipped_blobs:
                budget_skipped_count += 1
            else:
                oversize_count += 1
            continue
        if b"\0" in raw:
            binary_count += 1
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            non_utf8_count += 1
            continue
        if not text:
            empty_text_count += 1
        documents.append(
            ReferenceDocument(
                role=role,
                repository_source_id=repository_source_id,
                revision=revision,
                tree_id=tree_id,
                path=entry.path,
                git_blob_id=entry.object_id,
                content_sha256=bytes_hash(raw),
                text=text,
            )
        )

    issues: list[str] = []
    if non_utf8_count:
        issues.append("non_utf8_entries")
    if oversize_count:
        issues.append("oversize_entries")
    if budget_skipped_count:
        issues.append("reference_byte_budget_exceeded")
    if symlink_count:
        issues.append("symlink_entries")
    if gitlink_count:
        issues.append("gitlink_entries")
    if other_nonregular_count:
        issues.append("other_nonregular_entries")
    summary_payload: dict[str, object] = {
        "role": role,
        "repository_source_id": repository_source_id,
        "revision": revision,
        "tree_id": tree_id,
        "scope": scope,
        "requested_paths": list(requested_paths),
        "requested_path_count": len(requested_paths),
        "total_entry_count": len(entries),
        "regular_blob_count": len(regular_entries),
        "unique_blob_count": len(raw_by_blob),
        "utf8_text_count": len(documents),
        "binary_count": binary_count,
        "non_utf8_count": non_utf8_count,
        "empty_text_count": empty_text_count,
        "oversize_count": oversize_count,
        "budget_skipped_count": budget_skipped_count,
        "loaded_unique_blob_bytes": loaded_unique_blob_bytes,
        "symlink_count": symlink_count,
        "gitlink_count": gitlink_count,
        "other_nonregular_count": other_nonregular_count,
        "scanned_document_count": len(documents),
        "inventory_issues": sorted(issues),
        "scope_entry_fingerprint": canonical_hash(
            "tag-truth-reference-scope",
            [
                {
                    "path": entry.path,
                    "mode": entry.mode,
                    "kind": entry.kind,
                    "object_id": entry.object_id,
                }
                for entry in entries
            ],
        ),
        "document_set_fingerprint": canonical_hash(
            "tag-truth-reference-documents",
            [
                {
                    "role": document.role,
                    "repository_source_id": document.repository_source_id,
                    "revision": document.revision,
                    "tree_id": document.tree_id,
                    "path": document.path,
                    "git_blob_id": document.git_blob_id,
                    "content_sha256": document.content_sha256,
                }
                for document in documents
            ],
        ),
    }
    summary_payload["inventory_fingerprint"] = canonical_hash(
        "tag-truth-reference-inventory",
        summary_payload,
    )
    summary = ReferenceInventorySummary.model_validate(summary_payload)
    return ScannedReferenceInventory(summary=summary, documents=tuple(documents))


class SimilarityScores(_FrozenModel):
    selected_content_token_count: Annotated[int, Field(ge=0)]
    reference_content_token_count: Annotated[int, Field(ge=0)]
    selected_content_shingle_count: Annotated[int, Field(ge=0)]
    reference_content_shingle_count: Annotated[int, Field(ge=0)]
    shared_content_shingle_count: Annotated[int, Field(ge=0)]
    content_union_shingle_count: Annotated[int, Field(ge=0)]
    selected_shape_shingle_count: Annotated[int, Field(ge=0)]
    reference_shape_shingle_count: Annotated[int, Field(ge=0)]
    shared_shape_shingle_count: Annotated[int, Field(ge=0)]
    longest_contiguous_token_run: Annotated[int, Field(ge=0)]
    normalized_token_stream_equal: bool
    normalized_shape_token_stream_equal: bool

    @model_validator(mode="after")
    def validate_scores(self) -> SimilarityScores:
        if self.shared_content_shingle_count > min(
            self.selected_content_shingle_count,
            self.reference_content_shingle_count,
        ):
            raise ValueError("shared content shingles exceed either input")
        expected_union = (
            self.selected_content_shingle_count
            + self.reference_content_shingle_count
            - self.shared_content_shingle_count
        )
        if self.content_union_shingle_count != expected_union:
            raise ValueError("content shingle union count is inconsistent")
        if self.shared_shape_shingle_count > min(
            self.selected_shape_shingle_count,
            self.reference_shape_shingle_count,
        ):
            raise ValueError("shared shape shingles exceed either input")
        if self.longest_contiguous_token_run > min(
            self.selected_content_token_count,
            self.reference_content_token_count,
        ):
            raise ValueError("contiguous token run exceeds either input")
        return self


class NearDuplicateMatch(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    axis: ScreeningAxis
    reference_role: ReferenceRole
    reference_axis: Literal["file", "unit"]
    reference_repository_source_id: Annotated[str, Field(min_length=1)]
    reference_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    reference_path: Annotated[str, Field(min_length=1)]
    reference_case_id: Annotated[str, Field(pattern=_CASE_ID)] | None = None
    reference_content_sha256: Annotated[str, Field(pattern=_SHA256)]
    decision: Literal["duplicate", "gray"]
    signals: tuple[SimilaritySignal, ...]
    scores: SimilarityScores

    @field_validator("reference_repository_source_id", "reference_path")
    @classmethod
    def validate_single_line(cls, value: str, info: object) -> str:
        return _single_line(value, f"near-duplicate match {getattr(info, 'field_name', '')}")

    @field_validator("signals", mode="before")
    @classmethod
    def parse_signals(cls, value: object) -> tuple[object, ...]:
        return _sequence(value, "near-duplicate match signals")

    @field_validator("signals")
    @classmethod
    def validate_signals(
        cls,
        value: tuple[SimilaritySignal, ...],
    ) -> tuple[SimilaritySignal, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("near-duplicate match signals must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_signal_scores(self) -> NearDuplicateMatch:
        content_signals = {
            "content_containment",
            "reference_content_containment",
            "content_jaccard",
        }
        shape_signals = {
            "shape_containment",
            "reference_shape_containment",
        }
        contiguous_signals = {
            "contiguous_token_run",
            "contiguous_reference_coverage",
        }
        signal_set = set(self.signals)
        if (
            "normalized_token_stream_equal" in signal_set
            and not self.scores.normalized_token_stream_equal
        ):
            raise ValueError("exact-token signal requires equal normalized token streams")
        if (
            "normalized_shape_token_stream_equal" in signal_set
            and not self.scores.normalized_shape_token_stream_equal
        ):
            raise ValueError("shape-equality signal requires equal normalized shape streams")
        if (
            signal_set.intersection(content_signals)
            and not self.scores.shared_content_shingle_count
        ):
            raise ValueError("content similarity signal requires shared content shingles")
        if signal_set.intersection(shape_signals) and not self.scores.shared_shape_shingle_count:
            raise ValueError("shape similarity signal requires shared shape shingles")
        if (
            signal_set.intersection(contiguous_signals)
            and not self.scores.longest_contiguous_token_run
        ):
            raise ValueError("contiguous signal requires a non-empty contiguous run")
        gray_only = {*shape_signals, "normalized_shape_token_stream_equal"}
        if self.decision == "duplicate" and signal_set.intersection(gray_only):
            raise ValueError("shape-only signals cannot hard-reject a sample")
        if self.decision == "gray" and "normalized_token_stream_equal" in signal_set:
            raise ValueError("equal normalized content streams require duplicate decision")
        if self.reference_role == "campaign_peer":
            if self.reference_case_id is None:
                raise ValueError("campaign-peer match requires a reference case")
            if self.reference_case_id == self.case_id:
                raise ValueError("campaign case cannot match itself")
        elif self.reference_case_id is not None or self.reference_axis != "file":
            raise ValueError("inventory match cannot claim campaign case or Unit identity")
        return self


class NearDuplicateCaseResult(_FrozenModel):
    case_id: Annotated[str, Field(pattern=_CASE_ID)]
    probe_evaluation_status: Literal["evaluated", "not_run_resource_limit"]
    file_content_sha256: Annotated[str, Field(pattern=_SHA256)]
    unit_content_sha256: Annotated[str, Field(pattern=_SHA256)] | None
    unit_start_line: Annotated[int, Field(ge=1)] | None
    unit_end_line: Annotated[int, Field(ge=1)] | None
    file_content_token_count: Annotated[int, Field(ge=0)]
    unit_content_token_count: Annotated[int, Field(ge=0)]
    file_content_shingle_count: Annotated[int, Field(ge=0)]
    unit_content_shingle_count: Annotated[int, Field(ge=0)]
    file_tokenization_issues: tuple[str, ...]
    unit_tokenization_issues: tuple[str, ...]
    file_decision: AxisDecision
    unit_decision: AxisDecision
    overall_decision: AxisDecision
    blockers: tuple[str, ...]
    hard_match_count: Annotated[int, Field(ge=0)]
    gray_match_count: Annotated[int, Field(ge=0)]

    @field_validator(
        "file_tokenization_issues",
        "unit_tokenization_issues",
        "blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"case result {getattr(info, 'field_name', '')}")

    @field_validator("file_tokenization_issues", "unit_tokenization_issues", "blockers")
    @classmethod
    def validate_sequences(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _sorted_unique(value, f"case result {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_case_result(self) -> NearDuplicateCaseResult:
        span_values = (self.unit_start_line, self.unit_end_line)
        if self.unit_content_sha256 is None:
            if span_values != (None, None) or self.unit_content_token_count != 0:
                raise ValueError("unresolved ReviewUnit cannot contain Unit identity")
        elif self.unit_start_line is None or self.unit_end_line is None:
            raise ValueError("resolved ReviewUnit requires a complete line span")
        elif self.unit_start_line > self.unit_end_line:
            raise ValueError("ReviewUnit start line cannot exceed end line")
        expected_overall: AxisDecision
        if "duplicate" in {self.file_decision, self.unit_decision}:
            expected_overall = "duplicate"
        elif "gray" in {self.file_decision, self.unit_decision}:
            expected_overall = "gray"
        elif "abstain" in {self.file_decision, self.unit_decision}:
            expected_overall = "abstain"
        else:
            expected_overall = "clear"
        if self.overall_decision != expected_overall:
            raise ValueError("case overall decision does not match its axes")
        if self.probe_evaluation_status == "not_run_resource_limit":
            if any(
                (
                    self.file_content_token_count,
                    self.unit_content_token_count,
                    self.file_content_shingle_count,
                    self.unit_content_shingle_count,
                    self.file_tokenization_issues,
                    self.unit_tokenization_issues,
                    self.hard_match_count,
                    self.gray_match_count,
                )
            ):
                raise ValueError("non-evaluated probes cannot contain measured token results")
            if self.file_decision != "abstain" or self.unit_decision != "abstain":
                raise ValueError("non-evaluated probes must abstain on both axes")
        return self


class _TagTruthV2NearDuplicateVerificationPayload(_FrozenModel):
    schema_version: Literal["tag-truth-v2-near-duplicate-screening-v1"]
    policy_fingerprint: Annotated[str, Field(pattern=_POLICY_FINGERPRINT)]
    policy_approval_status: Literal["snapshot_only_not_approved"]
    provenance_verification_id: Annotated[
        str,
        Field(pattern=r"^tag-truth-provenance-verification:sha256:[0-9a-f]{64}$"),
    ]
    seal_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    seal_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    candidate_commit: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    candidate_project_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    source_repository_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    source_repository_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    exposure_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    exposure_tree_id: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    development_truth_revision: Annotated[str, Field(pattern=_GIT_OBJECT_ID)]
    development_truth_suite_fingerprint: Annotated[
        str,
        Field(pattern=r"^tag-retrieval-truth:sha256:[0-9a-f]{64}$"),
    ]
    selection_id: Annotated[str, Field(pattern=r"^tag-truth-selection:sha256:[0-9a-f]{64}$")]
    packet_id: Annotated[str, Field(pattern=r"^tag-truth-review-packet:sha256:[0-9a-f]{64}$")]
    receipt_ids: tuple[
        Annotated[str, Field(pattern=r"^tag-truth-review-receipt:sha256:[0-9a-f]{64}$")],
        ...,
    ]
    consensus_id: Annotated[str, Field(pattern=r"^tag-truth-consensus:sha256:[0-9a-f]{64}$")]
    reference_inventories: tuple[ReferenceInventorySummary, ...]
    cases: tuple[NearDuplicateCaseResult, ...]
    matches: tuple[NearDuplicateMatch, ...]
    reference_tokenization_issue_count: Annotated[int, Field(ge=0)]
    work_estimate_version: Literal["nfc-character-work-v1"]
    probe_count: Annotated[int, Field(ge=0)]
    selected_nfc_character_count: Annotated[int, Field(ge=0)]
    unique_reference_text_count: Annotated[int, Field(ge=0)]
    unique_reference_nfc_character_count: Annotated[int, Field(ge=0)]
    planned_similarity_pair_count: Annotated[int, Field(ge=0)]
    planned_similarity_pair_nfc_characters: Annotated[int, Field(ge=0)]
    attempted_similarity_pair_count: Annotated[int, Field(ge=0)]
    similarity_work_status: SimilarityWorkStatus
    similarity_work_blockers: tuple[SimilarityWorkBlocker, ...]
    duplicate_case_count: Annotated[int, Field(ge=0)]
    gray_case_count: Annotated[int, Field(ge=0)]
    abstain_case_count: Annotated[int, Field(ge=0)]
    clear_case_count: Annotated[int, Field(ge=0)]
    screening_blockers: tuple[str, ...]
    qualification_blockers: tuple[str, ...]
    screening_outcome: ScreeningOutcome
    near_duplicate_qualification_status: Literal[
        "not_qualified_policy_unapproved",
        "qualified",
    ]
    evidence_qualification_status: Literal["not_qualified"]
    candidate_execution_status: Literal["not_run"]

    @field_validator(
        "receipt_ids",
        "reference_inventories",
        "cases",
        "matches",
        "similarity_work_blockers",
        "screening_blockers",
        "qualification_blockers",
        mode="before",
    )
    @classmethod
    def parse_sequences(cls, value: object, info: object) -> tuple[object, ...]:
        return _sequence(value, f"screening {getattr(info, 'field_name', '')}")

    @model_validator(mode="after")
    def validate_payload(self) -> _TagTruthV2NearDuplicateVerificationPayload:
        if len(self.receipt_ids) != 2 or self.receipt_ids != tuple(sorted(set(self.receipt_ids))):
            raise ValueError("screening requires two sorted unique receipt IDs")
        expected_roles = ("candidate_project", "exposure", "development_truth")
        roles = tuple(item.role for item in self.reference_inventories)
        if roles != expected_roles:
            raise ValueError("screening requires the three canonical reference inventories")
        case_ids = tuple(item.case_id for item in self.cases)
        if not case_ids or case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("screening cases must be sorted and unique")
        expected_probe_count = len(self.cases) + sum(
            case.unit_content_sha256 is not None for case in self.cases
        )
        if self.probe_count != expected_probe_count:
            raise ValueError("screening probe count differs from case axes")
        inventory_document_count = sum(
            inventory.scanned_document_count for inventory in self.reference_inventories
        )
        resolved_unit_count = sum(case.unit_content_sha256 is not None for case in self.cases)
        expected_pair_count = len(self.cases) * (
            inventory_document_count + len(self.cases) - 1
        ) + resolved_unit_count * (
            inventory_document_count + len(self.cases) + resolved_unit_count - 2
        )
        if self.planned_similarity_pair_count != expected_pair_count:
            raise ValueError("planned similarity-pair count differs from report scope")
        match_keys = tuple(_match_sort_key(item) for item in self.matches)
        if match_keys != tuple(sorted(set(match_keys))):
            raise ValueError("screening matches must be sorted and unique")
        cases_by_id = {item.case_id: item for item in self.cases}
        matches_by_case: dict[str, list[NearDuplicateMatch]] = {
            case_id: [] for case_id in cases_by_id
        }
        for match in self.matches:
            case = cases_by_id.get(match.case_id)
            if case is None:
                raise ValueError("screening match references an unknown case")
            axis_decision = case.file_decision if match.axis == "file" else case.unit_decision
            if match.decision == "duplicate" and axis_decision != "duplicate":
                raise ValueError("hard match requires duplicate decision on its axis")
            if match.decision == "gray" and axis_decision not in {"gray", "duplicate"}:
                raise ValueError("gray match requires gray-or-harder decision on its axis")
            if match.reference_role == "campaign_peer":
                if (
                    match.reference_case_id not in cases_by_id
                    or match.reference_revision != self.source_repository_revision
                ):
                    raise ValueError("campaign-peer match differs from the sealed campaign")
            else:
                inventory = next(
                    item for item in self.reference_inventories if item.role == match.reference_role
                )
                if (
                    match.reference_repository_source_id,
                    match.reference_revision,
                ) != (inventory.repository_source_id, inventory.revision):
                    raise ValueError("inventory match differs from its reference provenance")
            matches_by_case[match.case_id].append(match)
        for case in self.cases:
            case_matches = matches_by_case[case.case_id]
            if case.hard_match_count != sum(
                match.decision == "duplicate" for match in case_matches
            ):
                raise ValueError("case hard-match count does not match screening matches")
            if case.gray_match_count != sum(match.decision == "gray" for match in case_matches):
                raise ValueError("case gray-match count does not match screening matches")
            for axis, decision in (
                ("file", case.file_decision),
                ("unit", case.unit_decision),
            ):
                axis_matches = tuple(match for match in case_matches if match.axis == axis)
                hard_count = sum(match.decision == "duplicate" for match in axis_matches)
                gray_count = sum(match.decision == "gray" for match in axis_matches)
                if decision == "duplicate" and not hard_count:
                    raise ValueError("duplicate axis requires at least one hard match")
                if decision == "gray" and (hard_count or not gray_count):
                    raise ValueError("gray axis requires gray matches and no hard match")
                if decision in {"clear", "abstain"} and (hard_count or gray_count):
                    raise ValueError("clear or abstain axis cannot contain a similarity match")
        if self.screening_blockers != tuple(sorted(set(self.screening_blockers))):
            raise ValueError("screening blockers must be sorted and unique")
        if self.similarity_work_blockers != tuple(sorted(set(self.similarity_work_blockers))):
            raise ValueError("similarity-work blockers must be sorted and unique")
        if self.qualification_blockers != tuple(sorted(set(self.qualification_blockers))):
            raise ValueError("qualification blockers must be sorted and unique")
        inventory_blockers = {
            f"{inventory.role}:{issue}"
            for inventory in self.reference_inventories
            for issue in inventory.inventory_issues
        }
        development_inventory = self.reference_inventories[2]
        if (
            development_inventory.scanned_document_count
            < development_inventory.requested_path_count
        ):
            inventory_blockers.add("development_truth:registered_path_not_evaluated")
        if self.reference_tokenization_issue_count:
            inventory_blockers.add("reference_tokenization_issues")
        elif "reference_tokenization_issues" in self.screening_blockers:
            raise ValueError("reference-tokenization blocker requires a positive issue count")
        work_blockers = set(self.similarity_work_blockers)
        precomparison_work_blockers = {
            "selected_character_budget_exceeded",
            "unique_reference_character_budget_exceeded",
            "similarity_pair_budget_exceeded",
            "similarity_character_budget_exceeded",
        }
        case_probe_statuses = {case.probe_evaluation_status for case in self.cases}
        if self.attempted_similarity_pair_count < len(self.matches):
            raise ValueError("screening cannot record more matches than attempted pairs")
        if self.similarity_work_status == "evaluated":
            if work_blockers:
                raise ValueError("evaluated similarity work cannot contain resource blockers")
            if self.attempted_similarity_pair_count != self.planned_similarity_pair_count:
                raise ValueError("evaluated similarity work must execute every planned pair")
            if case_probe_statuses != {"evaluated"}:
                raise ValueError("evaluated similarity work requires evaluated probes")
        elif work_blockers == {"recorded_match_budget_exceeded"}:
            if not 0 < self.attempted_similarity_pair_count <= self.planned_similarity_pair_count:
                raise ValueError("recorded-match overflow requires attempted planned work")
            if case_probe_statuses != {"evaluated"}:
                raise ValueError("runtime match overflow requires evaluated probes")
        else:
            if not work_blockers or not work_blockers.issubset(precomparison_work_blockers):
                raise ValueError("resource abstention has an invalid work-blocker state")
            if self.attempted_similarity_pair_count:
                raise ValueError("pre-comparison resource abstention cannot attempt pairs")
            if case_probe_statuses != {"not_run_resource_limit"}:
                raise ValueError("pre-comparison resource abstention requires not-run probes")
        inventory_blockers.update(work_blockers)
        if not inventory_blockers.issubset(self.screening_blockers):
            raise ValueError("screening blockers omit reference inventory failures")
        if set(self.screening_blockers).difference(inventory_blockers) - {"consensus_unresolved"}:
            raise ValueError("screening report contains an unknown blocker")
        reference_incomplete = bool(
            work_blockers
            or self.reference_tokenization_issue_count
            or any(inventory.inventory_issues for inventory in self.reference_inventories)
            or development_inventory.scanned_document_count
            < development_inventory.requested_path_count
        )
        if reference_incomplete and any(
            decision == "clear"
            for case in self.cases
            for decision in (case.file_decision, case.unit_decision)
        ):
            raise ValueError("incomplete reference evaluation cannot produce a clear axis")
        if self.similarity_work_status == "abstained_resource_limit" and self.matches:
            raise ValueError("resource-abstained similarity work cannot contain matches")
        counts = {
            "duplicate": self.duplicate_case_count,
            "gray": self.gray_case_count,
            "abstain": self.abstain_case_count,
            "clear": self.clear_case_count,
        }
        actual = {
            decision: sum(item.overall_decision == decision for item in self.cases)
            for decision in counts
        }
        if counts != actual:
            raise ValueError("screening aggregate case counts do not match cases")
        expected_outcome: ScreeningOutcome
        if self.duplicate_case_count:
            expected_outcome = "potential_duplicate"
        elif self.gray_case_count or self.abstain_case_count or self.screening_blockers:
            expected_outcome = "review_required"
        else:
            expected_outcome = "clean"
        if self.screening_outcome != expected_outcome:
            raise ValueError("screening outcome does not match cases and blockers")
        if self.policy_approval_status == "snapshot_only_not_approved":
            if self.near_duplicate_qualification_status != "not_qualified_policy_unapproved":
                raise ValueError("unapproved shadow policy cannot qualify near duplicates")
            required = ("calibration_truth_unavailable", "policy_not_approved")
            if self.qualification_blockers != required:
                raise ValueError("shadow screening must preserve policy/calibration blockers")
        return self


class TagTruthV2NearDuplicateVerification(_TagTruthV2NearDuplicateVerificationPayload):
    screening_id: Annotated[str, Field(pattern=_SCREENING_ID)]

    @model_validator(mode="after")
    def validate_screening_id(self) -> TagTruthV2NearDuplicateVerification:
        expected = canonical_hash(
            "tag-truth-near-duplicate-screening",
            _identity_payload(self, "screening_id"),
        )
        if self.screening_id != expected:
            raise ValueError("near-duplicate screening ID does not match its complete report")
        return self


def near_duplicate_verification_payload_with_id(
    payload: Mapping[str, object],
) -> dict[str, object]:
    if "screening_id" in payload:
        raise ValueError("unsealed near-duplicate report cannot contain screening_id")
    canonical_payload = _TagTruthV2NearDuplicateVerificationPayload.model_validate_json(
        canonical_json(dict(payload))
    )
    result = canonical_payload.model_dump(mode="json")
    result["screening_id"] = canonical_hash(
        "tag-truth-near-duplicate-screening",
        result,
    )
    return result


@dataclass(frozen=True, slots=True)
class _Probe:
    case_id: str
    axis: ScreeningAxis
    text: str
    content: _Tokenization
    shape: _Tokenization
    content_shingles: frozenset[tuple[str, ...]]
    shape_shingles: frozenset[tuple[str, ...]]
    content_automaton: _SuffixAutomaton


@dataclass(frozen=True, slots=True)
class _PreparedReferenceText:
    content: _Tokenization
    shape: _Tokenization
    content_shingles: frozenset[tuple[str, ...]]
    shape_shingles: frozenset[tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class _ReferenceView:
    role: ReferenceRole
    reference_axis: Literal["file", "unit"]
    repository_source_id: str
    revision: str
    path: str
    case_id: str | None
    content_sha256: str
    text: str


def _probe(
    case_id: str,
    axis: ScreeningAxis,
    text: str,
    policy: TagTruthV2NearDuplicatePolicy,
) -> _Probe:
    content = _tokenize_arkts_like(text, mode="lexical_content")
    shape = _tokenize_arkts_like(text, mode="lexical_shape")
    return _Probe(
        case_id=case_id,
        axis=axis,
        text=text,
        content=content,
        shape=shape,
        content_shingles=_shingles(content.tokens, policy.content_shingle_width),
        shape_shingles=_shingles(shape.tokens, policy.shape_shingle_width),
        content_automaton=_build_suffix_automaton(content.tokens),
    )


def _prepare_reference_text(
    text: str,
    policy: TagTruthV2NearDuplicatePolicy,
) -> _PreparedReferenceText:
    content = _tokenize_arkts_like(text, mode="lexical_content")
    shape = _tokenize_arkts_like(text, mode="lexical_shape")
    return _PreparedReferenceText(
        content=content,
        shape=shape,
        content_shingles=_shingles(content.tokens, policy.content_shingle_width),
        shape_shingles=_shingles(shape.tokens, policy.shape_shingle_width),
    )


def _similarity(
    selected: _Probe,
    reference_text: str | _PreparedReferenceText,
    policy: TagTruthV2NearDuplicatePolicy,
) -> tuple[AxisDecision, tuple[SimilaritySignal, ...], SimilarityScores, tuple[str, ...]]:
    prepared = (
        _prepare_reference_text(reference_text, policy)
        if isinstance(reference_text, str)
        else reference_text
    )
    reference_content = prepared.content
    reference_shape = prepared.shape
    reference_content_shingles = prepared.content_shingles
    reference_shape_shingles = prepared.shape_shingles
    shared_content = selected.content_shingles & reference_content_shingles
    shared_shape = selected.shape_shingles & reference_shape_shingles
    union_content = selected.content_shingles | reference_content_shingles
    longest = _scan_longest_contiguous_run(
        selected.content_automaton,
        reference_content.tokens,
    )
    exact = bool(selected.content.tokens) and selected.content.tokens == reference_content.tokens
    shape_exact = bool(selected.shape.tokens) and selected.shape.tokens == reference_shape.tokens
    scores = SimilarityScores(
        selected_content_token_count=len(selected.content.tokens),
        reference_content_token_count=len(reference_content.tokens),
        selected_content_shingle_count=len(selected.content_shingles),
        reference_content_shingle_count=len(reference_content_shingles),
        shared_content_shingle_count=len(shared_content),
        content_union_shingle_count=len(union_content),
        selected_shape_shingle_count=len(selected.shape_shingles),
        reference_shape_shingle_count=len(reference_shape_shingles),
        shared_shape_shingle_count=len(shared_shape),
        longest_contiguous_token_run=longest,
        normalized_token_stream_equal=exact,
        normalized_shape_token_stream_equal=shape_exact,
    )
    tokenization_issues = tuple(
        sorted(
            set(
                (
                    *selected.content.issues,
                    *selected.shape.issues,
                    *reference_content.issues,
                    *reference_shape.issues,
                )
            )
        )
    )
    if tokenization_issues:
        return "abstain", (), scores, tokenization_issues
    hard_signals: list[SimilaritySignal] = []
    if exact:
        hard_signals.append("normalized_token_stream_equal")
    if len(
        shared_content
    ) >= policy.hard_minimum_shared_content_shingles and policy.hard_content_containment.reached(
        len(shared_content),
        len(selected.content_shingles),
    ):
        hard_signals.append("content_containment")
    if len(
        shared_content
    ) >= policy.hard_minimum_shared_content_shingles and policy.hard_content_containment.reached(
        len(shared_content),
        len(reference_content_shingles),
    ):
        hard_signals.append("reference_content_containment")
    if len(
        shared_content
    ) >= policy.hard_minimum_shared_content_shingles and policy.hard_content_jaccard.reached(
        len(shared_content), len(union_content)
    ):
        hard_signals.append("content_jaccard")
    if (
        longest >= policy.hard_contiguous_minimum_tokens
        and policy.hard_contiguous_selected_coverage.reached(
            longest,
            len(selected.content.tokens),
        )
    ):
        hard_signals.append("contiguous_token_run")
    if (
        longest >= policy.hard_contiguous_minimum_tokens
        and policy.hard_contiguous_selected_coverage.reached(
            longest,
            len(reference_content.tokens),
        )
    ):
        hard_signals.append("contiguous_reference_coverage")
    if hard_signals:
        return "duplicate", tuple(sorted(set(hard_signals))), scores, ()

    gray_signals: list[SimilaritySignal] = []
    if shape_exact and len(selected.shape.tokens) >= policy.hard_contiguous_minimum_tokens:
        gray_signals.append("normalized_shape_token_stream_equal")
    if len(
        shared_content
    ) >= policy.gray_minimum_shared_content_shingles and policy.gray_content_containment.reached(
        len(shared_content),
        len(selected.content_shingles),
    ):
        gray_signals.append("content_containment")
    if len(
        shared_content
    ) >= policy.gray_minimum_shared_content_shingles and policy.gray_content_containment.reached(
        len(shared_content),
        len(reference_content_shingles),
    ):
        gray_signals.append("reference_content_containment")
    if len(
        shared_content
    ) >= policy.gray_minimum_shared_content_shingles and policy.gray_content_jaccard.reached(
        len(shared_content),
        len(union_content),
    ):
        gray_signals.append("content_jaccard")
    if len(
        shared_shape
    ) >= policy.gray_minimum_shared_shape_shingles and policy.gray_shape_containment.reached(
        len(shared_shape),
        len(selected.shape_shingles),
    ):
        gray_signals.append("shape_containment")
    if len(
        shared_shape
    ) >= policy.gray_minimum_shared_shape_shingles and policy.gray_shape_containment.reached(
        len(shared_shape),
        len(reference_shape_shingles),
    ):
        gray_signals.append("reference_shape_containment")
    if gray_signals:
        return "gray", tuple(sorted(set(gray_signals))), scores, ()
    return "clear", (), scores, ()


def _match_sort_key(match: NearDuplicateMatch) -> tuple[object, ...]:
    return (
        match.case_id,
        match.axis,
        _ROLE_ORDER[match.reference_role],
        match.reference_repository_source_id,
        match.reference_revision,
        match.reference_path,
        match.reference_axis,
        match.reference_case_id or "",
        match.reference_content_sha256,
    )


def _case_source_maps(
    selection: TagTruthV2Selection,
    packet: TagTruthV2ReviewPacket,
) -> tuple[dict[str, SelectionCase], dict[str, BlindReviewCase]]:
    selection_cases = {case.case_id: case for case in selection.cases}
    packet_cases = {case.case_id: case for case in packet.cases}
    if set(selection_cases) != set(packet_cases):
        raise ValueError("selection and packet case sets differ")
    return selection_cases, packet_cases


def _unit_text(source_text: str, start_line: int, end_line: int) -> str:
    lines = source_text.splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise ValueError("agreed ReviewUnit span exceeds packet source")
    return "".join(lines[start_line - 1 : end_line])


def build_tag_truth_v2_near_duplicate_verification(
    *,
    policy: TagTruthV2NearDuplicatePolicy,
    provenance: TagTruthV2ProvenanceVerification,
    selection: TagTruthV2Selection,
    packet: TagTruthV2ReviewPacket,
    consensus: TagTruthV2Consensus,
    reference_inventories: Sequence[ScannedReferenceInventory],
) -> TagTruthV2NearDuplicateVerification:
    policy = TagTruthV2NearDuplicatePolicy.model_validate_json(
        canonical_json(policy.model_dump(mode="json"))
    )
    provenance = TagTruthV2ProvenanceVerification.model_validate_json(
        canonical_json(provenance.model_dump(mode="json"))
    )
    selection = TagTruthV2Selection.model_validate_json(
        canonical_json(selection.model_dump(mode="json"))
    )
    packet = TagTruthV2ReviewPacket.model_validate_json(
        canonical_json(packet.model_dump(mode="json"))
    )
    consensus = TagTruthV2Consensus.model_validate_json(
        canonical_json(consensus.model_dump(mode="json"))
    )
    verify_tag_truth_v2_review_packet(packet, selection)
    expected_binding = (
        provenance.selection_id,
        provenance.packet_id,
        provenance.consensus_id,
        provenance.candidate_commit,
    )
    actual_binding = (
        selection.selection_id,
        packet.packet_id,
        consensus.consensus_id,
        selection.candidate_freeze.candidate_commit,
    )
    if actual_binding != expected_binding:
        raise ValueError("near-duplicate inputs do not bind the Stage-2C provenance chain")
    if consensus.selection_id != selection.selection_id or consensus.packet_id != packet.packet_id:
        raise ValueError("near-duplicate consensus differs from selection or packet")

    inventories = tuple(reference_inventories)
    ordered_inventories = tuple(
        sorted(inventories, key=lambda item: _ROLE_ORDER[item.summary.role])
    )
    expected_roles = ("candidate_project", "exposure", "development_truth")
    if tuple(item.summary.role for item in ordered_inventories) != expected_roles:
        raise ValueError("near-duplicate screening requires canonical reference inventories")
    candidate_inventory, exposure_inventory, development_inventory = ordered_inventories
    if (
        candidate_inventory.summary.repository_source_id != "arkts-code-reviewer"
        or candidate_inventory.summary.revision != provenance.candidate_commit
        or candidate_inventory.summary.scope != "entire_tracked_tree"
        or exposure_inventory.summary.repository_source_id != provenance.source_repository_source_id
        or exposure_inventory.summary.revision != provenance.exposure_revision
        or exposure_inventory.summary.tree_id != provenance.exposure_tree_id
        or exposure_inventory.summary.scope != "entire_tracked_tree"
        or development_inventory.summary.repository_source_id
        != provenance.source_repository_source_id
        or development_inventory.summary.scope != "registered_paths"
    ):
        raise ValueError("reference inventories differ from Stage-2C provenance")
    development_paths = development_inventory.summary.requested_paths
    evaluated_development_paths = tuple(
        document.path for document in development_inventory.documents
    )
    development_hashes = tuple(
        sorted({document.content_sha256 for document in development_inventory.documents})
    )
    if development_paths != selection.development_exclusions.source_paths:
        raise ValueError("development reference paths differ from selection exclusions")
    if not set(evaluated_development_paths).issubset(development_paths):
        raise ValueError("evaluated development paths escape the registered scope")
    if not set(development_hashes).issubset(selection.development_exclusions.content_sha256):
        raise ValueError("development reference hashes differ from selection exclusions")
    development_fully_evaluated = len(evaluated_development_paths) == len(development_paths)
    if (
        development_fully_evaluated
        and development_hashes != selection.development_exclusions.content_sha256
    ):
        raise ValueError("development reference hashes differ from selection exclusions")
    reference_inventory_incomplete = bool(
        not development_fully_evaluated
        or any(inventory.summary.inventory_issues for inventory in ordered_inventories)
    )

    selection_cases, packet_cases = _case_source_maps(selection, packet)
    sources_by_alias = {source.alias: source for source in selection.sources}
    consensus_cases = {case.case_id: case for case in consensus.cases}
    if set(consensus_cases) != set(packet_cases):
        raise ValueError("consensus case set differs from sealed packet")

    probe_texts: dict[tuple[str, ScreeningAxis], str] = {}
    unit_spans: dict[str, tuple[int, int] | None] = {}
    unit_texts: dict[str, str | None] = {}
    source_paths: dict[str, str] = {}
    for case_id in sorted(packet_cases):
        packet_case = packet_cases[case_id]
        selection_case = selection_cases[case_id]
        source = sources_by_alias.get(selection_case.source_alias)
        if source is None:
            raise ValueError("selection case references an unknown source alias")
        source_paths[case_id] = source.path
        probe_texts[(case_id, "file")] = packet_case.source_text
        consensus_case = consensus_cases[case_id]
        if consensus_case.review_unit_status == "agreed" and consensus_case.review_unit is not None:
            span = consensus_case.review_unit.source_span
            text = _unit_text(packet_case.source_text, span.start_line, span.end_line)
            unit_spans[case_id] = (span.start_line, span.end_line)
            unit_texts[case_id] = text
            probe_texts[(case_id, "unit")] = text
        else:
            unit_spans[case_id] = None
            unit_texts[case_id] = None

    references: list[_ReferenceView] = []
    for inventory in ordered_inventories:
        references.extend(
            _ReferenceView(
                role=document.role,
                reference_axis="file",
                repository_source_id=document.repository_source_id,
                revision=document.revision,
                path=document.path,
                case_id=None,
                content_sha256=document.content_sha256,
                text=document.text,
            )
            for document in inventory.documents
        )
    for case_id in sorted(packet_cases):
        file_text = probe_texts[(case_id, "file")]
        references.append(
            _ReferenceView(
                role="campaign_peer",
                reference_axis="file",
                repository_source_id=selection.repository.source_id,
                revision=selection.repository.revision,
                path=source_paths[case_id],
                case_id=case_id,
                content_sha256=bytes_hash(file_text.encode("utf-8")),
                text=file_text,
            )
        )
        unit_text = unit_texts[case_id]
        if unit_text is not None:
            references.append(
                _ReferenceView(
                    role="campaign_peer",
                    reference_axis="unit",
                    repository_source_id=selection.repository.source_id,
                    revision=selection.repository.revision,
                    path=f"{source_paths[case_id]}#review-unit",
                    case_id=case_id,
                    content_sha256=bytes_hash(unit_text.encode("utf-8")),
                    text=unit_text,
                )
            )

    unique_reference_texts = {
        (reference.content_sha256, reference.text) for reference in references
    }
    reference_nfc_characters = {
        identity: len(unicodedata.normalize("NFC", identity[1]))
        for identity in unique_reference_texts
    }
    unique_reference_nfc_character_count = sum(reference_nfc_characters.values())
    selected_nfc_characters = {
        key: len(unicodedata.normalize("NFC", text)) for key, text in probe_texts.items()
    }
    selected_nfc_character_count = sum(selected_nfc_characters.values())
    file_reference_nfc_character_count = sum(
        reference_nfc_characters[(reference.content_sha256, reference.text)]
        for reference in references
        if reference.reference_axis == "file"
    )
    all_reference_nfc_character_count = sum(
        reference_nfc_characters[(reference.content_sha256, reference.text)]
        for reference in references
    )
    file_reference_count = sum(reference.reference_axis == "file" for reference in references)
    all_reference_count = len(references)
    planned_similarity_pair_count = 0
    planned_similarity_pair_nfc_characters = 0
    for case_id, axis in sorted(probe_texts):
        own_file_characters = selected_nfc_characters[(case_id, "file")]
        selected_characters = selected_nfc_characters[(case_id, axis)]
        if axis == "file":
            eligible_count = file_reference_count - 1
            planned_similarity_pair_count += eligible_count
            planned_similarity_pair_nfc_characters += (
                file_reference_nfc_character_count
                - own_file_characters
                + eligible_count * selected_characters
            )
            continue
        own_unit_characters = selected_nfc_characters[(case_id, "unit")]
        own_reference_count = 2
        eligible_count = all_reference_count - own_reference_count
        planned_similarity_pair_count += eligible_count
        planned_similarity_pair_nfc_characters += (
            all_reference_nfc_character_count
            - own_file_characters
            - own_unit_characters
            + eligible_count * selected_characters
        )
    similarity_work_blockers: list[SimilarityWorkBlocker] = []
    if selected_nfc_character_count > policy.maximum_selected_nfc_characters:
        similarity_work_blockers.append("selected_character_budget_exceeded")
    if unique_reference_nfc_character_count > policy.maximum_unique_reference_nfc_characters:
        similarity_work_blockers.append("unique_reference_character_budget_exceeded")
    if planned_similarity_pair_count > policy.maximum_similarity_pairs:
        similarity_work_blockers.append("similarity_pair_budget_exceeded")
    if planned_similarity_pair_nfc_characters > policy.maximum_similarity_pair_nfc_characters:
        similarity_work_blockers.append("similarity_character_budget_exceeded")
    similarity_work_status: SimilarityWorkStatus = (
        "abstained_resource_limit" if similarity_work_blockers else "evaluated"
    )
    probes_evaluated = similarity_work_status == "evaluated"

    probes: dict[tuple[str, ScreeningAxis], _Probe] = {}
    if probes_evaluated:
        probes = {key: _probe(key[0], key[1], text, policy) for key, text in probe_texts.items()}
    matches: list[NearDuplicateMatch] = []
    axis_decisions: dict[tuple[str, ScreeningAxis], list[AxisDecision]] = {
        key: [] for key in probe_texts
    }
    reference_tokenization_issue_keys: set[tuple[str, str, str]] = set()
    prepared_by_content: dict[tuple[str, str], _PreparedReferenceText] = {}
    prepared_reference_items: list[tuple[_ReferenceView, _PreparedReferenceText]] = []
    if similarity_work_status == "evaluated":
        for reference in references:
            cache_key = (reference.content_sha256, reference.text)
            prepared = prepared_by_content.get(cache_key)
            if prepared is None:
                prepared = _prepare_reference_text(reference.text, policy)
                prepared_by_content[cache_key] = prepared
            if prepared.content.issues or prepared.shape.issues:
                reference_tokenization_issue_keys.add(
                    (reference.role, reference.revision, reference.path)
                )
            prepared_reference_items.append((reference, prepared))
    prepared_references = tuple(prepared_reference_items)
    attempted_similarity_pair_count = 0
    recorded_match_budget_exceeded = False
    for key, selected_probe in sorted(probes.items()):
        case_id, axis = key
        for reference, prepared_reference in prepared_references:
            if reference.role == "campaign_peer" and reference.case_id == case_id:
                continue
            if axis == "file" and reference.reference_axis != "file":
                continue
            attempted_similarity_pair_count += 1
            decision, signals, scores, _issues = _similarity(
                selected_probe,
                prepared_reference,
                policy,
            )
            axis_decisions[key].append(decision)
            if decision in {"duplicate", "gray"}:
                if len(matches) >= policy.maximum_recorded_matches:
                    recorded_match_budget_exceeded = True
                    break
                match_decision: Literal["duplicate", "gray"] = (
                    "duplicate" if decision == "duplicate" else "gray"
                )
                matches.append(
                    NearDuplicateMatch(
                        case_id=case_id,
                        axis=axis,
                        reference_role=reference.role,
                        reference_axis=reference.reference_axis,
                        reference_repository_source_id=reference.repository_source_id,
                        reference_revision=reference.revision,
                        reference_path=reference.path,
                        reference_case_id=reference.case_id,
                        reference_content_sha256=reference.content_sha256,
                        decision=match_decision,
                        signals=signals,
                        scores=scores,
                    )
                )
        if recorded_match_budget_exceeded:
            break
    if recorded_match_budget_exceeded:
        similarity_work_blockers.append("recorded_match_budget_exceeded")
        similarity_work_status = "abstained_resource_limit"
        matches.clear()
        axis_decisions = {key: [] for key in probe_texts}

    case_results: list[NearDuplicateCaseResult] = []
    for case_id in sorted(packet_cases):
        file_text = probe_texts[(case_id, "file")]
        file_probe = probes.get((case_id, "file"))
        unit_text = unit_texts[case_id]
        unit_probe = probes.get((case_id, "unit"))
        blockers: list[str] = []

        def decide_axis(
            axis: ScreeningAxis,
            text: str | None,
            probe: _Probe | None,
            case_blockers: list[str],
        ) -> AxisDecision:
            if text is None:
                case_blockers.append("review_unit_unresolved")
                return "abstain"
            if similarity_work_status == "abstained_resource_limit":
                case_blockers.append(f"{axis}_similarity_work_budget_exceeded")
                return "abstain"
            if probe is None:
                raise ValueError("evaluated similarity work is missing a selected probe")
            decisions = axis_decisions[(probe.case_id, probe.axis)]
            if probe.content.issues or probe.shape.issues:
                case_blockers.append(f"{probe.axis}_tokenization_issue")
                return "abstain"
            if "duplicate" in decisions:
                return "duplicate"
            if "gray" in decisions:
                return "gray"
            if reference_inventory_incomplete:
                case_blockers.append(f"{probe.axis}_reference_inventory_incomplete")
                return "abstain"
            if "abstain" in decisions:
                case_blockers.append(f"{probe.axis}_reference_tokenization_issue")
                return "abstain"
            if len(probe.content_shingles) < policy.minimum_informative_content_shingles:
                case_blockers.append(f"{probe.axis}_too_short_for_policy")
                return "abstain"
            return "clear"

        file_decision = decide_axis("file", file_text, file_probe, blockers)
        unit_decision = decide_axis("unit", unit_text, unit_probe, blockers)
        if "duplicate" in {file_decision, unit_decision}:
            overall: AxisDecision = "duplicate"
        elif "gray" in {file_decision, unit_decision}:
            overall = "gray"
        elif "abstain" in {file_decision, unit_decision}:
            overall = "abstain"
        else:
            overall = "clear"
        case_matches = tuple(match for match in matches if match.case_id == case_id)
        case_span = unit_spans[case_id]
        case_results.append(
            NearDuplicateCaseResult(
                case_id=case_id,
                probe_evaluation_status=(
                    "evaluated" if probes_evaluated else "not_run_resource_limit"
                ),
                file_content_sha256=bytes_hash(file_text.encode("utf-8")),
                unit_content_sha256=(
                    bytes_hash(unit_text.encode("utf-8")) if unit_text is not None else None
                ),
                unit_start_line=case_span[0] if case_span is not None else None,
                unit_end_line=case_span[1] if case_span is not None else None,
                file_content_token_count=len(file_probe.content.tokens) if file_probe else 0,
                unit_content_token_count=len(unit_probe.content.tokens) if unit_probe else 0,
                file_content_shingle_count=len(file_probe.content_shingles) if file_probe else 0,
                unit_content_shingle_count=len(unit_probe.content_shingles) if unit_probe else 0,
                file_tokenization_issues=file_probe.content.issues if file_probe else (),
                unit_tokenization_issues=unit_probe.content.issues if unit_probe else (),
                file_decision=file_decision,
                unit_decision=unit_decision,
                overall_decision=overall,
                blockers=tuple(sorted(set(blockers))),
                hard_match_count=sum(match.decision == "duplicate" for match in case_matches),
                gray_match_count=sum(match.decision == "gray" for match in case_matches),
            )
        )

    screening_blockers: list[str] = []
    for inventory in ordered_inventories:
        screening_blockers.extend(
            f"{inventory.summary.role}:{issue}" for issue in inventory.summary.inventory_issues
        )
    if not development_fully_evaluated:
        screening_blockers.append("development_truth:registered_path_not_evaluated")
    screening_blockers.extend(similarity_work_blockers)
    if reference_tokenization_issue_keys:
        screening_blockers.append("reference_tokenization_issues")
    if consensus.consensus_status != "complete":
        screening_blockers.append("consensus_unresolved")
    duplicate_count = sum(case.overall_decision == "duplicate" for case in case_results)
    gray_count = sum(case.overall_decision == "gray" for case in case_results)
    abstain_count = sum(case.overall_decision == "abstain" for case in case_results)
    clear_count = sum(case.overall_decision == "clear" for case in case_results)
    if duplicate_count:
        outcome: ScreeningOutcome = "potential_duplicate"
    elif gray_count or abstain_count or screening_blockers:
        outcome = "review_required"
    else:
        outcome = "clean"
    payload: dict[str, object] = {
        "schema_version": TAG_TRUTH_V2_NEAR_DUPLICATE_SCREENING_SCHEMA_VERSION,
        "policy_fingerprint": policy.policy_fingerprint,
        "policy_approval_status": policy.approval_status,
        "provenance_verification_id": provenance.verification_id,
        "seal_revision": provenance.seal_revision,
        "seal_tree_id": provenance.seal_tree_id,
        "candidate_commit": provenance.candidate_commit,
        "candidate_project_tree_id": candidate_inventory.summary.tree_id,
        "source_repository_revision": provenance.source_repository_revision,
        "source_repository_tree_id": provenance.source_repository_tree_id,
        "exposure_revision": provenance.exposure_revision,
        "exposure_tree_id": provenance.exposure_tree_id,
        "development_truth_revision": development_inventory.summary.revision,
        "development_truth_suite_fingerprint": (
            selection.development_exclusions.truth_suite_fingerprint
        ),
        "selection_id": selection.selection_id,
        "packet_id": packet.packet_id,
        "receipt_ids": list(provenance.receipt_ids),
        "consensus_id": consensus.consensus_id,
        "reference_inventories": [
            inventory.summary.model_dump(mode="json") for inventory in ordered_inventories
        ],
        "cases": [case.model_dump(mode="json") for case in case_results],
        "matches": [
            match.model_dump(mode="json") for match in sorted(matches, key=_match_sort_key)
        ],
        "reference_tokenization_issue_count": len(reference_tokenization_issue_keys),
        "work_estimate_version": policy.work_estimate_version,
        "probe_count": len(probe_texts),
        "selected_nfc_character_count": selected_nfc_character_count,
        "unique_reference_text_count": len(unique_reference_texts),
        "unique_reference_nfc_character_count": unique_reference_nfc_character_count,
        "planned_similarity_pair_count": planned_similarity_pair_count,
        "planned_similarity_pair_nfc_characters": (planned_similarity_pair_nfc_characters),
        "attempted_similarity_pair_count": attempted_similarity_pair_count,
        "similarity_work_status": similarity_work_status,
        "similarity_work_blockers": sorted(similarity_work_blockers),
        "duplicate_case_count": duplicate_count,
        "gray_case_count": gray_count,
        "abstain_case_count": abstain_count,
        "clear_case_count": clear_count,
        "screening_blockers": sorted(set(screening_blockers)),
        "qualification_blockers": ["calibration_truth_unavailable", "policy_not_approved"],
        "screening_outcome": outcome,
        "near_duplicate_qualification_status": "not_qualified_policy_unapproved",
        "evidence_qualification_status": "not_qualified",
        "candidate_execution_status": "not_run",
    }
    sealed = near_duplicate_verification_payload_with_id(payload)
    return TagTruthV2NearDuplicateVerification.model_validate_json(canonical_json(sealed))


def verify_tag_truth_v2_near_duplicate_verification(
    report: TagTruthV2NearDuplicateVerification,
    *,
    policy: TagTruthV2NearDuplicatePolicy,
    provenance: TagTruthV2ProvenanceVerification,
    selection: TagTruthV2Selection,
    packet: TagTruthV2ReviewPacket,
    consensus: TagTruthV2Consensus,
    reference_inventories: Sequence[ScannedReferenceInventory],
) -> None:
    canonical_report = TagTruthV2NearDuplicateVerification.model_validate_json(
        canonical_json(report.model_dump(mode="json"))
    )
    rebuilt = build_tag_truth_v2_near_duplicate_verification(
        policy=policy,
        provenance=provenance,
        selection=selection,
        packet=packet,
        consensus=consensus,
        reference_inventories=reference_inventories,
    )
    if canonical_report != rebuilt:
        raise ValueError("near-duplicate screening does not rebuild from sealed inputs")


def _parse_json_model[TModel: BaseModel](
    raw: bytes,
    model: type[TModel],
    context: str,
) -> TModel:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        return model.model_validate_json(canonical_json(payload))
    except (UnicodeError, json.JSONDecodeError, ValidationError, _DuplicateKeyError) as exc:
        raise ValueError(f"invalid {context}: {exc}") from exc


def _load_json_model[TModel: BaseModel](
    path: str | Path,
    model: type[TModel],
    context: str,
) -> TModel:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"{context} must be a regular non-symlink file: {candidate}")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {context} {candidate}: {exc}") from exc
    return _parse_json_model(raw, model, context)


def parse_tag_truth_v2_near_duplicate_policy(raw: bytes) -> TagTruthV2NearDuplicatePolicy:
    return _parse_json_model(raw, TagTruthV2NearDuplicatePolicy, "near-duplicate policy")


def load_tag_truth_v2_near_duplicate_policy(
    path: str | Path,
) -> TagTruthV2NearDuplicatePolicy:
    return _load_json_model(path, TagTruthV2NearDuplicatePolicy, "near-duplicate policy")


def parse_tag_truth_v2_near_duplicate_verification(
    raw: bytes,
) -> TagTruthV2NearDuplicateVerification:
    return _parse_json_model(
        raw,
        TagTruthV2NearDuplicateVerification,
        "near-duplicate screening",
    )


def load_tag_truth_v2_near_duplicate_verification(
    path: str | Path,
) -> TagTruthV2NearDuplicateVerification:
    return _load_json_model(
        path,
        TagTruthV2NearDuplicateVerification,
        "near-duplicate screening",
    )


NearDuplicatePolicy = TagTruthV2NearDuplicatePolicy


__all__ = [
    "NearDuplicateCaseResult",
    "NearDuplicateMatch",
    "NearDuplicatePolicy",
    "RatioThreshold",
    "ReferenceDocument",
    "ReferenceInventory",
    "ReferenceInventorySummary",
    "ScannedReferenceInventory",
    "SimilarityScores",
    "TAG_TRUTH_V2_NEAR_DUPLICATE_POLICY_SCHEMA_VERSION",
    "TAG_TRUTH_V2_NEAR_DUPLICATE_SCREENING_SCHEMA_VERSION",
    "TagTruthV2NearDuplicatePolicy",
    "TagTruthV2NearDuplicateVerification",
    "build_tag_truth_v2_near_duplicate_verification",
    "load_tag_truth_v2_near_duplicate_policy",
    "load_tag_truth_v2_near_duplicate_verification",
    "near_duplicate_policy_payload_with_fingerprint",
    "near_duplicate_verification_payload_with_id",
    "parse_tag_truth_v2_near_duplicate_policy",
    "parse_tag_truth_v2_near_duplicate_verification",
    "scan_pinned_git_reference_inventory",
    "tokenize_arkts_like",
    "verify_tag_truth_v2_near_duplicate_verification",
]
