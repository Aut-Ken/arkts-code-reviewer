"""Independent Golden validation for the structured ChangeSet contract."""

from arkts_code_reviewer.change_set_validation.golden import (
    ChangeSetGoldenCase,
    ChangeSetGoldenSuite,
    assert_strict_baseline,
    evaluate_golden_suite,
    is_perfect,
    load_golden_suite,
    write_current_baseline,
)

__all__ = [
    "ChangeSetGoldenCase",
    "ChangeSetGoldenSuite",
    "assert_strict_baseline",
    "evaluate_golden_suite",
    "is_perfect",
    "load_golden_suite",
    "write_current_baseline",
]
