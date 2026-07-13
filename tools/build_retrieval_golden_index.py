#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arkts_code_reviewer.feature_routing.config import load_default_feature_config
from arkts_code_reviewer.knowledge.models import (
    AnnotationProvenance,
    ApiSymbol,
    Applicability,
    KnowledgeAnnotation,
    KnowledgeClause,
    SourceRef,
    SourceSpan,
)
from arkts_code_reviewer.retrieval.config import load_default_retrieval_config
from arkts_code_reviewer.retrieval.index import estimate_knowledge_tokens, retrieval_text
from arkts_code_reviewer.retrieval.models import KnowledgeIndex, KnowledgeIndexRecord

_REVISION = "a" * 40
_CREATED_AT = datetime(2026, 7, 13, tzinfo=UTC)
_ANNOTATION_VERSION = "retrieval-golden-annotation-v1"
_CATALOG_VERSION = "retrieval-golden-api-catalog-v1"


def _sha(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _source(rule_id: str, text: str, *, authority: str = "test_evidence") -> SourceRef:
    return SourceRef(
        source_id="retrieval-golden-v1",
        revision=_REVISION,
        relative_path=f"rules/{rule_id.lower().replace('/', '-')}.md",
        anchor="L1-L1",
        authority=authority,
        content_hash=_sha(text),
    )


def _provenance(annotation: dict[str, Any]) -> tuple[AnnotationProvenance, ...]:
    pairs: set[tuple[str, str]] = set()
    for field, kind in (
        ("dimension_ids", "dimension"),
        ("tags", "tag"),
        ("apis", "api"),
        ("components", "component"),
        ("decorators", "decorator"),
        ("domains", "domain"),
        ("raw_keywords", "keyword"),
        ("llm_keywords", "keyword"),
    ):
        pairs.update((kind, value) for value in annotation.get(field, ()))
    if scenario := annotation.get("scenario"):
        pairs.add(("scenario", scenario))
    return tuple(
        AnnotationProvenance(
            kind=kind,
            value=value,
            origin="human_curator",
            evidence_ref="retrieval-golden-v1",
        )
        for kind, value in sorted(pairs)
    )


def _record(
    *,
    rule_id: str,
    text: str,
    dimensions: tuple[str, ...],
    embedding: tuple[float, ...],
    apis: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    components: tuple[str, ...] = (),
    decorators: tuple[str, ...] = (),
    domains: tuple[str, ...],
    keywords: tuple[str, ...] = (),
    scenario: str | None = None,
    applicability: Applicability | None = None,
) -> KnowledgeIndexRecord:
    source = _source(rule_id, text)
    effective_applicability = applicability or Applicability()
    clause = KnowledgeClause(
        rule_id=rule_id,
        rule_type="constraint",
        status="Baselined",
        authority=source.authority,
        text=text,
        heading_path=(rule_id.split("/", 1)[0],),
        applicability=effective_applicability,
        source_ref=source,
        source_span=SourceSpan(start_line=1, end_line=1),
        doc_hash=source.content_hash,
        curation_version="retrieval-golden-curation-v1",
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )
    annotation_fields = {
        "dimension_ids": tuple(sorted(dimensions)),
        "tags": tuple(sorted(tags)),
        "apis": tuple(sorted(apis)),
        "components": tuple(sorted(components)),
        "decorators": tuple(sorted(decorators)),
        "domains": tuple(sorted(domains)),
        "raw_keywords": tuple(sorted(keywords)),
        "scenario": scenario,
    }
    annotation = KnowledgeAnnotation(
        target_kind="clause",
        target_id=rule_id,
        index_version="retrieval-golden-candidate-v1",
        **annotation_fields,
        provenance=_provenance(annotation_fields),
        annotation_version=_ANNOTATION_VERSION,
    )
    text_for_retrieval = retrieval_text(
        scenario=scenario,
        heading_path=clause.heading_path,
        parent_context=None,
        applicability=effective_applicability,
        text=text,
    )
    return KnowledgeIndexRecord(
        clause=clause,
        annotation=annotation,
        domains=tuple(sorted(domains)),
        retrieval_text=text_for_retrieval,
        token_count=estimate_knowledge_tokens(text_for_retrieval),
        embedding=embedding,
    )


def _api_symbol(
    canonical_name: str,
    aliases: tuple[str, ...],
) -> ApiSymbol:
    source = _source(f"API/{canonical_name}", canonical_name)
    return ApiSymbol.create(
        canonical_name=canonical_name,
        aliases=aliases,
        module="fixture.global",
        kind="function",
        signature=f"function {canonical_name}(): void",
        since=None,
        deprecated_since=None,
        source_ref=source,
        source_span=SourceSpan(start_line=1, end_line=1),
        catalog_version=_CATALOG_VERSION,
    )


def build_index() -> KnowledgeIndex:
    records = (
        _record(
            rule_id="ASYNC/ERROR",
            text="异步调用必须显式处理失败路径，不能静默吞掉 rejected Promise。",
            dimensions=("DIM-05", "DIM-07"),
            tags=("has_async",),
            domains=("async-taskpool-worker",),
            keywords=("Promise", "失败路径"),
            scenario="异步任务和 Promise 的错误传播与失败处理",
            embedding=(0.0, 0.8, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8),
        ),
        _record(
            rule_id="DFX/LOGGING",
            text="关键失败路径应记录结构化诊断信息，避免只输出无上下文字符串。",
            dimensions=("DIM-12",),
            apis=("hilog.info",),
            tags=("has_logging",),
            domains=("diagnostics",),
            scenario="日志、诊断和可测试性",
            embedding=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0),
        ),
        _record(
            rule_id="FILE/CLOSE",
            text="文件句柄使用完成后必须在所有退出路径关闭。",
            dimensions=("DIM-05", "DIM-06"),
            apis=("fileIo.close", "fileIo.open"),
            tags=("has_file_io",),
            domains=("resource-lifecycle",),
            scenario="文件句柄创建、异常路径与释放",
            embedding=(1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2),
        ),
        _record(
            rule_id="I18N/RESOURCE",
            text="用户可见文本应使用资源引用，避免在组件中硬编码展示文案。",
            dimensions=("DIM-10",),
            apis=("$r",),
            components=("Text",),
            tags=("has_resource_ref", "has_text_display"),
            domains=("internationalization",),
            scenario="用户可见文本和资源国际化",
            embedding=(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        ),
        _record(
            rule_id="IMAGE/MEMORY",
            text="大图加载应控制解码尺寸并及时释放不再使用的图片资源。",
            dimensions=("DIM-03", "DIM-06"),
            components=("Image",),
            tags=("has_image",),
            domains=("image-loading",),
            scenario="图片加载的内存和性能管理",
            embedding=(0.8, 0.0, 0.0, 0.6, 0.0, 0.0, 0.0, 0.0),
        ),
        _record(
            rule_id="MAINTAINABILITY/SPLIT",
            text="职责过多的大型组件应按稳定职责拆分，并保留清晰的数据边界。",
            dimensions=("DIM-04",),
            domains=("maintainability",),
            keywords=("大型组件", "职责拆分"),
            scenario="大型 UI 组件的职责拆分和可维护性",
            embedding=(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0),
        ),
        _record(
            rule_id="NAVIGATION/FAILURE",
            text="页面跳转应处理目标无效和调用失败，不能假设导航始终成功。",
            dimensions=("DIM-05",),
            apis=("router.pushUrl",),
            tags=("has_navigation",),
            domains=("navigation",),
            scenario="导航调用的失败处理和状态一致性",
            embedding=(0.0, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.7),
        ),
        _record(
            rule_id="NETWORK/PERMISSION",
            text="发起网络请求前必须声明并满足对应权限，同时处理请求失败。",
            dimensions=("DIM-05", "DIM-11"),
            apis=("http.request",),
            tags=("has_network", "has_permission_request"),
            domains=("network-security",),
            scenario="网络访问权限和失败处理",
            applicability=Applicability(permissions=("ohos.permission.INTERNET",)),
            embedding=(0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.4),
        ),
        _record(
            rule_id="ROBUSTNESS/BOUNDARY",
            text="外部输入和边界返回值必须验证，失败时提供可恢复路径。",
            dimensions=("DIM-05",),
            domains=("robustness",),
            keywords=("边界返回值", "输入验证"),
            scenario="边界输入验证、失败恢复和健壮性",
            embedding=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        ),
        _record(
            rule_id="SECURITY/CAPABILITY",
            text="调用受限能力前必须确认目标设备具备系统能力。",
            dimensions=("DIM-11",),
            apis=("secureApi.use",),
            tags=("has_permission_request",),
            domains=("security",),
            applicability=Applicability(system_capabilities=("SystemCapability.Security",)),
            embedding=(0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0),
        ),
        _record(
            rule_id="STATE/LINK",
            text="@Link 必须由父组件提供双向状态来源，避免复制为彼此独立的状态。",
            dimensions=("DIM-02", "DIM-05"),
            decorators=("@Link",),
            tags=("has_state_management",),
            domains=("state-management",),
            scenario="父子组件双向状态同步",
            embedding=(0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.1),
        ),
        _record(
            rule_id="STATE/OBSERVED",
            text="被观察对象的变更必须通过可追踪状态路径传播。",
            dimensions=("DIM-02", "DIM-05"),
            decorators=("@Observed",),
            tags=("has_state_management",),
            domains=("state-management",),
            scenario="观察对象和状态变更传播",
            embedding=(0.0, 0.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.2),
        ),
        _record(
            rule_id="SUBSCRIPTION/PAIR",
            text="事件或传感器订阅必须保存配对关系，并在生命周期结束时取消订阅。",
            dimensions=("DIM-05", "DIM-06"),
            apis=("emitter.off", "emitter.on", "sensor.off", "sensor.on"),
            tags=("has_lifecycle", "has_subscription"),
            domains=("resource-lifecycle",),
            scenario="订阅与取消订阅的生命周期配对",
            embedding=(1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2),
        ),
        _record(
            rule_id="TASKPOOL/SENDABLE",
            text="传入 taskpool 的对象必须满足 Sendable 约束，避免跨线程共享不可发送状态。",
            dimensions=("DIM-02", "DIM-07"),
            apis=("taskpool.execute",),
            tags=("has_async", "has_taskpool"),
            domains=("async-taskpool-worker",),
            scenario="taskpool 跨线程参数和 Sendable 约束",
            embedding=(0.0, 0.8, 0.7, 0.0, 0.0, 0.0, 0.0, 0.1),
        ),
        _record(
            rule_id="TIMER/CLEAR",
            text="组件创建的定时器必须在不再使用或生命周期结束时主动清理。",
            dimensions=("DIM-05", "DIM-06"),
            apis=("clearInterval", "clearTimeout", "setInterval", "setTimeout"),
            tags=("has_timer",),
            domains=("timer-subscription-lifecycle",),
            keywords=("定时器",),
            scenario="组件定时器创建与生命周期清理",
            embedding=(1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2),
        ),
        _record(
            rule_id="TIMER/HANDLE",
            text="周期定时器句柄应由明确 owner 保存，避免重复创建和无法清理。",
            dimensions=("DIM-04", "DIM-06"),
            apis=("setInterval", "setTimeout"),
            tags=("has_timer",),
            domains=("timer-subscription-lifecycle",),
            keywords=("句柄",),
            scenario="定时器句柄所有权和重复创建",
            embedding=(0.9, 0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 0.0),
        ),
        _record(
            rule_id="UI/ACCESSIBILITY",
            text="可交互组件必须提供可理解的无障碍描述和可操作状态。",
            dimensions=("DIM-08",),
            components=("Button",),
            tags=("has_interactive_component",),
            domains=("accessibility",),
            scenario="交互组件无障碍描述和操作",
            embedding=(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        ),
        _record(
            rule_id="UI/RESPONSIVE",
            text="布局应响应窗口和设备尺寸变化，避免依赖单一固定宽度。",
            dimensions=("DIM-09",),
            components=("GridRow",),
            tags=("has_layout", "has_responsive_api"),
            domains=("adaptability",),
            scenario="响应式布局和多设备适配",
            embedding=(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0),
        ),
        _record(
            rule_id="VERSION/API12",
            text="该静态语言 API 仅适用于 API 12 到 API 14 的 OpenHarmony-5.x。",
            dimensions=("DIM-02",),
            apis=("versionedApi.use",),
            domains=("language-version",),
            scenario="API level 和语言模式适用性",
            applicability=Applicability(
                min_api_level=12,
                max_api_level=14,
                releases=("OpenHarmony-5.x",),
                language_modes=("static",),
            ),
            embedding=(0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        ),
        _record(
            rule_id="WORKER/TERMINATE",
            text="Worker 不再使用时必须终止并释放跨线程资源。",
            dimensions=("DIM-06", "DIM-07"),
            apis=("worker.terminate",),
            tags=("has_worker",),
            domains=("async-taskpool-worker",),
            scenario="Worker 生命周期终止和资源释放",
            embedding=(0.7, 0.7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        ),
    )
    api_symbols = tuple(
        sorted(
            (
                _api_symbol("router.pushUrl", ("router.push",)),
                _api_symbol("setInterval", ("global.setInterval",)),
            ),
            key=lambda item: (item.canonical_name, item.signature, item.declaration_id),
        )
    )
    feature_config = load_default_feature_config()
    retrieval_config = load_default_retrieval_config()
    return KnowledgeIndex.create(
        origin="golden_fixture",
        published_build_id=(
            "retrieval-fixture:sha256:"
            + hashlib.sha256(b"retrieval-golden-publication-v1").hexdigest()
        ),
        source_bundle_id=(
            "source-bundle:sha256:"
            + hashlib.sha256(b"retrieval-golden-source-bundle-v1").hexdigest()
        ),
        feature_config_version=feature_config.fingerprint,
        annotation_version=_ANNOTATION_VERSION,
        catalog_version=_CATALOG_VERSION,
        retrieval_version=retrieval_config.version,
        retrieval_config_fingerprint=retrieval_config.fingerprint,
        embedding_model="retrieval-golden-embedding-8d",
        embedding_version="retrieval-golden-embedding-v1",
        embedding_dimensions=8,
        api_symbols=api_symbols,
        records=records,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the reviewed Retrieval Golden index")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/golden/retrieval/index.json"),
    )
    args = parser.parse_args()
    index = build_index()
    rendered = index.model_dump_json(indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "index_version": index.index_version,
                "records": len(index.records),
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
