from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from arkts_code_reviewer.code_analysis.lexical import LexicalParser
from arkts_code_reviewer.code_analysis.tagger import derive_tags

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = REPO_ROOT.parent / "arkui_ace_engine"
MANIFEST = REPO_ROOT / "tests" / "fixtures" / "arkui_ace_engine_samples.json"


@unittest.skipUnless(ENGINE_ROOT.exists(), "arkui_ace_engine sibling repository is not available")
class ArkuiAceEngineSamplesTest(unittest.TestCase):
    def test_l0_parser_handles_selected_real_arkts_files(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        parser = LexicalParser()
        parsed = 0
        empty_features: list[str] = []
        categories: Counter[str] = Counter()

        for sample in manifest["samples"]:
            categories[sample["category"]] += 1
            path = sample["path"]
            source_path = ENGINE_ROOT / Path(path)
            self.assertTrue(source_path.exists(), path)
            source = source_path.read_text(encoding="utf-8")
            facts = parser.parse(source, path)
            tags = derive_tags(facts)
            parsed += 1
            if not any((facts.components, facts.apis, facts.decorators, facts.declarations, facts.syntax, tags)):
                empty_features.append(path)

        self.assertGreaterEqual(parsed, 60)
        self.assertGreaterEqual(len(categories), 10)
        self.assertLessEqual(len(empty_features), 2, empty_features[:5])

