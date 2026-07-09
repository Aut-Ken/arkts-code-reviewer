from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "arkui_ace_engine_samples.json"
DEFAULT_ENGINE_ROOT = REPO_ROOT.parent / "arkui_ace_engine"
DEFAULT_RUNS_ROOT = REPO_ROOT / "reports" / "parser_validation" / "runs"
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"


@dataclass(frozen=True)
class GroupConfig:
    group_id: str
    title: str
    min_lines: int
    max_lines: int | None
    batch_style: str
    default_limit: int
    max_source_lines: int
    goal: str

    @property
    def line_range(self) -> str:
        if self.max_lines is None:
            return f"{self.min_lines}+"
        return f"{self.min_lines}-{self.max_lines}"


@dataclass(frozen=True)
class PlannedSample:
    category: str
    path: str
    line_count: int | None
    missing: bool = False

    @property
    def sample_id(self) -> str:
        return f"{self.category}/{Path(self.path).name}"

    def to_json(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "category": self.category,
            "path": self.path,
            "line_count": self.line_count,
            "missing": self.missing,
        }


GROUPS = (
    GroupConfig(
        group_id="T01-small-batch",
        title="Small files batch validation",
        min_lines=0,
        max_lines=150,
        batch_style="batch",
        default_limit=5,
        max_source_lines=200,
        goal="Verify basic parser field policy on short, easy-to-audit files.",
    ),
    GroupConfig(
        group_id="T02-medium-batch",
        title="Medium files small-batch validation",
        min_lines=151,
        max_lines=500,
        batch_style="small-batch",
        default_limit=2,
        max_source_lines=500,
        goal="Check whether parser quality holds on complete page-level files.",
    ),
    GroupConfig(
        group_id="T03-long-single",
        title="Long files single-file validation",
        min_lines=501,
        max_lines=1500,
        batch_style="single-or-pair",
        default_limit=1,
        max_source_lines=1000,
        goal="Audit deeper ArkUI chains, declarations, and APIs without overloading GLM.",
    ),
    GroupConfig(
        group_id="T04-large-single",
        title="Large files single-file validation",
        min_lines=1501,
        max_lines=3000,
        batch_style="single",
        default_limit=1,
        max_source_lines=800,
        goal="Use focused single-file tests for high-complexity examples.",
    ),
    GroupConfig(
        group_id="T05-huge-segmented",
        title="Huge files segmented validation",
        min_lines=3001,
        max_lines=None,
        batch_style="segmented",
        default_limit=1,
        max_source_lines=600,
        goal="Keep huge files out of whole-file GLM checks; validate by slice later.",
    ),
)
GROUPS_BY_ID = {group.group_id: group for group in GROUPS}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create parser validation plans and per-run record folders."
    )
    parser.add_argument("--engine-root", type=Path, default=DEFAULT_ENGINE_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--init", action="store_true", help="Write group plans and sample lists.")
    parser.add_argument("--create-run", choices=GROUPS_BY_ID)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-id", action="append", default=[])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--parser", default="arkts-tree-sitter")
    parser.add_argument("--model", default="glm-5.1")
    parser.add_argument("--thinking", default="enabled")
    parser.add_argument("--response-format", default="omit")
    parser.add_argument("--max-tokens", type=int, default=20_000)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--retry-attempts", type=int, default=4)
    parser.add_argument("--retry-base-delay-seconds", type=float, default=20.0)
    parser.add_argument("--request-delay-seconds", type=float, default=5.0)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    samples = load_samples(args.manifest, args.engine_root)
    grouped = group_samples(samples)
    result: dict[str, Any] = {
        "manifest": str(args.manifest),
        "engine_root": str(args.engine_root),
        "runs_root": str(args.runs_root),
        "groups": {group_id: len(items) for group_id, items in grouped.items()},
    }

    if args.init or not args.create_run:
        write_group_plans(args.runs_root, args.manifest, args.engine_root, grouped)
        result["initialized"] = True

    if args.create_run:
        run_dir = create_run_folder(args, grouped[args.create_run])
        result["run_dir"] = str(run_dir)

    print(json.dumps(result, ensure_ascii=False, indent=2))


