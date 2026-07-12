from __future__ import annotations

import unittest
from pathlib import Path

from arkts_code_reviewer.code_analysis import ArktsTreeSitterParser, CodeAnalyzer, LexicalParser
from arkts_code_reviewer.code_analysis.models import (
    CodeFacts,
    Declaration,
    FileHunk,
    FileInput,
    ParserQuality,
    SourceSpan,
)
from arkts_code_reviewer.code_analysis.review_units import ReviewUnitBuilder
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


BUILDER_METHOD_SAMPLE = """@Component
struct BuilderKinds {
  @Builder
  content() {
    Text('content')
  }

  helper() {}

  build() {
    Column() {}
  }
}
"""


UI_BLOCK_MODIFIER_SPAN_SAMPLE = """@Component
struct UiBlockSpan {
  build() {
    Column() {
      Text('first')
      Row() {
        Text('nested')
      }
      .width('100%')
      .margin({ bottom: 1 })
    }
  }
}
"""


PARSER_TAIL_SAMPLE = """@Component
struct ParserTail {
  @State first: number = 0
  @State second: number = 0
  aboutToAppear() {
    this.first = 1
  }

  @Styles
  paddingStyle() {
    .padding(1)
  }

  build() {
    Column() {
      ImageAnimator()
        .images([{ src: 'image.png' }])
        .duration(100)
        .state(AnimationStatus.Running)
      Row() {
        Button('run').width(100).padding(5).onClick(() => {
          this.first = 2
        }).margin(5)
        closeDialog()
      }
      Divider()
        .color(Color.Gray)
    }
  }
}
"""


SPECIAL_UI_CONTEXT_SAMPLE = """@CustomDialog
struct DialogContent {
  build() {
    Text('dialog')
  }
}

@Component
struct SpecialContexts {
  controller: CustomDialogController = new CustomDialogController({
    builder: DialogContent()
  })

  runBusiness() {
    Factory().start()
    TestRunner.bind(this)(task)
  }

  pageTransition() {
    PageTransitionEnter({ duration: 100 }).onEnter(() => {})
    PageTransitionExit({ duration: 100 }).onExit(() => {})
  }

  build() {
    Column() {
      String('not a component').trim()
      Text('content')
    }
  }
}
"""


API_OWNERSHIP_SAMPLE = """import systemRouter from '@system.router'
import { router as navigation, ColorMetrics } from '@kit.ArkUI'
import { i18n as locale } from '@kit.LocalizationKit'
import { ProjectHelper } from './ProjectHelper'

function exercise(items: string[], helper: Helper) {
  systemRouter.back()
  navigation.pushUrl({ url: 'pages/Detail' })
  ColorMetrics.resourceColor('#ffffff')
  locale.System.getSystemLanguage()
  AppStorage.get('token')
  AppStorage.setOrCreate('token', '')
  setTimeout(() => {}, 1)

  ProjectHelper.run()
  items.push('value')
  helper.setTimeout()
  console.info('ignored')
  Math.max(1, 2)
  getController().open()
  PageTransitionEnter({ duration: 100 }).onEnter(() => {})
}
"""


API_SHADOW_CASES = {
    "import parameter": """import { router } from '@kit.ArkUI'
function exercise(router: Router) {
  router.back()
}
""",
    "import method parameter": """import { router } from '@kit.ArkUI'
class Runner {
  exercise(router: Router) {
    router.back()
  }
}
""",
    "import arrow parameter": """import { router } from '@kit.ArkUI'
const exercise = (router: Router) => router.back()
""",
    "import local declarations": """import { router } from '@kit.ArkUI'
function exercise() {
  let router = helper
  router.back()
}
function exerciseConst() {
  const router = helper
  router.pushUrl({})
}
function exerciseVar() {
  var router = helper
  router.replaceUrl({})
}
""",
    "relative AppStorage binding": """import { AppStorage } from './storage'
function exercise() {
  AppStorage.get('token')
}
""",
    "relative setTimeout binding": """import { setTimeout } from './timer'
function exercise() {
  setTimeout(work, 1)
}
""",
    "global parameter shadow": """function exercise(
  AppStorage: ProjectStorage,
  setTimeout: ProjectTimer
) {
  AppStorage.get('token')
  setTimeout(work, 1)
}
""",
    "callback type before import parameter": """import { router } from '@kit.ArkUI'
function exercise(callback: () => void, router: Router) {
  callback()
  router.back()
}
""",
}


