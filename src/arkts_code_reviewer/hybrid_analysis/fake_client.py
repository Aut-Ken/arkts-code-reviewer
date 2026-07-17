from __future__ import annotations

from collections.abc import Sequence

from arkts_code_reviewer.hybrid_analysis.dispatch import VerifiedAITagDispatchEnvelope
from arkts_code_reviewer.hybrid_analysis.execution import (
    AITagRawCompletion,
    AITagTransportFailure,
)

ScriptedFakeResponse = AITagRawCompletion | AITagTransportFailure


class ScriptedFakeDeepSeekClient:
    """Test-only scripted source of raw completions or normalized failures.

    This class performs no network access and deliberately cannot construct a
    formal AITagAnalysisResult or AITagExecutionOutcome.
    """

    def __init__(self, script: Sequence[ScriptedFakeResponse]) -> None:
        if not script:
            raise ValueError("ScriptedFakeDeepSeekClient script must not be empty")
        if any(item.source_kind != "scripted_fixture" for item in script):
            raise ValueError("ScriptedFakeDeepSeekClient accepts scripted_fixture inputs only")
        self._script = tuple(script)
        self._next_index = 0

    @property
    def invocation_count(self) -> int:
        return self._next_index

    def complete(
        self,
        envelope: VerifiedAITagDispatchEnvelope,
    ) -> ScriptedFakeResponse:
        VerifiedAITagDispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
        if self._next_index >= len(self._script):
            raise RuntimeError("ScriptedFakeDeepSeekClient script is exhausted")
        response = self._script[self._next_index]
        self._next_index += 1
        return response


__all__ = ["ScriptedFakeDeepSeekClient", "ScriptedFakeResponse"]