def load_samples(manifest_path: Path, engine_root: Path) -> list[PlannedSample]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples: list[PlannedSample] = []
    for item in data["samples"]:
        rel_path = item["path"].replace("\\", "/")
        source_path = engine_root / Path(rel_path)
        if source_path.exists():
            line_count = len(source_path.read_text(encoding="utf-8").splitlines())
            samples.append(
                PlannedSample(
                    category=item["category"],
                    path=rel_path,
                    line_count=line_count,
                )
            )
        else:
            samples.append(
                PlannedSample(
                    category=item["category"],
                    path=rel_path,
                    line_count=None,
                    missing=True,
                )
            )
    return samples


def group_samples(samples: list[PlannedSample]) -> dict[str, list[PlannedSample]]:
    grouped = {group.group_id: [] for group in GROUPS}
    for sample in samples:
        group = group_for_line_count(sample.line_count)
        if group is not None:
            grouped[group.group_id].append(sample)
    return grouped


def group_for_line_count(line_count: int | None) -> GroupConfig | None:
    if line_count is None:
        return None
    for group in GROUPS:
        if line_count < group.min_lines:
            continue
        if group.max_lines is not None and line_count > group.max_lines:
            continue
        return group
    return None


def write_group_plans(
    runs_root: Path,
    manifest_path: Path,
    engine_root: Path,
    grouped: dict[str, list[PlannedSample]],
) -> None:
    runs_root.mkdir(parents=True, exist_ok=True)
    index = {
        "created_at": now_iso(),
        "manifest": str(manifest_path),
        "engine_root": str(engine_root),
        "groups": [],
    }
    for group in GROUPS:
        group_dir = runs_root / group.group_id
        group_dir.mkdir(parents=True, exist_ok=True)
        (group_dir / "runs").mkdir(parents=True, exist_ok=True)
        samples = grouped[group.group_id]
        write_json(group_dir / "samples.json", sample_manifest(group, samples))
        (group_dir / "group-plan.md").write_text(
            render_group_plan(group, samples, manifest_path, engine_root),
            encoding="utf-8",
        )
        index["groups"].append(
            {
                "group_id": group.group_id,
                "title": group.title,
                "line_range": group.line_range,
                "sample_count": len(samples),
                "samples": str(group_dir / "samples.json"),
                "plan": str(group_dir / "group-plan.md"),
            }
        )
    write_json(runs_root / "index.json", index)


def sample_manifest(group: GroupConfig, samples: list[PlannedSample]) -> dict[str, Any]:
    return {
        "engine": "arkui_ace_engine",
        "description": f"Parser validation samples for {group.group_id}.",
        "group_id": group.group_id,
        "line_range": group.line_range,
        "batch_style": group.batch_style,
        "default_limit": group.default_limit,
        "max_source_lines": group.max_source_lines,
        "samples": [sample.to_json() for sample in samples],
    }


def render_group_plan(
    group: GroupConfig,
    samples: list[PlannedSample],
    manifest_path: Path,
    engine_root: Path,
) -> str:
    lines = [
        f"# {group.group_id}: {group.title}",
        "",
        "## Scope",
        "",
        f"- Source manifest: `{manifest_path}`",
        f"- Engine root: `{engine_root}`",
        f"- Line range: `{group.line_range}`",
        f"- Batch style: `{group.batch_style}`",
        f"- Default limit per run: `{group.default_limit}`",
        f"- Suggested max source lines: `{group.max_source_lines}`",
        f"- Current sample count: `{len(samples)}`",
        "",
        "## Goal",
        "",
        group.goal,
        "",
        "## Run Folder Rule",
        "",
        "Each real test run should be stored under `runs/run-NNN-YYYYMMDD-HHMMSS/`.",
        "The run folder should contain `run-meta.json`, `selected-samples.json`,",
        "`deterministic-batch.json`, `glm-findings.jsonl`, `glm-findings.pretty.json`,",
        "`raw_glm/`, `summary.md`, and `adjudication.md`.",
        "",
        "## Samples",
        "",
        "| Lines | Sample ID | Category | Path |",
        "| ---: | --- | --- | --- |",
    ]
    for sample in samples:
        lines.append(
            f"| {sample.line_count} | `{sample.sample_id}` | "
            f"`{sample.category}` | `{sample.path}` |"
        )
    lines.append("")
    return "\n".join(lines)