API_MIXED_SHADOW_SAMPLE = """import { router } from '@kit.ArkUI'

function valid() {
  router.back()
}

function parameterShadow(router: Router) {
  router.pushUrl({})
}

function localShadow() {
  const router = helper
  router.replaceUrl({})
}
"""

API_FOR_SHADOW_SAMPLE = """import { router } from '@kit.ArkUI'
function exercise(values: Router[]) {
  for (let router of values) {
    router.back()
  }
  router.pushUrl({})
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

    def test_filters_apis_by_receiver_binding_and_preserves_full_chains(self) -> None:
        facts = LexicalParser().parse(API_OWNERSHIP_SAMPLE, "src/pages/ApiOwnership.ets")

        self.assertEqual(
            facts.apis,
            {
                "AppStorage.get",
                "AppStorage.setOrCreate",
                "ColorMetrics.resourceColor",
                "i18n.System.getSystemLanguage",
                "router.back",
                "router.pushUrl",
                "setTimeout",
            },
        )

    def test_respects_local_and_non_sdk_import_api_shadows(self) -> None:
        parser = LexicalParser()
        for name, source in API_SHADOW_CASES.items():
            with self.subTest(name=name):
                self.assertEqual(parser.parse(source, f"{name}.ets").apis, set())

        self.assertEqual(
            parser.parse(API_MIXED_SHADOW_SAMPLE, "MixedShadow.ets").apis,
            {"router.back"},
        )

    def test_extracts_optional_and_generic_api_invocations(self) -> None:
        source = """import { router } from '@kit.ArkUI'
