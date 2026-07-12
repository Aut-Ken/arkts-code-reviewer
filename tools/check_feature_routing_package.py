#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_CONFIGS = {
    "arkts_code_reviewer/feature_routing/defaults/dimensions.yaml",
    "arkts_code_reviewer/feature_routing/defaults/tags.yaml",
}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="feature-routing-wheel-") as raw_root:
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
            missing = PACKAGED_CONFIGS - names
            if missing:
                raise RuntimeError(
                    f"Feature Routing wheel is missing configs: {sorted(missing)}"
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
                    "from arkts_code_reviewer.feature_routing.config import "
                    "DEFAULT_DIMENSIONS_PATH, DEFAULT_TAGS_PATH; "
                    "from arkts_code_reviewer.feature_routing.engine import FeatureRouter; "
                    "package = Path(arkts_code_reviewer.__file__).resolve(); "
                    "assert package.is_relative_to(Path.cwd() / 'unpacked'); "
                    "assert DEFAULT_TAGS_PATH.is_file(); "
                    "assert DEFAULT_DIMENSIONS_PATH.is_file(); "
                    "result = FeatureRouter().route([]); "
                    "assert result.units == (); "
                    "print(result.feature_config_version)"
                ),
            ],
            check=True,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
        )
        print("Feature Routing wheel smoke: passed")
        print(f"  wheel: {wheels[0].name}")
        print(f"  {completed.stdout.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
