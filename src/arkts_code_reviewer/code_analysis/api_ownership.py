from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from arkts_code_reviewer.code_analysis.arkts_lexicon import (
    GLOBAL_APIS,
    OHOS_MODULE_PREFIXES,
    SDK_MODULE_PREFIXES,
)
from arkts_code_reviewer.code_analysis.models import ImportInfo

_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
_STATIC_MEMBER_CALL = re.compile(rf"{_IDENTIFIER}(?:\.{_IDENTIFIER})+")
_LOCAL_DECLARATION = re.compile(
    rf"\b(?P<kind>let|const|var)\s+(?P<name>{_IDENTIFIER})"
)
_PARAMETER_TEXT = r"(?:[^(){}]|\([^(){}]*\))*"
_CALLABLE_PARAMETERS = re.compile(
    rf"(?P<name>{_IDENTIFIER})\s*\((?P<params>{_PARAMETER_TEXT})\)"
    rf"\s*(?::\s*[^{{;=]+)?\s*(?P<brace>\{{)"
)
_ARROW_PARAMETERS = re.compile(
    rf"(?P<params>\({_PARAMETER_TEXT}\)|{_IDENTIFIER})\s*=>\s*(?P<brace>\{{)?"
)
_FOR_HEADER = re.compile(r"\bfor(?:\s+await)?\s*\(")
_CONTROL_FLOW_NAMES = frozenset({"catch", "for", "if", "switch", "while", "with"})


@dataclass(frozen=True)
class ApiShadowIndex:
    """Common lexical bindings that can hide an imported/global API root."""

    blocked_import_roots: frozenset[str]
    local_bindings: Mapping[str, tuple[tuple[int, int], ...]]

    def is_shadowed(self, name: str, offset: int) -> bool:
        if name in self.blocked_import_roots:
            return True
        return any(start <= offset <= end for start, end in self.local_bindings.get(name, ()))


def canonical_api_bindings(imports: list[ImportInfo]) -> dict[str, str]:
    """Map local SDK import bindings to their frozen canonical API roots."""

    bindings: dict[str, str] = {}
    for item in imports:
        if not item.module.startswith(SDK_MODULE_PREFIXES):
            continue

        module_prefix = OHOS_MODULE_PREFIXES.get(item.module)
        if item.default_name and module_prefix:
            bindings[item.default_name] = module_prefix
        if item.namespace_name and module_prefix:
            bindings[item.namespace_name] = module_prefix

        for local_name, imported_name in item.named.items():
            if imported_name == "default":
                if module_prefix:
                    bindings[local_name] = module_prefix
                continue
            bindings[local_name] = imported_name
    return bindings


def build_api_shadow_index(
    masked_source: str,
    imports: list[ImportInfo],
    sdk_bindings: Mapping[str, str],
) -> ApiShadowIndex:
    """Build a scope index for common parameter and simple local bindings.

    Destructuring bindings remain intentionally unsupported until the L1 sidecar
    exposes occurrence-level call and scope data.
    """

    brace_pairs = _brace_pairs(masked_source)
    paren_pairs = _delimiter_pairs(masked_source, "(", ")")
    root_scope = (0, max(0, len(masked_source) - 1))
    callable_scopes: set[tuple[int, int]] = set()
    bindings: dict[str, list[tuple[int, int]]] = {}
    for_scopes = _for_scopes(masked_source, brace_pairs, paren_pairs)

    for match in _CALLABLE_PARAMETERS.finditer(masked_source):
        if match.group("name") in _CONTROL_FLOW_NAMES:
            continue
        if _is_property_access(masked_source, match.start("name")):
            continue
        scope = brace_pairs.get(match.start("brace"))
        if scope is None:
            continue
        callable_scopes.add(scope)
        for name in _parameter_names(match.group("params")):
            bindings.setdefault(name, []).append(scope)

    for match in _ARROW_PARAMETERS.finditer(masked_source):
        brace_start = match.start("brace") if match.group("brace") else None
        if brace_start is not None:
            scope = brace_pairs.get(brace_start)
            if scope is not None:
                callable_scopes.add(scope)
        else:
            scope = (match.end(), _arrow_expression_end(masked_source, match.end()))
        if scope is None:
            continue
        for name in _parameter_names(match.group("params")):
            bindings.setdefault(name, []).append(scope)

    all_scopes = (root_scope, *brace_pairs.values())
    for match in _LOCAL_DECLARATION.finditer(masked_source):
        containing = [scope for scope in all_scopes if scope[0] < match.start() < scope[1]]
        scope = min(containing, key=lambda item: item[1] - item[0], default=root_scope)
        kind = match.group("kind")
        if kind == "var":
            function_scopes = [
                item for item in callable_scopes if item[0] < match.start() < item[1]
            ]
            scope = min(
                function_scopes,
                key=lambda item: item[1] - item[0],
                default=root_scope,
            )
        else:
            matching_loops = [
                loop_scope
                for header_start, header_end, loop_scope in for_scopes
                if header_start < match.start() < header_end
            ]
            if matching_loops:
                scope = min(matching_loops, key=lambda item: item[1] - item[0])
        bindings.setdefault(match.group("name"), []).append(scope)

    imported_roots = {
        name
        for item in imports
        for name in (
            item.default_name,
            item.namespace_name,
            *item.named.keys(),
        )
        if name
    }
    return ApiShadowIndex(
        blocked_import_roots=frozenset(imported_roots - sdk_bindings.keys()),
        local_bindings={name: tuple(ranges) for name, ranges in bindings.items()},
    )


