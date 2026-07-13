from __future__ import annotations

import io
import re
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from arkts_code_reviewer.feature_routing import (
    DEFAULT_DIMENSIONS_PATH,
    DEFAULT_TAGS_PATH,
    load_feature_config,
)


def _copy_default_configs(tmp_path: Path) -> tuple[Path, Path]:
    tags_path = tmp_path / "tags.yaml"
    dimensions_path = tmp_path / "dimensions.yaml"
    tags_path.write_text(DEFAULT_TAGS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    dimensions_path.write_text(
        DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tags_path, dimensions_path


def _replace(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    assert old in source
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def _reverse_sequence(path: Path, key: str) -> None:
    safe_yaml = YAML(typ="safe")
    data = safe_yaml.load(path.read_text(encoding="utf-8"))
    data[key] = list(reversed(data[key]))
    output = io.StringIO()
    yaml = YAML()
    yaml.dump(data, output)
    path.write_text(output.getvalue(), encoding="utf-8")


def test_default_feature_config_freezes_v1_truth() -> None:
    config = load_feature_config()

    assert config.tag_config.schema_version == "tag-config-v1"
    assert config.tag_config.version == "tags-v1"
    assert config.dimension_config.schema_version == "dimension-config-v1"
    assert config.dimension_config.version == "dimensions-v1"
    assert len(config.tags_by_id) == 24
    assert len(config.dimensions_by_id) == 12
    assert len(config.review_questions_by_id) == 12
    assert tuple(config.tags_by_id) == tuple(sorted(config.tags_by_id))
    assert tuple(config.dimensions_by_id) == tuple(sorted(config.dimensions_by_id))
    assert tuple(config.review_questions_by_id) == tuple(
        sorted(config.review_questions_by_id)
    )
    assert all(item.status == "Active" for item in config.tags_by_id.values())
    assert all(item.status == "Active" for item in config.dimensions_by_id.values())
    assert all(
        item.status == "Active" for item in config.review_questions_by_id.values()
    )
    assert re.fullmatch(r"feature-config:sha256:[0-9a-f]{64}", config.fingerprint)

    assert config.tags_by_id["has_timer"].triggers.any_api == (
        "clearInterval",
        "clearTimeout",
        "setInterval",
        "setTimeout",
        "systemTimer.setInterval",
    )
    assert config.tags_by_id["has_subscription"].triggers.any_api == (
        "emitter.off",
        "emitter.on",
        "emitter.once",
        "sensor.off",
        "sensor.on",
        "sensor.once",
    )
    assert config.tags_by_id["has_interactive_component"].triggers.any_attribute == (
        "onBlur",
        "onChange",
        "onClick",
        "onFocus",
        "onTouch",
    )


def test_default_dimension_and_question_policies_are_frozen() -> None:
    config = load_feature_config()

    always_check = {
        item.id for item in config.dimensions_by_id.values() if item.always_check
    }
    assert always_check == {
        "DIM-01",
        "DIM-02",
        "DIM-03",
        "DIM-04",
        "DIM-05",
        "DIM-12",
    }
    assert config.dimensions_by_id["DIM-01"].retrieval_policy == "disabled"
    assert {
        item.retrieval_policy
        for dimension_id, item in config.dimensions_by_id.items()
        if dimension_id != "DIM-01"
    } == {"signal_required"}
    assert config.dimensions_by_id["DIM-06"].triggers.any_tag == (
        "has_file_io",
        "has_image",
        "has_media",
        "has_subscription",
        "has_timer",
    )

    always_bound = {
        item.id for item in config.review_questions_by_id.values() if item.always_bind
    }
    assert always_bound == {"RQ-correctness"}
    assert not config.review_questions_by_id["RQ-correctness"].triggers.any_tag
    assert config.review_questions_by_id["RQ-resource"].triggers.any_tag == (
        "has_file_io",
        "has_image",
        "has_media",
        "has_subscription",
        "has_timer",
    )


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    tags_path, dimensions_path = _copy_default_configs(tmp_path)
    _replace(tags_path, "version: tags-v1\n", "version: tags-v1\nversion: tags-v2\n")

    with pytest.raises(ValueError, match="unable to load tag config"):
        load_feature_config(tags_path, dimensions_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda source: source.replace(
                "    description: 代码使用 ArkUI 动画 API 或 transition 属性\n",
                "",
                1,
            ),
            "description",
        ),
        (
            lambda source: source.replace(
                "    description: 代码使用 ArkUI 动画 API 或 transition 属性\n",
                "    description: 代码使用 ArkUI 动画 API 或 transition 属性\n"
                "    unknown: true\n",
                1,
            ),
            "unknown",
        ),
        (
            lambda source: source.replace("  - id: has_async\n", "  - id: has_animation\n", 1),
            "duplicate IDs",
        ),
        (
            lambda source: source.replace("    status: Active\n", "    status: Retired\n", 1),
            "status",
        ),
        (
            lambda source: source.replace(
                "      any_api: [clearInterval, clearTimeout, setInterval, setTimeout, "
                "systemTimer.setInterval]\n",
                "      any_api: [setInterval, clearInterval]\n",
                1,
            ),
            "sorted and unique",
        ),
    ],
)
def test_tag_config_rejects_missing_unknown_duplicate_status_and_order(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    tags_path, dimensions_path = _copy_default_configs(tmp_path)
    source = tags_path.read_text(encoding="utf-8")
    assert callable(mutation)
    tags_path.write_text(mutation(source), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_feature_config(tags_path, dimensions_path)


def test_unknown_tag_reference_is_rejected(tmp_path: Path) -> None:
    tags_path, dimensions_path = _copy_default_configs(tmp_path)
    _replace(dimensions_path, "[has_interactive_component]", "[has_missing]")

    with pytest.raises(ValueError, match="unknown tags"):
        load_feature_config(tags_path, dimensions_path)


def test_active_definition_cannot_depend_on_non_active_tag(tmp_path: Path) -> None:
    tags_path, dimensions_path = _copy_default_configs(tmp_path)
    _replace(
        tags_path,
        "  - id: has_timer\n    status: Active\n",
        "  - id: has_timer\n    status: Draft\n",
    )

    with pytest.raises(ValueError, match="depends on non-Active tags"):
        load_feature_config(tags_path, dimensions_path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "    title: 无障碍行为是否完整且可用\n",
            "",
            "title",
        ),
        (
            "    title: 无障碍行为是否完整且可用\n",
            "    title: 无障碍行为是否完整且可用\n    unknown: true\n",
            "unknown",
        ),
        (
            "  - id: RQ-adaptability\n",
            "  - id: RQ-accessibility\n",
            "duplicate IDs",
        ),
        (
            "  - id: DIM-02\n",
            "  - id: DIM-01\n",
            "duplicate IDs",
        ),
        (
            "    status: Active\n    always_bind: false\n",
            "    status: Retired\n    always_bind: false\n",
            "status",
        ),
        (
            "    status: Active\n    always_check: true\n"
            "    retrieval_policy: disabled\n",
            "    status: Retired\n    always_check: true\n"
            "    retrieval_policy: disabled\n",
            "status",
        ),
        (
            "    retrieval_policy: disabled\n",
            "    retrieval_policy: sometimes\n",
            "retrieval_policy",
        ),
        (
            "    always_bind: true\n    triggers: {}\n",
            "    always_bind: true\n    triggers:\n      any_tag: [has_timer]\n",
            "always-bound",
        ),
        (
            "    always_bind: false\n    triggers:\n      any_tag: [has_interactive_component]\n",
            "    always_bind: false\n    triggers: {}\n",
            "conditional review questions",
        ),
    ],
)
def test_dimension_and_question_config_fail_closed(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    tags_path, dimensions_path = _copy_default_configs(tmp_path)
    _replace(dimensions_path, old, new)

    with pytest.raises(ValueError, match=message):
        load_feature_config(tags_path, dimensions_path)


@pytest.mark.parametrize("key", ["tags", "review_questions", "dimensions"])
def test_empty_definition_sets_are_rejected(tmp_path: Path, key: str) -> None:
    tags_path, dimensions_path = _copy_default_configs(tmp_path)
    path = tags_path if key == "tags" else dimensions_path
    safe_yaml = YAML(typ="safe")
    data = safe_yaml.load(path.read_text(encoding="utf-8"))
    data[key] = []
    output = io.StringIO()
    yaml = YAML()
    yaml.dump(data, output)
    path.write_text(output.getvalue(), encoding="utf-8")

    with pytest.raises(ValueError, match="must not be empty"):
        load_feature_config(tags_path, dimensions_path)


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks are unavailable")
def test_config_symlinks_are_rejected(tmp_path: Path) -> None:
    tags_path, dimensions_path = _copy_default_configs(tmp_path)
    symlink = tmp_path / "tags-link.yaml"
    symlink.symlink_to(tags_path)

    with pytest.raises(ValueError, match="must not be a symlink"):
        load_feature_config(symlink, dimensions_path)


def test_fingerprint_uses_canonical_definition_order(tmp_path: Path) -> None:
    default = load_feature_config()
    tags_path, dimensions_path = _copy_default_configs(tmp_path)
    _reverse_sequence(tags_path, "tags")
    _reverse_sequence(dimensions_path, "review_questions")
    _reverse_sequence(dimensions_path, "dimensions")

    reordered = load_feature_config(tags_path, dimensions_path)

    assert reordered.fingerprint == default.fingerprint
    assert tuple(reordered.tags_by_id) == tuple(default.tags_by_id)
    assert tuple(reordered.dimensions_by_id) == tuple(default.dimensions_by_id)
    assert tuple(reordered.review_questions_by_id) == tuple(
        default.review_questions_by_id
    )


def test_fingerprint_changes_when_canonical_content_changes(tmp_path: Path) -> None:
    default = load_feature_config()
    tags_path, dimensions_path = _copy_default_configs(tmp_path)
    _replace(
        tags_path,
        "description: 代码使用 ArkUI 动画 API 或 transition 属性",
        "description: 代码使用 ArkUI 动画和 transition 属性",
    )

    changed = load_feature_config(tags_path, dimensions_path)

    assert changed.fingerprint != default.fingerprint


def test_feature_config_rejects_forged_fingerprint() -> None:
    config = load_feature_config()

    with pytest.raises(ValueError, match="fingerprint does not match"):
        replace(
            config,
            fingerprint="feature-config:sha256:" + ("0" * 64),
        )


def test_default_configs_are_mapped_into_the_wheel() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ]

    assert force_include == {
        "config/dimensions.yaml": (
            "arkts_code_reviewer/feature_routing/defaults/dimensions.yaml"
        ),
        "config/knowledge_seed_v1.yaml": (
            "arkts_code_reviewer/knowledge/defaults/knowledge_seed_v1.yaml"
        ),
        "config/knowledge_annotations.yaml": (
            "arkts_code_reviewer/knowledge/defaults/knowledge_annotations.yaml"
        ),
        "config/knowledge_model_export.yaml": (
            "arkts_code_reviewer/knowledge/defaults/knowledge_model_export.yaml"
        ),
        "config/retrieval.yaml": (
            "arkts_code_reviewer/retrieval/defaults/retrieval.yaml"
        ),
        "config/tags.yaml": "arkts_code_reviewer/feature_routing/defaults/tags.yaml",
        "sidecars/knowledge-api-parser/package-lock.json": (
            "arkts_code_reviewer/knowledge/sidecars/api_parser/package-lock.json"
        ),
        "sidecars/knowledge-api-parser/package.json": (
            "arkts_code_reviewer/knowledge/sidecars/api_parser/package.json"
        ),
        "sidecars/knowledge-api-parser/parse_api.js": (
            "arkts_code_reviewer/knowledge/sidecars/api_parser/parse_api.js"
        ),
        "prompts/knowledge/grok-knowledge-auditor-v4.md": (
            "arkts_code_reviewer/knowledge/defaults/grok-knowledge-auditor-v4.md"
        ),
    }
    assert DEFAULT_TAGS_PATH.is_file()
    assert DEFAULT_DIMENSIONS_PATH.is_file()
