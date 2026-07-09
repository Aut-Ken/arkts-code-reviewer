from __future__ import annotations

from arkts_code_reviewer.code_analysis.arkts_lexicon import (
    IMAGE_COMPONENTS,
    INTERACTIVE_COMPONENTS,
    LAYOUT_COMPONENTS,
    LIFECYCLE_SYMBOLS,
    STATE_DECORATORS,
)
from arkts_code_reviewer.code_analysis.models import CodeFacts

CORE_DIMENSIONS = ("DIM-01", "DIM-02", "DIM-03", "DIM-04", "DIM-05")


def derive_tags(facts: CodeFacts) -> set[str]:
    tags: set[str] = set()

    if facts.components & IMAGE_COMPONENTS or _has_api_prefix(facts, "image."):
        tags.add("has_image")
    if facts.apis & {"setInterval", "setTimeout", "systemTimer.setInterval"}:
        tags.add("has_timer")
    if _has_subscription(facts):
        tags.add("has_subscription")
    if _has_api_prefixes(facts, ("media.", "audio.", "camera.")) or facts.components & {
        "Video",
        "XComponent",
    }:
        tags.add("has_media")
    if _has_api_prefixes(facts, ("fs.", "fileIo.")):
        tags.add("has_file_io")
    if {"async_fn", "await_expr", "promise"} & facts.syntax:
        tags.add("has_async")
    if _has_api_prefix(facts, "taskpool."):
        tags.add("has_taskpool")
    if _has_api_prefix(facts, "worker.") or "ThreadWorker" in facts.symbols:
        tags.add("has_worker")
    if facts.components & INTERACTIVE_COMPONENTS or any(
        item.startswith("on") for item in facts.attributes
    ):
        tags.add("has_interactive_component")
    if facts.components & LAYOUT_COMPONENTS:
        tags.add("has_layout")
    if _has_api_prefixes(facts, ("mediaquery.", "display.")) or facts.components & {
        "GridRow",
        "GridCol",
    }:
        tags.add("has_responsive_api")
    if (
        facts.components & {"Text", "TextInput", "TextArea", "Search"}
        or "placeholder" in facts.attributes
    ):
        tags.add("has_text_display")
    if facts.apis & {"$r", "$rawfile"}:
        tags.add("has_resource_ref")
    if "requestPermissionsFromUser" in facts.apis or _has_api_prefix(facts, "abilityAccessCtrl."):
        tags.add("has_permission_request")
    if facts.components & {"TextInput", "TextArea", "Search"}:
        tags.add("has_user_input")
    if _has_api_prefixes(facts, ("http.", "socket.", "rcp.")):
        tags.add("has_network")
    if _has_api_prefixes(facts, ("preferences.", "relationalStore.")):
        tags.add("has_storage")
    if facts.decorators & STATE_DECORATORS:
        tags.add("has_state_management")
    if facts.symbols & LIFECYCLE_SYMBOLS:
        tags.add("has_lifecycle")
    if facts.components & {"List", "Grid", "WaterFlow"} or facts.symbols & {
        "ForEach",
        "LazyForEach",
        "Repeat",
    }:
        tags.add("has_list_render")
    if "animateTo" in facts.apis or "transition" in facts.attributes:
        tags.add("has_animation")
    if facts.decorators & {"@Builder", "@BuilderParam"}:
        tags.add("has_builder")
    if facts.components & {"Navigation", "NavDestination"} or _has_api_prefix(facts, "router."):
        tags.add("has_navigation")
    if _has_api_prefix(facts, "hilog."):
        tags.add("has_logging")

    return tags


def trigger_dimensions(tags: set[str]) -> list[str]:
    dimensions = set(CORE_DIMENSIONS)
    if {"has_image", "has_subscription", "has_timer", "has_media", "has_file_io"} & tags:
        dimensions.add("DIM-06")
    if {"has_async", "has_taskpool", "has_worker"} & tags:
        dimensions.add("DIM-07")
    if "has_interactive_component" in tags:
        dimensions.add("DIM-08")
    if {"has_layout", "has_responsive_api"} & tags:
        dimensions.add("DIM-09")
    if {"has_text_display", "has_resource_ref"} & tags:
        dimensions.add("DIM-10")
    if {"has_permission_request", "has_user_input", "has_network", "has_storage"} & tags:
        dimensions.add("DIM-11")
    dimensions.add("DIM-12")
    return sorted(dimensions)


def _has_api_prefix(facts: CodeFacts, prefix: str) -> bool:
    return any(api.startswith(prefix) for api in facts.apis)


def _has_api_prefixes(facts: CodeFacts, prefixes: tuple[str, ...]) -> bool:
    return any(any(api.startswith(prefix) for prefix in prefixes) for api in facts.apis)


def _has_subscription(facts: CodeFacts) -> bool:
    if _has_api_prefixes(facts, ("emitter.", "sensor.")):
        return True
    return any(
        api.endswith(".on") or api.endswith(".off") or api.endswith(".once")
        for api in facts.apis
    )
