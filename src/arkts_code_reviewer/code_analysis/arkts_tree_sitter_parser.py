from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.models import CodeFacts, Declaration, SourceSpan
from arkts_code_reviewer.code_analysis.text_utils import extract_lines

ATTACHED_DECORATOR_LINE = re.compile(
    r"^@[A-Za-z_$][A-Za-z0-9_$]*(?:\s*\(.*\))?$"
)


class ArktsTreeSitterParser:
    """L1 ArkTS parser backed by the Node tree-sitter sidecar.

    The sidecar is optional. When it is unavailable or fails, this parser returns
    the L0 facts from ``LexicalParser`` with a warning instead of blocking review.
    """

    def __init__(
        self,
        fallback: LexicalParser | None = None,
        sidecar_path: Path | None = None,
        node_executable: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.fallback = fallback or LexicalParser()
        self.node_executable = node_executable or os.getenv("ARKTS_PARSER_NODE", "node")
        self.timeout_seconds = timeout_seconds or float(os.getenv("ARKTS_PARSER_TIMEOUT", "20"))
        self.sidecar_path = sidecar_path or self._default_sidecar_path()

    def parse(self, source: str, path: str) -> CodeFacts:
        facts = self.fallback.parse(source, path)
        if not self.sidecar_path.exists():
            facts.warnings.append(f"arkts_tree_sitter_unavailable: {self.sidecar_path}")
            return facts

        try:
            snapshot = self._run_sidecar(source, path)
        except Exception as exc:
            facts.parser_layer = "parse_degraded"
            facts.warnings.append(f"arkts_tree_sitter_failed: {exc}")
            return facts

        if error := snapshot.get("error"):
            facts.parser_layer = "parse_degraded"
            facts.warnings.append(f"arkts_tree_sitter_failed: {error}")
            return facts

        self._merge_snapshot(facts, source, snapshot)
        facts.parser_layer = "L1"
        error_nodes = int(snapshot.get("error_nodes", 0))
        missing_nodes = int(snapshot.get("missing_nodes", 0))
        if error_nodes:
            facts.warnings.append(f"arkts_tree_sitter_error_nodes: {error_nodes}")
        if missing_nodes:
            facts.warnings.append(f"arkts_tree_sitter_missing_nodes: {missing_nodes}")
        return facts

    def _run_sidecar(self, source: str, path: str) -> dict[str, Any]:
        completed = subprocess.run(
            [self.node_executable, str(self.sidecar_path), "--path", path],
            input=source.encode("utf-8"),
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8", errors="replace").strip()
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        if not stdout:
            message = stderr or f"node exited with code {completed.returncode}"
            raise RuntimeError(message)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid sidecar JSON: {exc}") from exc
        if completed.returncode != 0 and "error" not in data:
            data["error"] = stderr or f"node exited with code {completed.returncode}"
        if not isinstance(data, dict):
            raise RuntimeError("sidecar JSON root is not an object")
        return data

    def _merge_snapshot(self, facts: CodeFacts, source: str, snapshot: dict[str, Any]) -> None:
        facts.decorators.update(self._string_set(snapshot.get("decorators")))
        facts.attributes = self._string_set(snapshot.get("attributes"))
        facts.syntax.update(self._string_set(snapshot.get("syntax")))

        snapshot_declarations = snapshot.get("declarations")
        declarations = self._parse_declarations(source, snapshot_declarations)
        if isinstance(snapshot_declarations, list):
            facts.declarations = declarations
            facts.symbols = {
                symbol
                for declaration in declarations
                for symbol in (declaration.name, declaration.qualified_name)
            }
            facts.components = {
                declaration.name
                for declaration in declarations
                if declaration.kind == "ui_block"
            }

    def _parse_declarations(self, source: str, value: object) -> list[Declaration]:
        if not isinstance(value, list):
            return []
        declarations: list[Declaration] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            span_data = item.get("span")
            if not isinstance(span_data, dict):
                continue
            kind = item.get("kind")
            name = item.get("name")
            qualified_name = item.get("qualified_name")
            if not all(isinstance(part, str) for part in (kind, name, qualified_name)):
                continue
            valid_kinds = {
                "struct",
                "class",
                "function",
                "method",
                "build_method",
                "builder",
                "ui_block",
            }
            if kind not in valid_kinds:
                continue
            span = self._span_with_leading_decorators(
                SourceSpan(
                    start_line=int(span_data.get("start_line", 1)),
                    end_line=int(span_data.get("end_line", 1)),
                    start_col=int(span_data.get("start_col", 0)),
                    end_col=int(span_data.get("end_col", 0)),
                ),
                source,
                str(kind),
            )
            parent_name = item.get("parent_name")
            declarations.append(
                Declaration(
                    kind=kind,  # type: ignore[arg-type]
                    name=name,
                    qualified_name=qualified_name,
                    span=span,
                    parent_name=parent_name if isinstance(parent_name, str) else None,
                    text=extract_lines(source, span.start_line, span.end_line),
                )
            )
        declarations.sort(
            key=lambda item: (item.span.start_line, item.span.end_line, item.qualified_name)
        )
        return declarations

    def _span_with_leading_decorators(
        self,
        span: SourceSpan,
        source: str,
        kind: str,
    ) -> SourceSpan:
        if kind not in {"struct", "class", "function", "method", "build_method", "builder"}:
            return span

        lines = source.splitlines()
        start_line = span.start_line
        while start_line > 1:
            previous = lines[start_line - 2].strip()
            if not ATTACHED_DECORATOR_LINE.fullmatch(previous):
                break
            start_line -= 1
        if start_line == span.start_line:
            return span
        return SourceSpan(
            start_line=start_line,
            end_line=span.end_line,
            start_col=1,
            end_col=span.end_col,
        )

    def _string_set(self, value: object) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {item for item in value if isinstance(item, str) and item}

    def _default_sidecar_path(self) -> Path:
        package_root = Path(__file__).resolve().parents[3]
        configured = os.getenv("ARKTS_PARSER_SIDECAR")
        if configured:
            return Path(configured)
        return package_root / "sidecars" / "arkts-parser" / "parse_arkts.js"