function exercise() {
  router.pushUrl?.({})
  router.replaceUrl<RouteInfo>({})
  setTimeout?.(work, 1)
}
"""

        self.assertEqual(
            LexicalParser().parse(source, "OptionalCalls.ets").apis,
            {"router.pushUrl", "router.replaceUrl", "setTimeout"},
        )

    def test_for_loop_shadow_does_not_escape_the_loop(self) -> None:
        self.assertEqual(
            LexicalParser().parse(API_FOR_SHADOW_SAMPLE, "ForShadow.ets").apis,
            {"router.pushUrl"},
        )


@unittest.skipUnless(
    SIDECAR_NODE_MODULE.exists(),
    "ArkTS tree-sitter sidecar dependencies are not installed",
)
class ArktsTreeSitterParserTest(unittest.TestCase):
    def test_classifies_builder_and_regular_struct_methods(self) -> None:
        facts = ArktsTreeSitterParser().parse(BUILDER_METHOD_SAMPLE, "BuilderKinds.ets")

        self.assertEqual(facts.parser_layer, "L1")
        declarations = {
            item.qualified_name: item.kind
            for item in facts.declarations
            if item.qualified_name
            in {"BuilderKinds.content", "BuilderKinds.helper", "BuilderKinds.build"}
        }
        self.assertEqual(
            declarations,
            {
                "BuilderKinds.content": "builder",
                "BuilderKinds.helper": "method",
                "BuilderKinds.build": "build_method",
            },
        )

    def test_ui_block_span_includes_trailing_modifier_chain(self) -> None:
        facts = ArktsTreeSitterParser().parse(
            UI_BLOCK_MODIFIER_SPAN_SAMPLE,
            "UiBlockSpan.ets",
        )

        self.assertEqual(facts.parser_layer, "L1")
        declaration = next(
            item
            for item in facts.declarations
            if item.qualified_name == "UiBlockSpan.build.Column.Row"
        )
        self.assertEqual(
            (declaration.span.start_line, declaration.span.end_line),
            (6, 10),
        )

    def test_recovers_chained_leaf_components_without_local_call_false_positives(
        self,
    ) -> None:
        facts = ArktsTreeSitterParser().parse(PARSER_TAIL_SAMPLE, "ParserTail.ets")

        self.assertEqual(facts.parser_layer, "L1")
        declarations = {item.qualified_name: item for item in facts.declarations}
        expected_spans = {
            "ParserTail.build.Column.ImageAnimator": (16, 19),
            "ParserTail.build.Column.Row.Button": (21, 23),
            "ParserTail.build.Column.Divider": (26, 27),
        }
        for qualified_name, expected_span in expected_spans.items():
            with self.subTest(qualified_name=qualified_name):
                declaration = declarations[qualified_name]
                self.assertEqual(declaration.kind, "ui_block")
                self.assertEqual(
                    (declaration.span.start_line, declaration.span.end_line),
                    expected_span,
                )

        self.assertFalse(any(item.name == "closeDialog" for item in facts.declarations))
        self.assertEqual(
            facts.components,
            {"Button", "Column", "Divider", "ImageAnimator", "Row"},
        )
        self.assertEqual(
            facts.attributes,
            {
                "color",
                "duration",
                "images",
                "margin",
                "onClick",
                "padding",
                "state",
                "width",
            },
        )
        self.assertEqual(
            facts.symbols,
            {
                value
                for declaration in facts.declarations
                for value in (declaration.name, declaration.qualified_name)
            },
        )

    def test_declaration_span_only_includes_attached_decorators(self) -> None:
        facts = ArktsTreeSitterParser().parse(PARSER_TAIL_SAMPLE, "ParserTail.ets")
        declarations = {item.qualified_name: item for item in facts.declarations}

        self.assertEqual(facts.parser_layer, "L1")
        self.assertEqual(declarations["ParserTail.aboutToAppear"].span.start_line, 5)
        self.assertEqual(declarations["ParserTail.paddingStyle"].span.start_line, 9)

    def test_recovers_special_ui_contexts_without_uppercase_business_calls(self) -> None:
        facts = ArktsTreeSitterParser().parse(
            SPECIAL_UI_CONTEXT_SAMPLE,
            "SpecialContexts.ets",
        )

        self.assertEqual(facts.parser_layer, "L1")
        declarations = {item.qualified_name: item for item in facts.declarations}
        self.assertIn("SpecialContexts.DialogContent", declarations)
        self.assertIn(
            "SpecialContexts.pageTransition.PageTransitionEnter",
            declarations,
        )
        self.assertIn(
            "SpecialContexts.pageTransition.PageTransitionExit",
            declarations,
        )
        self.assertFalse(
            any(
                item.kind == "ui_block"
                and item.name in {"Factory", "String", "TestRunner"}
                for item in facts.declarations)
        )
        self.assertEqual(
            facts.components,
            {
                "Column",
                "DialogContent",
                "PageTransitionEnter",
                "PageTransitionExit",
                "Text",
            },
        )
        self.assertEqual(facts.attributes, {"onEnter", "onExit"})

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

    def test_filters_l1_calls_and_uses_filtered_lexical_recovery(self) -> None:
        source = """import { router } from '@kit.ArkUI'
import { accessibility } from '@kit.AccessibilityKit'
import { ProjectHelper } from './ProjectHelper'