def canonicalize_api_call(
    call: str,
    bindings: Mapping[str, str],
    *,
    allow_canonical_root: bool = False,
) -> str | None:
    """Return a platform API name only when its receiver ownership is proven."""

    normalized = "".join(call.split()).replace("?.", ".")
    if normalized in GLOBAL_APIS:
        return normalized
    if not _STATIC_MEMBER_CALL.fullmatch(normalized):
        return None

    head, tail = normalized.split(".", 1)
    canonical_head = bindings.get(head)
    if canonical_head is None and allow_canonical_root and head in bindings.values():
        canonical_head = head
    if canonical_head is None:
        return None
    return f"{canonical_head}.{tail}"


def api_call_root(call: str) -> str:
    return re.split(r"\?\.|\.", "".join(call.split()), maxsplit=1)[0]


def _brace_pairs(source: str) -> dict[int, tuple[int, int]]:
    return _delimiter_pairs(source, "{", "}")


def _delimiter_pairs(
    source: str,
    opening: str,
    closing: str,
) -> dict[int, tuple[int, int]]:
    stack: list[int] = []
    pairs: dict[int, tuple[int, int]] = {}
    for offset, char in enumerate(source):
        if char == opening:
            stack.append(offset)
        elif char == closing and stack:
            start = stack.pop()
            pairs[start] = (start, offset)
    return pairs


def _for_scopes(
    source: str,
    brace_pairs: Mapping[int, tuple[int, int]],
    paren_pairs: Mapping[int, tuple[int, int]],
) -> tuple[tuple[int, int, tuple[int, int]], ...]:
    scopes: list[tuple[int, int, tuple[int, int]]] = []
    for match in _FOR_HEADER.finditer(source):
        paren_start = source.find("(", match.start(), match.end())
        header = paren_pairs.get(paren_start)
        if header is None:
            continue
        index = header[1] + 1
        while index < len(source) and source[index].isspace():
            index += 1
        if index < len(source) and source[index] == "{":
            body = brace_pairs.get(index)
        else:
            statement_end = source.find(";", index)
            body = (
                (index, statement_end)
                if statement_end >= index
                else (index, max(index, len(source) - 1))
            )
        if body is not None:
            scopes.append((header[0], header[1], (header[0], body[1])))
    return tuple(scopes)


def _parameter_names(parameters: str) -> set[str]:
    text = parameters.strip().removeprefix("(").removesuffix(")")
    names: set[str] = set()
    for parameter in _split_top_level(text):
        match = re.match(rf"\s*(?:\.\.\.\s*)?(?P<name>{_IDENTIFIER})", parameter)
        if match:
            names.add(match.group("name"))
    return names


def _split_top_level(text: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    paren_depth = bracket_depth = brace_depth = angle_depth = 0
    for offset, char in enumerate(text):
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == "<":
            angle_depth += 1
        elif char == ">":
            angle_depth = max(0, angle_depth - 1)
        elif char == "," and not any(
            (paren_depth, bracket_depth, brace_depth, angle_depth)
        ):
            parts.append(text[start:offset])
            start = offset + 1
    parts.append(text[start:])
    return tuple(parts)


def _arrow_expression_end(source: str, start: int) -> int:
    paren_depth = bracket_depth = brace_depth = 0
    for offset in range(start, len(source)):
        char = source[offset]
        if char == "(":
            paren_depth += 1
        elif char == ")":
            if paren_depth == 0:
                return max(start, offset - 1)
            paren_depth -= 1
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            if bracket_depth == 0:
                return max(start, offset - 1)
            bracket_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            if brace_depth == 0:
                return max(start, offset - 1)
            brace_depth -= 1
        elif char in ",;" and paren_depth == bracket_depth == brace_depth == 0:
            return max(start, offset - 1)
    return max(start, len(source) - 1)


def _is_property_access(source: str, offset: int) -> bool:
    index = offset - 1
    while index >= 0 and source[index].isspace():
        index -= 1
    return index >= 0 and source[index] == "."
