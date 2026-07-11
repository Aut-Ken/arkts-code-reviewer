from __future__ import annotations

import re

from arkts_code_reviewer.code_analysis.arkts_lexicon import (
    ARKUI_ATTRIBUTES,
    DEFAULT_ARKUI_COMPONENTS,
    GLOBAL_APIS,
    OHOS_MODULE_PREFIXES,
)
from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    Declaration,
    ImportInfo,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.text_utils import (
    extract_lines,
    find_matching_brace,
    line_starts,
    mask_comments_and_strings,
    offset_to_line_col,
)

_IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"
_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return"}


class LexicalParser:
    """Deterministic L0 parser based on token-like regexes and brace matching."""

    import_from_pattern = re.compile(
        r"^[ \t]*(?P<keyword>import)\s+"
        r"(?P<clause>(?:(?!^[ \t]*import\b)[\s\S])+?)\s+from\s+"
        r"['\"](?P<module>[^'\"]+)['\"]\s*;?",
        re.MULTILINE,
    )
    import_side_effect_pattern = re.compile(
        r"^[ \t]*(?P<keyword>import)\s+(?:lazy\s+)?"
        r"['\"](?P<module>[^'\"]+)['\"]\s*;?",
        re.MULTILINE,
    )
    decorator_pattern = re.compile(r"@[A-Za-z_][A-Za-z0-9_]*")
    call_pattern = re.compile(rf"\b(?P<name>{_IDENT})\s*\(")
    dotted_call_pattern = re.compile(
        rf"\b(?P<object>{_IDENT})\s*\.\s*(?P<member>{_IDENT})\s*\("
    )
    attribute_pattern = re.compile(rf"\.\s*(?P<name>{_IDENT})\s*\(")
    struct_pattern = re.compile(
        r"\b(?P<kind>struct|class)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)[^{;]*\{"
    )
    callable_block_pattern = re.compile(
        rf"(?P<prefix>(?:@\w+\s*)*(?:(?:public|private|protected|static|async|export)\s+)*)"
        rf"\b(?P<name>{_IDENT})\s*\([^;{{}}]*\)\s*(?::\s*[^{{;]+)?\{{"
    )

    def parse(self, source: str, path: str) -> CodeFacts:
        masked = mask_comments_and_strings(source)
        starts = line_starts(source)
        imports = self._parse_imports(source, masked)
        alias_prefixes = self._alias_prefixes(imports)
        declarations = self._parse_declarations(source, masked, starts)

        facts = CodeFacts(
            path=path, imports=imports, parser_layer="L0", declarations=declarations
        )
        facts.decorators.update(self.decorator_pattern.findall(masked))
        facts.symbols.update(declaration.qualified_name for declaration in declarations)
        facts.symbols.update(declaration.name for declaration in declarations)
        facts.components.update(self._parse_components(masked, declarations))
        facts.apis.update(self._parse_apis(masked, alias_prefixes))
        facts.attributes.update(self._parse_attributes(masked))
        facts.syntax.update(self._parse_syntax(masked))
        return facts

    def _parse_imports(self, source: str, masked: str) -> list[ImportInfo]:
        imports: list[ImportInfo] = []
        for match in self.import_from_pattern.finditer(source):
            if not self._import_starts_in_code(masked, match):
                continue
            imports.append(
                self._parse_import_clause(match.group("clause"), match.group("module"))
            )
        covered = {item.module for item in imports}
        for match in self.import_side_effect_pattern.finditer(source):
            if not self._import_starts_in_code(masked, match):
                continue
            module = match.group("module")
            if module not in covered:
                imports.append(ImportInfo(module=module))
        return imports

    def _import_starts_in_code(self, masked: str, match: re.Match[str]) -> bool:
        return masked[match.start("keyword") : match.end("keyword")] == "import"

    def _parse_import_clause(self, clause: str, module: str) -> ImportInfo:
        clause = " ".join(clause.split())
        if clause.startswith("lazy "):
            clause = clause.removeprefix("lazy ").lstrip()
        default_name: str | None = None
        namespace_name: str | None = None
        named: dict[str, str] = {}

        if clause.startswith("{"):
            named.update(self._parse_named_imports(clause))
        elif clause.startswith("* as "):
            namespace_name = clause.removeprefix("* as ").strip()
        elif ", {" in clause:
            default_part, named_part = clause.split(", {", 1)
            default_name = default_part.strip()
            named.update(self._parse_named_imports("{" + named_part))
        elif ", * as " in clause:
            default_part, namespace_part = clause.split(", * as ", 1)
            default_name = default_part.strip()
            namespace_name = namespace_part.strip()
        elif clause:
            default_name = clause

        return ImportInfo(
            module=module,
            default_name=default_name or None,
            namespace_name=namespace_name or None,
            named=named,
        )

    def _parse_named_imports(self, clause: str) -> dict[str, str]:
        content = clause.strip().strip("{}")
        named: dict[str, str] = {}
        for item in content.split(","):
            item = item.strip()
            if not item:
                continue
            if " as " in item:
                imported, local = [part.strip() for part in item.split(" as ", 1)]
            else:
                imported = local = item
            named[local] = imported
        return named

    def _alias_prefixes(self, imports: list[ImportInfo]) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for item in imports:
            prefix = OHOS_MODULE_PREFIXES.get(item.module)
            if prefix is None and item.module.startswith("@ohos."):
                prefix = item.module.rsplit(".", 1)[-1]
            if prefix is None:
                continue
            if item.default_name:
                aliases[item.default_name] = prefix
            if item.namespace_name:
                aliases[item.namespace_name] = prefix
            for local, imported in item.named.items():
                aliases[local] = OHOS_MODULE_PREFIXES.get(
                    f"{item.module}.{imported}", imported
                )
        return aliases

    def _parse_components(
        self, masked: str, declarations: list[Declaration]
    ) -> set[str]:
        components: set[str] = set()
        for match in self.call_pattern.finditer(masked):
            name = match.group("name")
            if name in DEFAULT_ARKUI_COMPONENTS:
                components.add(name)
        for declaration in declarations:
            if declaration.kind == "ui_block":
                components.add(declaration.name)
        return components

    def _parse_apis(self, masked: str, aliases: dict[str, str]) -> set[str]:
        apis: set[str] = set()
        for match in self.dotted_call_pattern.finditer(masked):
            obj = match.group("object")
            member = match.group("member")
            prefix = aliases.get(obj, obj)
            apis.add(f"{prefix}.{member}")

        for match in self.call_pattern.finditer(masked):
            name = match.group("name")
            if name in GLOBAL_APIS:
                apis.add(name)

        for name in ("$r", "$rawfile"):
            if re.search(rf"{re.escape(name)}\s*\(", masked):
                apis.add(name)
        return apis

    def _parse_attributes(self, masked: str) -> set[str]:
        attributes: set[str] = set()
        for match in self.attribute_pattern.finditer(masked):
            name = match.group("name")
            if name in ARKUI_ATTRIBUTES or re.match(r"on[A-Z]", name):
                attributes.add(name)
        return attributes

    def _parse_syntax(self, masked: str) -> set[str]:
        syntax: set[str] = set()
        if re.search(r"\basync\s+(?:function\s+)?[A-Za-z_$]", masked):
            syntax.add("async_fn")
        if re.search(r"\bawait\b", masked):
            syntax.add("await_expr")
        if re.search(r"\bPromise\b", masked):
            syntax.add("promise")
        if "=>" in masked:
            syntax.add("arrow_fn")
        if re.search(r"\btry\s*\{", masked) or re.search(r"\bcatch\s*\(", masked):
            syntax.add("try_catch")
        return syntax

    def _parse_declarations(
        self, source: str, masked: str, starts: list[int]
    ) -> list[Declaration]:
        declarations: list[Declaration] = []
        seen_spans: set[tuple[int, int, str]] = set()

        for match in self.struct_pattern.finditer(masked):
            declaration = self._declaration_from_match(
                source, masked, starts, match, match.group("kind")
            )
            if declaration:
                declarations.append(declaration)
                seen_spans.add(
                    (
                        declaration.span.start_line,
                        declaration.span.end_line,
                        declaration.name,
                    )
                )

        for match in self.callable_block_pattern.finditer(masked):
            name = match.group("name")
            if name in _KEYWORDS:
                continue
            if self._is_property_chain(masked, match.start("name")):
                continue
            kind = self._callable_kind(name, match.group("prefix"))
            declaration = self._declaration_from_match(
                source, masked, starts, match, kind
            )
            if not declaration:
                continue
            key = (
                declaration.span.start_line,
                declaration.span.end_line,
                declaration.name,
            )
            if key not in seen_spans:
                declarations.append(declaration)
                seen_spans.add(key)

        self._attach_parents(declarations)
        declarations.sort(
            key=lambda item: (
                item.span.start_line,
                item.span.end_line,
                item.qualified_name,
            )
        )
        return declarations

    def _declaration_from_match(
        self,
        source: str,
        masked: str,
        starts: list[int],
        match: re.Match[str],
        kind: str,
    ) -> Declaration | None:
        brace_offset = masked.find("{", match.start(), match.end())
        if brace_offset < 0:
            return None
        close_offset = find_matching_brace(masked, brace_offset)
        if close_offset is None:
            return None
        start_line, start_col = offset_to_line_col(starts, match.start())
        end_line, end_col = offset_to_line_col(starts, close_offset)
        name = match.group("name")
        span = SourceSpan(
            start_line=start_line,
            end_line=end_line,
            start_col=start_col,
            end_col=end_col,
        )
        return Declaration(
            kind=kind,  # type: ignore[arg-type]
            name=name,
            qualified_name=name,
            span=span,
            text=extract_lines(source, start_line, end_line),
        )

    def _callable_kind(self, name: str, prefix: str) -> str:
        if "@Builder" in prefix:
            return "builder"
        if name == "build":
            return "build_method"
        if name in DEFAULT_ARKUI_COMPONENTS or (
            name[:1].isupper() and name not in {"Promise", "Array"}
        ):
            return "ui_block"
        return "function"

    def _is_property_chain(self, masked: str, name_start: int) -> bool:
        index = name_start - 1
        while index >= 0 and masked[index].isspace():
            index -= 1
        return index >= 0 and masked[index] == "."

    def _attach_parents(self, declarations: list[Declaration]) -> None:
        containers = [
            item
            for item in declarations
            if item.kind in {"struct", "class", "build_method", "method"}
        ]
        for declaration in declarations:
            parents = [
                candidate
                for candidate in containers
                if candidate is not declaration
                and candidate.span.contains_line_range(
                    declaration.span.start_line, declaration.span.end_line
                )
            ]
            if not parents:
                continue
            parent = min(parents, key=lambda item: item.line_count)
            declaration.parent_name = parent.qualified_name
            if declaration.kind == "function" and parent.kind in {"struct", "class"}:
                declaration.kind = "method"
            if declaration.kind in {
                "method",
                "build_method",
                "builder",
            } and parent.kind in {
                "struct",
                "class",
            }:
                declaration.qualified_name = f"{parent.name}.{declaration.name}"
            elif declaration.kind == "ui_block":
                declaration.qualified_name = (
                    f"{parent.qualified_name}.{declaration.name}"
                )
