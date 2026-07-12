"""Code analysis module for ArkTS review units and retrieval features."""

from arkts_code_reviewer.code_analysis.analyzer import CodeAnalyzer
from arkts_code_reviewer.code_analysis.arkts_tree_sitter_parser import ArktsTreeSitterParser
from arkts_code_reviewer.code_analysis.file_analysis_models import (
    FILE_ANALYSIS_SCHEMA_VERSION,
    CodeSourceRef,
    DeclarationOccurrence,
    ExactRange,
    FactKind,
    FactOccurrence,
    FileAnalysis,
    FileParseResult,
    FileParserQuality,
    OwnerRef,
    ReviewRegion,
    ScopedFacts,
    UnitFactScope,
)
from arkts_code_reviewer.code_analysis.file_analysis_parser import (
    ArktsFileAnalysisParser,
    FileAnalysisParser,
    LegacyFileAnalysisAdapter,
)
from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.models import (
    REVIEW_UNIT_BUILD_SCHEMA_VERSION,
    AnalysisResult,
    CodeFacts,
    CodeFeatures,
    Declaration,
    FileHunk,
    FileInput,
    ParserQuality,
    ReviewUnit,
    ReviewUnitBuildResult,
    ReviewUnitDiagnostic,
    ReviewUnitFileResult,
    ReviewUnitSpan,
)
from arkts_code_reviewer.code_analysis.tree_sitter_parser import TreeSitterParser

__all__ = [
    "AnalysisResult",
    "ArktsFileAnalysisParser",
    "ArktsTreeSitterParser",
    "CodeSourceRef",
    "CodeAnalyzer",
    "CodeFacts",
    "CodeFeatures",
    "Declaration",
    "DeclarationOccurrence",
    "ExactRange",
    "FactKind",
    "FactOccurrence",
    "FILE_ANALYSIS_SCHEMA_VERSION",
    "FileAnalysis",
    "FileInput",
    "FileHunk",
    "FileAnalysisParser",
    "FileParseResult",
    "FileParserQuality",
    "LegacyFileAnalysisAdapter",
    "LexicalParser",
    "ParserQuality",
    "OwnerRef",
    "REVIEW_UNIT_BUILD_SCHEMA_VERSION",
    "ReviewUnit",
    "ReviewUnitBuildResult",
    "ReviewUnitDiagnostic",
    "ReviewUnitFileResult",
    "ReviewRegion",
    "ReviewUnitSpan",
    "ScopedFacts",
    "TreeSitterParser",
    "UnitFactScope",
]