def create_run_folder(args: argparse.Namespace, group_samples: list[PlannedSample]) -> Path:
    group = GROUPS_BY_ID[args.create_run]
    group_dir = args.runs_root / group.group_id
    runs_dir = group_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    selected = select_run_samples(
        group_samples=group_samples,
        runs_dir=runs_dir,
        sample_ids=args.sample_id,
        offset=args.offset,
        limit=args.limit or group.default_limit,
    )
    run_dir = runs_dir / next_run_name(runs_dir)
    raw_dir = run_dir / "raw_glm"
    raw_dir.mkdir(parents=True, exist_ok=False)

    selected_manifest = {
        "engine": "arkui_ace_engine",
        "description": f"Selected samples for {run_dir.name}.",
        "group_id": group.group_id,
        "run_id": run_dir.name,
        "samples": [sample.to_json() for sample in selected],
    }
    write_json(run_dir / "selected-samples.json", selected_manifest)
    write_json(run_dir / "run-meta.json", run_meta(args, group, run_dir, selected))
    (run_dir / "command.ps1").write_text(
        render_command(args, group, run_dir),
        encoding="utf-8",
    )
    (run_dir / "summary.md").write_text(render_summary_template(group, run_dir), encoding="utf-8")
    (run_dir / "adjudication.md").write_text(
        render_adjudication_template(group, selected),
        encoding="utf-8",
    )
    return run_dir


def select_run_samples(
    *,
    group_samples: list[PlannedSample],
    runs_dir: Path,
    sample_ids: list[str],
    offset: int,
    limit: int,
) -> list[PlannedSample]:
    if sample_ids:
        selected = [
            sample
            for sample in group_samples
            if sample.sample_id in sample_ids or sample.path in sample_ids
        ]
        missing_ids = sorted(set(sample_ids) - {sample.sample_id for sample in selected})
        if missing_ids:
            raise SystemExit(f"Unknown sample ids for this group: {', '.join(missing_ids)}")
        return selected

    seen = read_seen_sample_ids(runs_dir)
    unseen = [sample for sample in group_samples if sample.sample_id not in seen]
    pool = unseen or group_samples
    return pool[offset : offset + limit]


def read_seen_sample_ids(runs_dir: Path) -> set[str]:
    seen: set[str] = set()
    for manifest_path in runs_dir.glob("run-*/selected-samples.json"):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for sample in data.get("samples", []):
            sample_id = sample.get("sample_id")
            if isinstance(sample_id, str):
                seen.add(sample_id)
    return seen


def next_run_name(runs_dir: Path) -> str:
    next_number = 1
    pattern = re.compile(r"^run-(\d+)-")
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        match = pattern.match(child.name)
        if match:
            next_number = max(next_number, int(match.group(1)) + 1)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"run-{next_number:03d}-{timestamp}"


def run_meta(
    args: argparse.Namespace,
    group: GroupConfig,
    run_dir: Path,
    selected: list[PlannedSample],
) -> dict[str, Any]:
    return {
        "run_id": run_dir.name,
        "created_at": now_iso(),
        "group_id": group.group_id,
        "group_title": group.title,
        "parser": args.parser,
        "model": args.model,
        "thinking": args.thinking,
        "response_format": args.response_format,
        "max_tokens": args.max_tokens,
        "max_source_lines": group.max_source_lines,
        "timeout_seconds": args.timeout_seconds,
        "retry_attempts": args.retry_attempts,
        "retry_base_delay_seconds": args.retry_base_delay_seconds,
        "request_delay_seconds": args.request_delay_seconds,
        "base_url": args.base_url,
        "manifest": str(args.manifest),
        "engine_root": str(args.engine_root),
        "selected_count": len(selected),
        "selected_samples": [sample.to_json() for sample in selected],
        "outputs": {
            "deterministic_batch": str(run_dir / "deterministic-batch.json"),
            "glm_findings_jsonl": str(run_dir / "glm-findings.jsonl"),
            "glm_findings_pretty": str(run_dir / "glm-findings.pretty.json"),
            "raw_glm": str(run_dir / "raw_glm"),
        },
    }