function exercise(info: accessibility.EventInfo) {
  router.pushUrl({ url: 'pages/Detail' })
  accessibility.sendAccessibilityEvent(info).then(() => {})
  ProjectHelper.run()
  console.info('ignored')
  Math.max(1, 2)
}
"""

        facts = ArktsTreeSitterParser().parse(source, "src/pages/ApiRecovery.ets")

        self.assertEqual(
            facts.apis,
            {"accessibility.sendAccessibilityEvent", "router.pushUrl"},
        )

    def test_l1_does_not_reintroduce_scope_shadowed_calls(self) -> None:
        parser = ArktsTreeSitterParser()
        for name, source in API_SHADOW_CASES.items():
            with self.subTest(name=name):
                facts = parser.parse(source, f"{name}.ets")
                self.assertEqual(facts.parser_layer, "L1")
                self.assertEqual(facts.apis, set())

        facts = parser.parse(API_MIXED_SHADOW_SAMPLE, "MixedShadow.ets")
        self.assertEqual(facts.parser_layer, "L1")
        self.assertEqual(facts.apis, {"router.back"})

        facts = parser.parse(API_FOR_SHADOW_SAMPLE, "ForShadow.ets")
        self.assertEqual(facts.parser_layer, "L1")
        self.assertEqual(facts.apis, {"router.pushUrl"})


class CodeAnalyzerTest(unittest.TestCase):
    def test_exposes_file_results_for_diff_without_hunks(self) -> None:
        result = CodeAnalyzer(parser=LexicalParser()).analyze_file(
            path="src/pages/EmptyDiff.ets",
            content="const unchanged = true\n",
            mode="diff",
            hunks=[],
        )

        self.assertEqual(result.review_units, [])
        self.assertEqual(result.retrieval_query.units, [])
        self.assertIsNotNone(result.review_unit_build_result)
        build_result = result.review_unit_build_result
        assert build_result is not None
        self.assertEqual(build_result.schema_version, "review-unit-build-v1")
        self.assertEqual(build_result.mode, "diff")
        self.assertEqual(build_result.flatten_units(), result.review_units)
        self.assertEqual(len(build_result.file_results), 1)
        file_result = build_result.file_results[0]
        self.assertEqual(file_result.path, "src/pages/EmptyDiff.ets")
        self.assertEqual(file_result.units, [])
        self.assertEqual(file_result.parser_quality.parser_layer, "L0")
        self.assertEqual(file_result.unassigned_hunk_lines, [])
        self.assertEqual(
            [diagnostic.code for diagnostic in file_result.diagnostics],
            ["diff_file_without_hunks"],
        )

    def test_preserves_full_file_and_secondary_parser_quality(self) -> None:
        class SequencedParser:
            def __init__(self) -> None:
                self.calls = 0

            def parse(self, source: str, path: str) -> CodeFacts:
                self.calls += 1
                facts = LexicalParser().parse(source, path)
                if self.calls == 1:
                    facts.parser_layer = "L1"
                    facts.warnings = ["arkts_tree_sitter_error_nodes: 1"]
                else:
                    facts.parser_layer = "parse_degraded"
                    facts.warnings = ["arkts_tree_sitter_failed: unit fixture"]
                return facts

        parser = SequencedParser()
        result = CodeAnalyzer(parser=parser).analyze_file(
            path="src/pages/PhotoWall.ets",
            content=SAMPLE,
            mode="diff",
            hunks=[(15, 1)],
        )

        self.assertEqual(parser.calls, 2)
        unit = result.review_units[0]
        self.assertTrue(unit.context_degraded)
        self.assertEqual(
            [diagnostic.code for diagnostic in unit.diagnostics],
            ["parser_degraded", "parser_error_nodes"],
        )
        unit.context_degraded = False
        with self.assertRaisesRegex(
            ValueError,
            "parser quality diagnostics require context_degraded",
        ):
            unit.validate()
        unit.context_degraded = True
        self.assertEqual(result.metadata.parser_layer, "parse_degraded")
        self.assertTrue(
            any("unit fixture" in warning for warning in result.metadata.warnings)
        )
        build_result = result.review_unit_build_result
        assert build_result is not None
        parser_quality = build_result.file_results[0].parser_quality
        self.assertEqual(parser_quality.parser_layer, "L1")
        self.assertEqual(
            parser_quality.warnings,
            ["arkts_tree_sitter_error_nodes: 1"],
        )

    def test_rejects_invalid_secondary_parser_layer(self) -> None:
        class InvalidSecondaryLayerParser:
            def __init__(self) -> None:
                self.calls = 0

            def parse(self, source: str, path: str) -> CodeFacts:
                self.calls += 1
                facts = LexicalParser().parse(source, path)
                if self.calls > 1:
                    facts.parser_layer = "BROKEN"  # type: ignore[assignment]
                return facts

        with self.assertRaisesRegex(ValueError, "unsupported parser layers"):
            CodeAnalyzer(parser=InvalidSecondaryLayerParser()).analyze_file(
                path="src/pages/PhotoWall.ets",
                content=SAMPLE,
                mode="diff",
                hunks=[(15, 1)],
            )

    def test_rejects_builder_parser_quality_that_drifts_from_file_facts(self) -> None:
        class DriftedQualityBuilder(ReviewUnitBuilder):
            def build_file_result(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                result = super().build_file_result(*args, **kwargs)
                result.parser_quality = ParserQuality(
                    parser_layer="parse_degraded",
                    warnings=["builder_quality_lost"],
                )
                return result

        with self.assertRaisesRegex(ValueError, "must match the full-file CodeFacts"):
            CodeAnalyzer(
                parser=LexicalParser(),
                unit_builder=DriftedQualityBuilder(),
            ).analyze_file(
                path="src/pages/PhotoWall.ets",
                content=SAMPLE,
                mode="diff",
                hunks=[(15, 1)],
            )

    def test_analysis_result_rejects_legacy_and_envelope_drift(self) -> None:
        analyzer = CodeAnalyzer(parser=LexicalParser())
        result = analyzer.analyze_file(
            path="src/pages/PhotoWall.ets",
            content=SAMPLE,
            mode="full",
        )

        build_result = result.review_unit_build_result
        assert build_result is not None
        file_result = build_result.file_results[0]
        file_result.parser_quality = ParserQuality(
            parser_layer="parse_degraded",
            warnings=[],
        )
        with self.assertRaisesRegex(ValueError, "must propagate to every Unit"):
            file_result.validate()
        file_result.parser_quality = ParserQuality(parser_layer="L0", warnings=[])
        file_result.unassigned_hunk_lines = [1]
        with self.assertRaisesRegex(ValueError, "full ReviewUnitBuildResult"):
            build_result.validate()
        file_result.unassigned_hunk_lines = []

        result.review_units = []

        with self.assertRaisesRegex(ValueError, "must match ReviewUnitBuildResult"):
            result.to_dict()
        with self.assertRaisesRegex(ValueError, "must match ReviewUnitBuildResult"):
            analyzer.to_json_ready(result)

    def test_sorts_file_results_independently_of_input_order(self) -> None:
        files = [
            FileInput(path="src/Z.ets", content="const z = 1\n"),
            FileInput(path="src/A.ets", content="const a = 1\n"),
        ]

        result = CodeAnalyzer(parser=LexicalParser()).analyze_files(files, mode="full")

        build_result = result.review_unit_build_result
        assert build_result is not None
        self.assertEqual(
            [file_result.path for file_result in build_result.file_results],
            ["src/A.ets", "src/Z.ets"],
        )
        self.assertEqual(build_result.flatten_units(), result.review_units)

    def test_host_summary_does_not_mix_multiple_structs(self) -> None:
        source = """@Component
