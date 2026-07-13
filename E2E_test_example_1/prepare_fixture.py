#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import subprocess
from pathlib import Path

SOURCE_REVISION = "8255a2987f70317cc3a2a4d46044c6b55f092bb3"
SOURCE_RELATIVE_PATH = (
    "code/BasicFeature/Media/AVSession/VideoPlayer/entry/src/main/ets/pages/Index.ets"
)
SOURCE_SHA256 = "6d9f373ca3ea6cf1b0386f4e92dd9fe785cc421263e3d7c6500d5a35fb808c1a"
SYNTHETIC_HEAD_REVISION = "synthetic-e2e-example-1-v1"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"fixture mutation {label!r} expected one match, found {count}")
    return source.replace(old, new, 1)


def build_head(base: str) -> str:
    head = base
    head = _replace_once(
        head,
        "  private sliderTimer?: number;\n",
        """  private sliderTimer?: number;
  private networkRetryTimer?: number;
  private isDisposed: boolean = false;
  private readonly networkRetryDelayMs: number = 1000;
""",
        "runtime state fields",
    )
    head = _replace_once(
        head,
        "  async aboutToAppear() {\n    Log.info('about to appear');\n",
        """  async aboutToAppear() {
    Log.info('about to appear');
    this.isDisposed = false;
    this.clearRuntimeTimers();
""",
        "lifecycle initialization",
    )
    head = _replace_once(
        head,
        """    this.avPlayer?.on('timeUpdate', (time: number) => {
      Log.info('timeUpdate time: ' + time);
      if (!this.isProgressSliding) {
        if (this.duration == 0) {
          this.seedPosition = 0;
        } else {
          this.seedPosition = time / this.duration * 100;
        }
        const params: avSession.AVPlaybackState = {
          position: {
            elapsedTime: time,
            updateTime: new Date().getTime()
          },
        };
        this.session?.setAVPlaybackState(params);
      }
    })
""",
        """    this.avPlayer?.on('timeUpdate', (time: number) => {
      this.updatePlaybackProgress(time);
    })
""",
        "progress callback delegation",
    )
    head = _replace_once(
        head,
        "  readLRCFile(): void {\n",
        """  private updatePlaybackProgress(time: number): void {
    if (this.isDisposed || this.isProgressSliding) {
      return;
    }
    Log.info('timeUpdate time: ' + time);
    this.seedPosition = this.duration === 0 ? 0 : time / this.duration * 100;
    const params: avSession.AVPlaybackState = {
      position: {
        elapsedTime: time,
        updateTime: new Date().getTime()
      },
    };
    this.session?.setAVPlaybackState(params);
  }

  readLRCFile(): void {
""",
        "guarded progress update",
    )
    head = _replace_once(
        head,
        """    this.netCon?.on('netAvailable', data => {
      Log.info('network Available: ' + JSON.stringify(data));
      this.hasNetwork = true;
    })
    this.netCon?.on('netLost', data => {
      Log.info('network Lost: ' + JSON.stringify(data));
      connection.getAllNets().then(data => {
        Log.info('get all network: ' + JSON.stringify(data));
        this.hasNetwork = data?.length > 0;
      });
    })
  }

  onPageHide() {
""",
        """    this.netCon?.on('netAvailable', data => {
      Log.info('network Available: ' + JSON.stringify(data));
      this.hasNetwork = true;
      this.clearNetworkRetryTimer();
    })
    this.netCon?.on('netLost', data => {
      Log.info('network Lost: ' + JSON.stringify(data));
      this.hasNetwork = false;
      this.scheduleNetworkRecovery();
    })
  }

  private clearNetworkRetryTimer(): void {
    if (this.networkRetryTimer !== undefined) {
      clearTimeout(this.networkRetryTimer);
      this.networkRetryTimer = undefined;
    }
  }

  private scheduleNetworkRecovery(): void {
    this.clearNetworkRetryTimer();
    if (this.isDisposed) {
      return;
    }
    this.networkRetryTimer = setTimeout(() => {
      if (this.isDisposed) {
        return;
      }
      connection.getAllNets()
        .then(data => {
          this.hasNetwork = data?.length > 0;
          if (!this.hasNetwork) {
            this.scheduleNetworkRecovery();
          }
        })
        .catch((error: BusinessError) => {
          Log.error('network recovery failed: ' + JSON.stringify(error));
          this.scheduleNetworkRecovery();
        });
    }, this.networkRetryDelayMs);
  }

  onPageHide() {
""",
        "network retry lifecycle",
    )
    head = _replace_once(
        head,
        """  aboutToDisappear() {
    Log.info('about to disappear');
""",
        """  private clearRuntimeTimers(): void {
    if (this.sliderTimer !== undefined) {
      clearTimeout(this.sliderTimer);
      this.sliderTimer = undefined;
    }
    this.clearNetworkRetryTimer();
  }

  private removePlayerListeners(): void {
    this.avPlayer?.off('audioInterrupt');
    this.avPlayer?.off('timeUpdate');
    this.avPlayer?.off('durationUpdate');
    this.avPlayer?.off('videoSizeChange');
  }

  aboutToDisappear() {
    Log.info('about to disappear');
    this.isDisposed = true;
    this.clearRuntimeTimers();
    this.removePlayerListeners();
""",
        "runtime cleanup helpers",
    )
    head = _replace_once(
        head,
        """    this.netCon?.unregister((error) => {
      Log.info('error is: ' + JSON.stringify(error));
    })
  }
""",
        """    this.netCon?.unregister((error) => {
      Log.info('error is: ' + JSON.stringify(error));
    })
    this.netCon = undefined;
    this.controller = undefined;
    this.castController = undefined;
    this.session = undefined;
    this.avPlayer = undefined;
  }
""",
        "released reference reset",
    )
    head = _replace_once(
        head,
        """                  if (event.type === TouchType.Up) {
                    this.sliderTimer = setTimeout(() => {
                      this.isProgressSliding = false;
                    }, 200);
                  } else {
                    clearTimeout(this.sliderTimer);
                    this.isProgressSliding = true;
                  }
""",
        """                  if (event.type === TouchType.Up) {
                    if (this.sliderTimer !== undefined) {
                      clearTimeout(this.sliderTimer);
                    }
                    this.sliderTimer = setTimeout(() => {
                      if (!this.isDisposed) {
                        this.isProgressSliding = false;
                      }
                      this.sliderTimer = undefined;
                    }, 200);
                  } else {
                    if (this.sliderTimer !== undefined) {
                      clearTimeout(this.sliderTimer);
                      this.sliderTimer = undefined;
                    }
                    this.isProgressSliding = true;
                  }
""",
        "slider timer ownership",
    )
    return head


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git(source_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"cannot verify source checkout: {detail}")
    return completed.stdout.strip()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze E2E example 1 source and diff")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("/home/autken/Code/applications_app_samples"),
    )
    args = parser.parse_args()

    source_root = args.source_root.resolve(strict=True)
    if _git(source_root, "rev-parse", "HEAD") != SOURCE_REVISION:
        raise RuntimeError("applications_app_samples revision drift")
    if _git(source_root, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("applications_app_samples tracked files must be clean")

    source_path = source_root / SOURCE_RELATIVE_PATH
    base_raw = source_path.read_bytes()
    if _sha256(base_raw) != SOURCE_SHA256:
        raise RuntimeError("selected source content hash drift")
    base = base_raw.decode("utf-8")
    head = build_head(base)
    head_raw = head.encode("utf-8")

    base_lines = base.splitlines()
    head_lines = head.splitlines()
    matcher = difflib.SequenceMatcher(a=base_lines, b=head_lines, autojunk=False)
    opcodes: list[dict[str, object]] = []
    added_lines = 0
    deleted_lines = 0
    formal_added_lines = 0
    formal_deleted_lines = 0
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_count = old_end - old_start
        new_count = new_end - new_start
        added_lines += new_count
        deleted_lines += old_count
        raw_added = list(range(new_start + 1, new_end + 1))
        raw_deleted = list(range(old_start + 1, old_end + 1))
        assigned_added = [line for line in raw_added if head_lines[line - 1].strip()]
        assigned_deleted = [line for line in raw_deleted if base_lines[line - 1].strip()]
        formal_added_lines += len(assigned_added)
        formal_deleted_lines += len(assigned_deleted)
        opcodes.append(
            {
                "kind": tag,
                "old_span": (
                    None if old_count == 0 else {"start_line": old_start + 1, "end_line": old_end}
                ),
                "new_span": (
                    None if new_count == 0 else {"start_line": new_start + 1, "end_line": new_end}
                ),
                "added_new_lines": assigned_added,
                "deleted_old_lines": assigned_deleted,
                "raw_added_new_lines": raw_added,
                "raw_deleted_old_lines": raw_deleted,
            }
        )

    if (len(base_lines), len(head_lines), added_lines, deleted_lines) != (
        984,
        1051,
        88,
        21,
    ):
        raise RuntimeError("synthetic diff statistics drift")

    root = Path(__file__).resolve().parent
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "base.ets").write_bytes(base_raw)
    (inputs / "head.ets").write_bytes(head_raw)
    diff = "".join(
        difflib.unified_diff(
            base.splitlines(keepends=True),
            head.splitlines(keepends=True),
            fromfile=f"a/{SOURCE_RELATIVE_PATH}",
            tofile=f"b/{SOURCE_RELATIVE_PATH}",
            n=3,
        )
    )
    (inputs / "diff.patch").write_text(diff, encoding="utf-8")

    _write_json(
        inputs / "provenance.json",
        {
            "schema_version": "e2e-source-provenance-v1",
            "source_id": "applications_app_samples",
            "repository_root_hint": str(source_root),
            "revision": SOURCE_REVISION,
            "relative_path": SOURCE_RELATIVE_PATH,
            "content_sha256": f"sha256:{SOURCE_SHA256}",
            "line_count": len(base_lines),
            "synthetic_head_revision": SYNTHETIC_HEAD_REVISION,
            "synthetic_head_sha256": f"sha256:{_sha256(head_raw)}",
            "synthetic_head_line_count": len(head_lines),
        },
    )
    _write_json(
        inputs / "mutation_spec.json",
        {
            "schema_version": "e2e-mutation-spec-v1",
            "purpose": (
                "Refactor playback progress updates and make network/slider timers "
                "explicitly owned by the page lifecycle."
            ),
            "diff_stats": {
                "added_new_lines": added_lines,
                "deleted_old_lines": deleted_lines,
                "total_diff_lines": added_lines + deleted_lines,
            },
            "formal_change_stats": {
                "assigned_added_new_lines": formal_added_lines,
                "assigned_deleted_old_lines": formal_deleted_lines,
                "assigned_total_lines": formal_added_lines + formal_deleted_lines,
                "excluded_whitespace_only_diff_lines": (
                    added_lines + deleted_lines - formal_added_lines - formal_deleted_lines
                ),
            },
            "topics": [
                "async-callback",
                "lifecycle-cleanup",
                "network-retry",
                "state-management",
                "timer-ownership",
            ],
            "opcodes": opcodes,
        },
    )
    print(
        json.dumps(
            {
                "base_lines": len(base_lines),
                "head_lines": len(head_lines),
                "added_new_lines": added_lines,
                "deleted_old_lines": deleted_lines,
                "head_sha256": f"sha256:{_sha256(head_raw)}",
                "opcode_count": len(opcodes),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