def render_command(args: argparse.Namespace, group: GroupConfig, run_dir: Path) -> str:
    selected_manifest = run_dir / "selected-samples.json"
    deterministic_output = run_dir / "deterministic-batch.json"
    findings_output = run_dir / "glm-findings.jsonl"
    pretty_output = run_dir / "glm-findings.pretty.json"
    raw_dir = run_dir / "raw_glm"
    return "\n".join(
        [
            "$ErrorActionPreference = \"Stop\"",
            f"$repoRoot = Resolve-Path {_ps_quote(str(REPO_ROOT))}",
            "Push-Location $repoRoot",
            "try {",
            "    if (-not $env:GLM_API_KEY) {",
            "        $secureKey = Read-Host \"Enter GLM API key for this run\" -AsSecureString",
            "        $keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)",
            "        try {",
            "            $env:GLM_API_KEY = "
            "[Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)",
            "        }",
            "        finally {",
            "            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)",
            "        }",
            "    }",
            f"    $env:GLM_MODEL = {_ps_quote(args.model)}",
            f"    $env:GLM_BASE_URL = {_ps_quote(args.base_url)}",
            f"    $env:GLM_THINKING_TYPE = {_ps_quote(args.thinking)}",
            f"    $env:GLM_RESPONSE_FORMAT = {_ps_quote(args.response_format)}",
            f"    $env:GLM_RAW_RESPONSE_DIR = {_ps_quote(str(raw_dir))}",
            f"    $env:GLM_MAX_TOKENS = {_ps_quote(str(args.max_tokens))}",
            f"    $env:GLM_RETRY_ATTEMPTS = {_ps_quote(str(args.retry_attempts))}",
            f"    $env:GLM_RETRY_BASE_DELAY_SECONDS = "
            f"{_ps_quote(str(args.retry_base_delay_seconds))}",
            "",
            "    python tools\\run_arkts_parser_batch.py `",
            f"        --parser {_ps_quote(args.parser)} `",
            f"        --manifest {_ps_quote(str(selected_manifest))} `",
            f"        --engine-root {_ps_quote(str(args.engine_root))} `",
            f"        --json-output {_ps_quote(str(deterministic_output))}",
            "",
            "    python tools\\validate_parser_with_llm.py `",
            f"        --parser {_ps_quote(args.parser)} `",
            f"        --manifest {_ps_quote(str(selected_manifest))} `",
            f"        --engine-root {_ps_quote(str(args.engine_root))} `",
            f"        --max-source-lines {_ps_quote(str(group.max_source_lines))} `",
            f"        --timeout-seconds {_ps_quote(str(args.timeout_seconds))} `",
            f"        --max-tokens {_ps_quote(str(args.max_tokens))} `",
            f"        --retry-attempts {_ps_quote(str(args.retry_attempts))} `",
            f"        --retry-base-delay-seconds "
            f"{_ps_quote(str(args.retry_base_delay_seconds))} `",
            f"        --request-delay-seconds {_ps_quote(str(args.request_delay_seconds))} `",
            f"        --output {_ps_quote(str(findings_output))} `",
            f"        --pretty-output {_ps_quote(str(pretty_output))} `",
            "        --resume",
            "}",
            "finally {",
            "    Pop-Location",
            "}",
            "",
        ]
    )


def render_summary_template(group: GroupConfig, run_dir: Path) -> str:
    return "\n".join(
        [
            f"# {run_dir.name} Summary",
            "",
            "## Basic",
            "",
            f"- Group: `{group.group_id}`",
            "- Status: `pending`",
            "- Operator:",
            "- Started at:",
            "- Finished at:",
            "",
            "## Deterministic Result",
            "",
            "- Parsed:",
            "- Missing:",
            "- Crashed:",
            "- Empty features:",
            "- Warning counts:",
            "",
            "## GLM Result",
            "",
            "- Verdict summary:",
            "- Total findings:",
            "- High confidence findings:",
            "",
            "## Accepted Findings",
            "",
            "-",
            "",
            "## Rejected / Prompt Policy Findings",
            "",
            "-",
            "",
            "## Next Action",
            "",
            "-",
            "",
        ]
    )


def render_adjudication_template(group: GroupConfig, selected: list[PlannedSample]) -> str:
    sample_lines = [
        f"- `{sample.sample_id}` ({sample.line_count} lines): `{sample.path}`"
        for sample in selected
    ]
    return "\n".join(
        [
            f"# {group.group_id} Adjudication",
            "",
            "## Selected Samples",
            "",
            *sample_lines,
            "",
            "## Finding 1",
            "",
            "- Source sample:",
            "- GLM verdict:",
            "- GLM finding:",
            "- Evidence lines:",
            "- Human decision: `accepted / rejected / unclear`",
            "- Reason:",
            "- Follow-up test:",
            "",
            "## Notes",
            "",
            "-",
            "",
        ]
    )


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    main()