struct First {
  @State first: number = 0
  aboutToAppear() {}
  build() {}
}

@Entry
@ComponentV2
struct Second {
  @State second: number = 0
  aboutToDisappear() {}
  build() {}
}
"""
        source_lines = source.splitlines()
        declarations = [
            Declaration(
                kind="struct",
                name="First",
                qualified_name="First",
                span=SourceSpan(start_line=1, end_line=6),
                text="\n".join(source_lines[0:6]),
            ),
            Declaration(
                kind="method",
                name="aboutToAppear",
                qualified_name="First.aboutToAppear",
                parent_name="First",
                span=SourceSpan(start_line=4, end_line=4),
                text=source_lines[3],
            ),
            Declaration(
                kind="build_method",
                name="build",
                qualified_name="First.build",
                parent_name="First",
                span=SourceSpan(start_line=5, end_line=5),
                text=source_lines[4],
            ),
            Declaration(
                kind="struct",
                name="Second",
                qualified_name="Second",
                span=SourceSpan(start_line=8, end_line=14),
                text="\n".join(source_lines[7:14]),
            ),
            Declaration(
                kind="method",
                name="aboutToDisappear",
                qualified_name="Second.aboutToDisappear",
                parent_name="Second",
                span=SourceSpan(start_line=12, end_line=12),
                text=source_lines[11],
            ),
            Declaration(
                kind="build_method",
                name="build",
                qualified_name="Second.build",
                parent_name="Second",
                span=SourceSpan(start_line=13, end_line=13),
                text=source_lines[12],
            ),
        ]
        facts = CodeFacts(
            path="src/pages/Hosts.ets",
            declarations=declarations,
            decorators={"@Component", "@ComponentV2", "@Entry"},
            symbols={"aboutToAppear", "aboutToDisappear"},
        )

        units = ReviewUnitBuilder().build_full_units(
            "src/pages/Hosts.ets",
            source,
            facts,
        )

        summaries = {unit.unit_symbol: unit.host_summary for unit in units}
        self.assertEqual(summaries["First"].decorators, ["@Component"])
        self.assertEqual(summaries["First"].lifecycle, ["aboutToAppear"])
        self.assertEqual(summaries["First"].states, ["@State first: number = 0"])
        self.assertEqual(
            summaries["Second"].decorators,
            ["@ComponentV2", "@Entry"],
        )
        self.assertEqual(
            summaries["Second"].lifecycle,
            ["aboutToDisappear"],
        )
        self.assertEqual(
            summaries["Second"].states,
            ["@State second: number = 0"],
        )

    def test_long_build_assigns_each_changed_line_to_its_innermost_owner(self) -> None:
        source = """struct Page {
  build() {
    // build-only before
    Column() {
      Text('target')
    }
    // build-only after
  }
}
"""
        facts = CodeFacts(
            path="src/pages/Page.ets",
            declarations=[
                Declaration(
                    kind="struct",
                    name="Page",
                    qualified_name="Page",
                    span=SourceSpan(start_line=1, end_line=9),
                    text=source.rstrip(),
                ),
                Declaration(
                    kind="build_method",
                    name="build",
                    qualified_name="Page.build",
                    parent_name="Page",
                    span=SourceSpan(start_line=2, end_line=8),
                    text="\n".join(source.splitlines()[1:8]),
                ),
                Declaration(
                    kind="ui_block",
                    name="Column",
                    qualified_name="Page.build.Column",
                    parent_name="Page.build",
                    span=SourceSpan(start_line=4, end_line=6),
                    text="\n".join(source.splitlines()[3:6]),
                ),
            ],
        )

        result = ReviewUnitBuilder(max_build_lines=5).build_file_result(
            "src/pages/Page.ets",
            source,
            facts,
            "diff",
            [FileHunk(new_start=3, new_lines=5)],
        )

        self.assertEqual(
            [unit.unit_symbol for unit in result.units],
            ["Page.build", "Page.build.Column"],
        )
        self.assertEqual(result.units[0].changed_new_lines, [3, 7])
        self.assertEqual(result.units[1].changed_new_lines, [4, 5, 6])
        self.assertEqual(result.unassigned_hunk_lines, [])

    def test_full_mode_emits_only_top_level_structs_and_classes(self) -> None:
        source = """class Root {
}

