"""Independent Golden validation for ChangeSet-aware ReviewUnit v2 output."""

from arkts_code_reviewer.review_unit_v2_validation.golden import (
    BASELINE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    ReviewUnitV2GoldenCase,
    ReviewUnitV2GoldenSuite,
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    reports_equal,
    write_current_baseline,
)

__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ReviewUnitV2GoldenCase",
    "ReviewUnitV2GoldenSuite",
    "assert_strict_baseline",
    "evaluate_golden_suite",
    "is_perfect",
    "load_golden_suite",
    "reports_equal",
    "write_current_baseline",
]
