#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_ASSETS = {
    "arkts_code_reviewer/hybrid_analysis/defaults/ai_tag_contracts.yaml",
    "arkts_code_reviewer/hybrid_analysis/defaults/deepseek-tag-analysis-v1.md",
}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hybrid-analysis-wheel-") as raw_root:
        root = Path(raw_root)
        dist = root / "dist"
        unpacked = root / "unpacked"
        dist.mkdir()
        unpacked.mkdir()
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--out-dir",
                str(dist),
                str(REPO_ROOT),
            ],
            check=True,
            cwd=root,
        )
        wheels = tuple(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one wheel, found {len(wheels)}")
        with zipfile.ZipFile(wheels[0]) as archive:
            names = set(archive.namelist())
            missing = PACKAGED_ASSETS - names
            if missing:
                raise RuntimeError(
                    f"Hybrid Analysis wheel is missing assets: {sorted(missing)}"
                )
            archive.extractall(unpacked)

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(unpacked)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "import arkts_code_reviewer; "
                    "from arkts_code_reviewer.hybrid_analysis import "
                    "DEFAULT_AI_TAG_CONTRACTS_PATH, DEFAULT_AI_TAG_PROMPT_PATH, "
                    "FullTaxonomyRequestBuilder; "
                    "package = Path(arkts_code_reviewer.__file__).resolve(); "
                    "assert package.is_relative_to(Path.cwd() / 'unpacked'); "
                    "builder = FullTaxonomyRequestBuilder.default(); "
                    "assert DEFAULT_AI_TAG_CONTRACTS_PATH.is_file(); "
                    "assert DEFAULT_AI_TAG_PROMPT_PATH.is_file(); "
                    "assert len(builder.catalog.contracts) == 24; "
                    "assert builder.catalog.qualification == "
                    "'development_not_qualified'; "
                    "assert builder.model_policy.dispatch_mode == "
                    "'disabled_no_budget_no_approval'; "
                    "print(builder.catalog.catalog_fingerprint); "
                    "print(builder.prompt.prompt_hash)"
                ),
            ],
            check=True,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
        )
        print("Hybrid Analysis wheel smoke: passed")
        print(f"  wheel: {wheels[0].name}")
        for line in completed.stdout.splitlines():
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
