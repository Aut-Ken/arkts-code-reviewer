from __future__ import annotations

import unittest
from pathlib import Path

from arkts_code_reviewer.code_analysis import ArktsTreeSitterParser, CodeAnalyzer, LexicalParser
from arkts_code_reviewer.code_analysis.models import FileHunk, FileInput
from arkts_code_reviewer.code_analysis.tagger import derive_tags

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_NODE_MODULE = REPO_ROOT / "sidecars" / "arkts-parser" / "node_modules" / "tree-sitter-arkts"


SAMPLE = """import img from '@ohos.multimedia.image'
import router from '@ohos.router'

@Entry
@Component
struct PhotoWall {
  @State photos: PixelMap[] = []
  private timerId: number = 0

  aboutToDisappear() {
    clearInterval(this.timerId)
  }

  async loadImages() {
    const pixelMap = await img.createPixelMap(buffer)
    this.photos.push(pixelMap)
    this.timerId = setInterval(() => {
      router.pushUrl({ url: 'pages/Detail' })
    }, 1000)
  }

  build() {
    Column() {
      Grid() {
        ForEach(this.photos, (photo: PixelMap) => {
          Image(photo)
            .objectFit(ImageFit.Cover)
            .onClick(() => this.loadImages())
        })
      }
    }
  }
}
"""


ANIMATION_SAMPLE = """import router from '@ohos.router';
@Entry
@Component
struct AnimationExample {
  @State rotateAngle: number = 0

  build() {
    Column() {
      Column() {
        Button("返回")
          .onClick(() => {
            router.back()
          })
      }
      Column() {
        Button('change rotate angle')
          .onClick(() => {
            this.rotateAngle = 90
          })
          .margin(50)
          .rotate({ angle: this.rotateAngle })
          .animation({
            duration: 1200,
          })
      }
    }
  }
}
"""


CUSTOM_COMPONENT_SAMPLE = """import { window } from '@kit.ArkUI';

@ComponentV2
struct ImageGeneratorDialog {
  private curWindow?: window.Window = undefined;

  @Builder
  pageMap(name: string) {
    if (name === 'home') {
      CanvasHome({})
    } else {
      TextTouchUpComponent()
    }
  }

  initWinSizeChangeCallback() {
    this.curWindow.on('windowSizeChange', () => {})
  }

  build() {
    Navigation() {
    }
      .hideNavBar(true)
  }
}
"""


class LexicalParserTest(unittest.TestCase):
    def test_extracts_core_arkts_facts(self) -> None:
        facts = LexicalParser().parse(SAMPLE, "src/pages/PhotoWall.ets")

        self.assertIn("@State", facts.decorators)
        self.assertIn("Image", facts.components)
        self.assertIn("Grid", facts.components)
        self.assertIn("Column", facts.components)
        self.assertIn("image.createPixelMap", facts.apis)
        self.assertIn("router.pushUrl", facts.apis)
        self.assertIn("setInterval", facts.apis)
        self.assertIn("objectFit", facts.attributes)
        self.assertIn("onClick", facts.attributes)
        self.assertIn("async_fn", facts.syntax)
        self.assertIn("await_expr", facts.syntax)
        self.assertTrue(
            any(item.qualified_name == "PhotoWall.loadImages" for item in facts.declarations)
        )

    def test_parses_multiple_and_multiline_named_imports(self) -> None:
        source = """import {
  RadioBlock,
  useEnabled,
  SliderBlock,
  IconBlock,
  ColorBlock
} from 'common';
import {
  matrix4,
  LengthMetrics,
  ColorMetrics
} from '@kit.ArkUI';
"""

        facts = LexicalParser().parse(source, "src/pages/ImageBootcamp.ets")
        imports = {item.module: item.named for item in facts.imports}

        self.assertEqual(
            imports["common"],
            {
                "RadioBlock": "RadioBlock",
                "useEnabled": "useEnabled",
                "SliderBlock": "SliderBlock",
                "IconBlock": "IconBlock",
                "ColorBlock": "ColorBlock",
            },
        )
        self.assertEqual(
            imports["@kit.ArkUI"],
            {
                "matrix4": "matrix4",
                "LengthMetrics": "LengthMetrics",
                "ColorMetrics": "ColorMetrics",
            },
        )

    def test_ignores_fake_imports_and_parses_lazy_imports(self) -> None:
        source = """import router from '@ohos.router'
/*
import fakeHttp from '@ohos.net.http'
import '@ohos.fake'
*/
const example = `
import fakeImage from '@ohos.multimedia.image'
`
import lazy { Foo as Bar } from 'mod'
import lazy * as tools from 'toolkit'
import lazy '@ohos.hilog'
import lazy from 'plain'
"""

        facts = LexicalParser().parse(source, "src/pages/Imports.ets")
        imports = {item.module: item for item in facts.imports}

        self.assertEqual(set(imports), {"@ohos.router", "mod", "toolkit", "@ohos.hilog", "plain"})
        self.assertEqual(imports["@ohos.router"].default_name, "router")
        self.assertEqual(imports["mod"].named, {"Bar": "Foo"})
        self.assertEqual(imports["toolkit"].namespace_name, "tools")
        self.assertIsNone(imports["@ohos.hilog"].default_name)
        self.assertEqual(imports["@ohos.hilog"].named, {})
        self.assertEqual(imports["plain"].default_name, "lazy")