function outer() {
  class Local {
    value: number = 1
  }
}
"""
        facts = CodeFacts(
            path="src/Outer.ets",
            declarations=[
                Declaration(
                    kind="class",
                    name="Root",
                    qualified_name="Root",
                    span=SourceSpan(start_line=1, end_line=2),
                    text="\n".join(source.splitlines()[0:2]),
                ),
                Declaration(
                    kind="function",
                    name="outer",
                    qualified_name="outer",
                    span=SourceSpan(start_line=4, end_line=8),
                    text="\n".join(source.splitlines()[3:8]),
                ),
                Declaration(
                    kind="class",
                    name="Local",
                    qualified_name="Local",
                    parent_name=None,
                    span=SourceSpan(start_line=5, end_line=7),
                    text="\n".join(source.splitlines()[4:7]),
                ),
            ],
        )

        units = ReviewUnitBuilder().build_full_units(
            "src/Outer.ets",
            source,
            facts,
        )

        self.assertEqual([unit.unit_symbol for unit in units], ["Root"])

        diff_result = ReviewUnitBuilder().build_file_result(
            "src/Outer.ets",
            source,
            facts,
            "diff",
            [FileHunk(new_start=6, new_lines=1)],
        )
        self.assertEqual(
            [unit.unit_symbol for unit in diff_result.units],
            ["Local"],
        )
        self.assertEqual(diff_result.units[0].changed_new_lines, [6])

    def test_rejects_normalized_path_aliases_before_parsing(self) -> None:
        class CountingParser:
            def __init__(self) -> None:
                self.calls = 0

            def parse(self, source: str, path: str) -> CodeFacts:
                self.calls += 1
                return LexicalParser().parse(source, path)

        parser = CountingParser()
        analyzer = CodeAnalyzer(parser=parser)
        files = [
            FileInput(path="src/A.ets", content="const value = 1\n"),
            FileInput(path=r"src\.\A.ets", content="const value = 2\n"),
        ]

        with self.assertRaisesRegex(ValueError, "duplicate normalized ReviewUnit path"):
            analyzer.analyze_files(files)
        self.assertEqual(parser.calls, 0)

        with self.assertRaisesRegex(ValueError, "repository-relative"):
            analyzer.analyze_file("/tmp/A.ets", "const value = 1\n")
        self.assertEqual(parser.calls, 0)

    def test_rejects_builder_output_that_does_not_match_file_source(self) -> None:
        class DriftedTextBuilder(ReviewUnitBuilder):
            def build_units(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                units = super().build_units(*args, **kwargs)
                units[0].full_text += "\n// unrelated"
                return units

        with self.assertRaisesRegex(ValueError, "context_span source slice"):
            CodeAnalyzer(
                parser=LexicalParser(),
                unit_builder=DriftedTextBuilder(),
            ).analyze_file(
                path="src/pages/PhotoWall.ets",
                content=SAMPLE,
                mode="diff",
                hunks=[(15, 1)],
            )

        class DriftedChangedLineBuilder(ReviewUnitBuilder):
            def build_units(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                units = super().build_units(*args, **kwargs)
                units[0].changed_lines = [1]
                units[0].file_changed_lines = [1]
                units[0].changed_new_lines = [1]
                units[0].unit_changed_lines = [1]
                return units

        with self.assertRaisesRegex(ValueError, "must come from its FileInput hunks"):
            CodeAnalyzer(
                parser=LexicalParser(),
                unit_builder=DriftedChangedLineBuilder(),
            ).analyze_file(
                path="src/plain.ets",
                content="const first = 1\nconst second = 2\nconst third = 3\n",
                mode="diff",
                hunks=[(2, 1)],
            )

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
        self.assertEqual(
            unit.unit_id,
            "src/pages/PhotoWall.ets@method:PhotoWall.loadImages:L14-L20",
        )
        self.assertEqual(unit.unit_kind, "method")
        self.assertEqual((unit.source_span.start_line, unit.source_span.end_line), (14, 20))
        self.assertEqual((unit.context_span.start_line, unit.context_span.end_line), (14, 20))
        self.assertEqual(unit.changed_new_lines, [14, 15, 16])
        self.assertEqual(unit.selection_reason, "innermost_changed_declaration")
        self.assertEqual(unit.host_summary.struct, "PhotoWall")
        self.assertEqual(unit.file_changed_lines, [14, 15, 16])
        build_result = result.review_unit_build_result
        assert build_result is not None
        self.assertEqual(build_result.file_results[0].unassigned_hunk_lines, [13])

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
        self.assertEqual(
            unit.unit_id,
            "src/pages/plain.ets@fallback:fallback:L2-L2:C1-L2",
        )
        self.assertEqual(unit.unit_kind, "fallback")
        self.assertEqual((unit.source_span.start_line, unit.source_span.end_line), (2, 2))
        self.assertEqual((unit.context_span.start_line, unit.context_span.end_line), (1, 2))
        self.assertEqual(unit.changed_new_lines, [2])
        self.assertEqual(unit.selection_reason, "fallback_window")
        self.assertEqual([item.code for item in unit.diagnostics], ["no_matching_declaration"])

    def test_unit_id_normalizes_path_without_changing_legacy_unit_ref(self) -> None:
        result = CodeAnalyzer(parser=LexicalParser()).analyze_file(
            path=r"src\pages\PhotoWall.ets",
            content=SAMPLE,
            mode="diff",
            hunks=[(15, 1)],
        )

        unit = result.review_units[0]
        self.assertEqual(
            unit.unit_id,
            "src/pages/PhotoWall.ets@method:PhotoWall.loadImages:L14-L20",
        )
        self.assertEqual(unit.unit_ref, r"PhotoWall.loadImages@src\pages\PhotoWall.ets")

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
