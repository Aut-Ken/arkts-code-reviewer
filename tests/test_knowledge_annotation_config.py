from __future__ import annotations

from pathlib import Path

import pytest

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.annotation_config import (
    DEFAULT_ANNOTATION_CONFIG,
    KnowledgeAnnotationConfig,
    load_knowledge_annotation_config,
)


def _default_config_text() -> str:
    return DEFAULT_ANNOTATION_CONFIG.read_text(encoding="utf-8")


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "knowledge_annotations.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_default_annotation_config_is_registered_and_repeatable() -> None:
    features = load_default_feature_config()

    first = load_knowledge_annotation_config(feature_config=features)
    second = load_knowledge_annotation_config(feature_config=features)

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.version == "knowledge-annotations-v1"
    assert first.source_domain_ids == (
        "async-taskpool-worker",
        "state-management-arkts",
        "timer-subscription-lifecycle",
    )
    assert set(first.registered_domain_ids) >= set(first.source_domain_ids)
    assert {
        rule.tag_id for rule in first.keyword_tag_rules
    }.issubset(features.tags_by_id)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda text: text.replace(
                "version: knowledge-annotations-v1\n",
                "version: knowledge-annotations-v1\nunknown: true\n",
                1,
            ),
            "Extra inputs are not permitted",
        ),
        (
            lambda text: text.replace(
                "version: knowledge-annotations-v1\n",
                "",
                1,
            ),
            "Field required",
        ),
        (
            lambda text: text.replace(
                "source_domain_ids: [async-taskpool-worker, state-management-arkts, "
                "timer-subscription-lifecycle]",
                "source_domain_ids: [timer-subscription-lifecycle, async-taskpool-worker]",
                1,
            ),
            "sorted and unique",
        ),
        (
            lambda text: text.replace(
                "tag_id: has_lifecycle",
                "tag_id: has_not_registered",
                1,
            ),
            "unknown Tags",
        ),
        (
            lambda text: text.replace(
                "any_tags: [has_lifecycle]",
                "any_tags: [has_not_registered]",
                1,
            ),
            "unknown Tags",
        ),
        (
            lambda text: text.replace(
                "any_tags: [has_lifecycle]\n    any_keywords: [不再使用, 组件创建, 组件销毁]",
                "any_tags: []\n    any_keywords: []",
                1,
            ),
            "requires at least one trigger",
        ),
    ],
)
def test_annotation_config_fails_closed_on_schema_and_reference_drift(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    assert callable(mutate)
    path = _write_config(tmp_path, mutate(_default_config_text()))

    with pytest.raises(ValueError, match=message):
        load_knowledge_annotation_config(
            path,
            feature_config=load_default_feature_config(),
        )


def test_annotation_config_rejects_duplicate_yaml_key(tmp_path: Path) -> None:
    text = _default_config_text().replace(
        "version: knowledge-annotations-v1\n",
        "version: knowledge-annotations-v1\nversion: forged\n",
        1,
    )

    with pytest.raises(ValueError, match="duplicate key"):
        load_knowledge_annotation_config(
            _write_config(tmp_path, text),
            feature_config=load_default_feature_config(),
        )


def test_annotation_config_rejects_symlink(tmp_path: Path) -> None:
    real = _write_config(tmp_path, _default_config_text())
    symlink = tmp_path / "annotation-link.yaml"
    symlink.symlink_to(real.name)

    with pytest.raises(ValueError, match="regular non-symlink"):
        load_knowledge_annotation_config(
            symlink,
            feature_config=load_default_feature_config(),
        )


def test_annotation_config_model_rejects_duplicate_records() -> None:
    config = load_knowledge_annotation_config(
        feature_config=load_default_feature_config()
    )
    payload = config.model_dump(mode="json")
    payload["domain_rules"].append(payload["domain_rules"][0])

    with pytest.raises(ValueError, match="domain_rules must be sorted and unique"):
        KnowledgeAnnotationConfig.model_validate(payload)
