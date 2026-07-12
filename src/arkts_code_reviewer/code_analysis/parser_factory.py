from __future__ import annotations

from typing import Literal

from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import ArktsTreeSitterParser
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
    LegacyFileAnalysisAdapter,
)
from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.models import CodeParser

ParserChoice = Literal["lexical", "arkts-tree-sitter"]
PARSER_CHOICES: tuple[ParserChoice, ...] = ("lexical", "arkts-tree-sitter")


def create_code_parser(parser_name: ParserChoice) -> CodeParser:
    if parser_name == "lexical":
        return LexicalParser()
    if parser_name == "arkts-tree-sitter":
        return ArktsTreeSitterParser()
    raise ValueError(f"Unsupported parser: {parser_name}")


def create_file_analysis_parser(
    parser_name: ParserChoice,
) -> ArktsFileAnalysisParser | LegacyFileAnalysisAdapter:
    if parser_name == "lexical":
        return LegacyFileAnalysisAdapter(LexicalParser())
    if parser_name == "arkts-tree-sitter":
        return ArktsFileAnalysisParser()
    raise ValueError(f"Unsupported parser: {parser_name}")


def parser_display_name(parser_name: ParserChoice) -> str:
    if parser_name == "lexical":
        return "LexicalParser"
    if parser_name == "arkts-tree-sitter":
        return "ArktsTreeSitterParser"
    raise ValueError(f"Unsupported parser: {parser_name}")
