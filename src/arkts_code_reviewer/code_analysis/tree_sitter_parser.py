from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.models import CodeFacts


class TreeSitterParser:
    """Optional L1 parser.

    For now tree-sitter is used as a syntax health check. Deterministic fact
    extraction still comes from L0 so the analyzer remains usable without the
    optional binary parser dependencies.
    """

    def __init__(self, fallback: LexicalParser | None = None) -> None:
        self.fallback = fallback or LexicalParser()
        self._parser: Any | None = None
        self._load_error: str | None = None

    def parse(self, source: str, path: str) -> CodeFacts:
        parser = self._load_parser()
        facts = self.fallback.parse(source, path)
        if parser is None:
            facts.warnings.append(f"tree_sitter_unavailable: {self._load_error}")
            return facts

        try:
            prepared = self._preprocess_arkts(source)
            tree = parser.parse(prepared.encode("utf-8"))
            error_nodes = sum(1 for node in self._walk(tree.root_node) if node.type == "ERROR")
            facts.parser_layer = "L1"
            if error_nodes:
                facts.warnings.append(f"tree_sitter_error_nodes: {error_nodes}")
            return facts
        except Exception as exc:  # pragma: no cover - optional dependency path.
            facts.parser_layer = "parse_degraded"
            facts.warnings.append(f"tree_sitter_parse_failed: {exc}")
            return facts

    def _load_parser(self) -> Any | None:
        if self._parser is not None:
            return self._parser
        if self._load_error:
            return None
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_typescript as tstypescript

            language_factory = getattr(tstypescript, "language_typescript", None)
            if language_factory is None:
                language_factory = getattr(tstypescript, "language_tsx", None)
            if language_factory is None:
                raise RuntimeError("tree_sitter_typescript has no TypeScript language factory")

            language = Language(language_factory())
            parser = Parser()
            if hasattr(parser, "language"):
                parser.language = language
            else:
                parser.set_language(language)
            self._parser = parser
            return parser
        except Exception as exc:  # pragma: no cover - depends on local environment.
            self._load_error = str(exc)
            return None

    def _preprocess_arkts(self, source: str) -> str:
        return source.replace("struct ", "class  ")

    def _walk(self, root: Any) -> Iterator[Any]:
        stack = [root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(getattr(node, "children", [])))