@unittest.skipUnless(
    SIDECAR_NODE_MODULE.exists(),
    "ArkTS tree-sitter sidecar dependencies are not installed",
)
class ArktsTreeSitterParserTest(unittest.TestCase):
    def test_extracts_l1_ast_facts_and_filters_internal_calls(self) -> None:
        facts = ArktsTreeSitterParser().parse(SAMPLE, "src/pages/PhotoWall.ets")

        self.assertEqual(facts.parser_layer, "L1")
        self.assertIn("Image", facts.components)
        self.assertIn("Grid", facts.components)
        self.assertIn("@State", facts.decorators)
        self.assertIn("image.createPixelMap", facts.apis)
        self.assertIn("router.pushUrl", facts.apis)
        self.assertNotIn("this.loadImages", facts.apis)
        self.assertIn("objectFit", facts.attributes)
        self.assertIn("onClick", facts.attributes)
        self.assertIn("await_expr", facts.syntax)
        self.assertTrue(
            any(item.qualified_name == "PhotoWall.build" for item in facts.declarations)
        )
        self.assertTrue(
            any(item.kind == "ui_block" and item.name == "Image" for item in facts.declarations)
        )

    def test_preserves_animation_modifiers_and_imported_router_api(self) -> None:
        facts = ArktsTreeSitterParser().parse(
            ANIMATION_SAMPLE,
            "examples/Animation/entry/src/main/ets/pages/animation.ets",
        )

        self.assertEqual(facts.parser_layer, "L1")
        self.assertIn("rotate", facts.attributes)
        self.assertIn("animation", facts.attributes)
        self.assertIn("onClick", facts.attributes)
        self.assertIn("router.back", facts.apis)
        self.assertFalse(any(api.startswith("Button") for api in facts.apis))
        self.assertFalse(any("changerotateangle" in api for api in facts.apis))

        tags = derive_tags(facts)
        self.assertNotIn("has_custom_component", tags)

    def test_preserves_custom_ui_blocks_and_filters_non_ui_on_method(self) -> None:
        parser = ArktsTreeSitterParser()
        facts = parser.parse(CUSTOM_COMPONENT_SAMPLE, "src/pages/Main.ets")

        self.assertEqual(facts.parser_layer, "L1")
        self.assertIn("Navigation", facts.components)
        self.assertIn("CanvasHome", facts.components)
        self.assertIn("TextTouchUpComponent", facts.components)
        self.assertIn("hideNavBar", facts.attributes)
        self.assertNotIn("on", facts.attributes)
        self.assertTrue(
            any(
                item.kind == "ui_block" and item.name == "CanvasHome"
                for item in facts.declarations
            )
        )

        result = CodeAnalyzer(parser=parser).analyze_file(
            path="src/pages/Main.ets",
            content=CUSTOM_COMPONENT_SAMPLE,
        )
        self.assertIn("@ComponentV2", result.retrieval_query.units[0].code_features.decorators)


class CodeAnalyzerTest(unittest.TestCase):
    def test_builds_diff_review_unit_and_retrieval_features(self) -> None:
        result = CodeAnalyzer(parser=LexicalParser()).analyze_files(
            [
                FileInput(
                    path="src/pages/PhotoWall.ets",
                    content=SAMPLE,
                    hunks=[FileHunk(new_start=13, new_lines=4)],
                )
            ],
            mode="diff",
        )

        self.assertEqual(len(result.review_units), 1)
        unit = result.review_units[0]
        self.assertEqual(unit.unit_ref, "PhotoWall.loadImages@src/pages/PhotoWall.ets")
        self.assertEqual(unit.host_summary.struct, "PhotoWall")
        self.assertIn(13, unit.file_changed_lines)

        retrieval_unit = result.retrieval_query.units[0]
        self.assertIn("has_image", retrieval_unit.code_features.tags)
        self.assertIn("has_timer", retrieval_unit.code_features.tags)
        self.assertIn("has_async", retrieval_unit.code_features.tags)
        self.assertIn("DIM-06", result.retrieval_query.mr_context.triggered_dimensions)
        self.assertIn("DIM-07", result.retrieval_query.mr_context.triggered_dimensions)

    def test_falls_back_to_hunk_window_when_no_declaration_matches(self) -> None:
        result = CodeAnalyzer(parser=LexicalParser()).analyze_file(
            path="src/pages/plain.ets",
            content="const a = 1\nconst b = 2\n",
            mode="diff",
            hunks=[(2, 1)],
        )

        unit = result.review_units[0]
        self.assertTrue(unit.context_degraded)
        self.assertEqual(unit.unit_ref, "hunk-L2-L2@src/pages/plain.ets")

    def test_default_analyzer_degrades_when_tree_sitter_is_unavailable(self) -> None:
        result = CodeAnalyzer().analyze_file(
            path="src/pages/PhotoWall.ets",
            content=SAMPLE,
            mode="diff",
            hunks=[(15, 2)],
        )

        self.assertEqual(
            result.review_units[0].unit_ref,
            "PhotoWall.loadImages@src/pages/PhotoWall.ets",
        )
        self.assertIn(result.metadata.parser_layer, {"L0", "L1", "parse_degraded"})


if __name__ == "__main__":
    unittest.main()
