"""Migrate Mini-Agent chat logs into deer-flow threads.

Usage (run from this directory or anywhere with python 3.10+):

    python migrate.py --logs ~/.mini-agent/log --base-url http://localhost:3000

What it does, per ``agent_run_*.log`` file under ``--logs``:

1. Parse the log into an ordered Mini-Agent message list (see log_parser).
2. Convert each message to a LangGraph message dict (see converter).
3. ``POST /api/threads`` to create a fresh deer-flow thread.
4. ``POST /api/threads/{id}/state`` to inject messages + title in one shot.

Safety features:
* ``--dry-run``        parse + convert only, print what would be sent, write nothing.
* ``--limit N``        only process the first N log files (useful for smoke tests).
* ``--state-file``     persist the set of already-imported source files so a
                       crashed run can be resumed without duplicating threads.
* ``--failures-file``  records every log file that errored, for a retry pass.
* Per-file errors never abort the whole run.

The script uses only the Python standard library (plus the sibling modules in
this folder), so it can be run with the same interpreter that runs deer-flow.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable

# Allow running both as ``python migrate.py`` from this folder and as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from converter import convert_session  # noqa: E402
from deerflow_client import DeerFlowClient, DeerFlowError  # noqa: E402
from log_parser import ParsedSession, parse_log_file  # noqa: E402

DEFAULT_LOG_DIR = Path.home() / ".mini-agent" / "log"
DEFAULT_STATE_FILE = Path(__file__).resolve().parent / "migration-state.json"
DEFAULT_FAILURES_FILE = Path(__file__).resolve().parent / "migration-failures.json"


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def discover_logs(log_dir: Path) -> list[Path]:
    """Return all ``agent_run_*.log`` files sorted oldest-first.

    Oldest-first keeps the imported threads in chronological order in the
    deer-flow sidebar (which typically lists by ``updated_at`` desc, but the
    creation order still matters for the state file).
    """
    if not log_dir.exists():
        return []
    files = sorted(
        (p for p in log_dir.glob("*.log") if p.is_file()),
        key=lambda p: p.name,
    )
    return files


# --------------------------------------------------------------------------- #
# State persistence (for resume)
# --------------------------------------------------------------------------- #
def load_state(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return set(data.get("done", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_state(state_file: Path, done: set[str]) -> None:
    payload = {"done": sorted(done)}
    state_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def record_failure(failures_file: Path, entry: dict) -> None:
    existing: list[dict] = []
    if failures_file.exists():
        try:
            existing = json.loads(failures_file.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.append(entry)
    failures_file.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Core per-file migration
# --------------------------------------------------------------------------- #
def migrate_one(
    client: DeerFlowClient | None,
    log_path: Path,
    dry_run: bool,
) -> dict:
    """Migrate a single log file. Returns a summary dict.

    When ``dry_run`` is True or ``client`` is None, nothing is written to the
    server; the summary just reports what *would* happen. Errors are caught
    and returned in the summary rather than raised, so the caller can keep
    processing the rest of the batch.
    """
    summary: dict = {
        "file": str(log_path),
        "status": "ok",
        "thread_id": None,
        "message_count": 0,
        "title": "",
        "error": None,
    }
    try:
        session: ParsedSession = parse_log_file(log_path)
        if session.is_empty:
            summary["status"] = "skipped"
            summary["error"] = "no user/assistant messages found"
            return summary

        messages, title = convert_session(session)
        summary["message_count"] = len(messages)
        summary["title"] = title

        if not messages:
            summary["status"] = "skipped"
            summary["error"] = "conversion produced no messages"
            return summary

        if dry_run or client is None:
            summary["status"] = "dry-run"
            return summary

        thread_id = client.create_thread()
        client.inject_messages(thread_id, messages, title)
        summary["thread_id"] = thread_id
        return summary
    except DeerFlowError as exc:
        summary["status"] = "error"
        summary["error"] = f"API: {exc}"
        return summary
    except Exception as exc:  # pragma: no cover - defensive
        summary["status"] = "error"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def iter_summaries(items: Iterable[dict]) -> None:
    """Print a compact line per migrated file as we go."""
    for s in items:
        status = s["status"]
        marker = {
            "ok": "✓",
            "dry-run": "·",
            "skipped": "→",
            "error": "✗",
        }.get(status, "?")
        extra = ""
        if status == "ok":
            extra = f" thread={s['thread_id']} msgs={s['message_count']} «{s['title']}»"
        elif status == "dry-run":
            extra = f" msgs={s['message_count']} «{s['title']}»"
        elif status == "error":
            extra = f" {s['error']}"
        elif status == "skipped":
            extra = f" {s['error']}"
        print(f"  {marker} {Path(s['file']).name}{extra}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Migrate Mini-Agent chat logs into deer-flow threads.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Dry run: parse and convert only, report counts/titles
  python migrate.py --logs ~/.mini-agent/log --dry-run

  # Real migration against a local deer-flow
  python migrate.py --logs ~/.mini-agent/log --base-url http://localhost:3000

  # Smoke test: just one file, then review it in the UI before the full run
  python migrate.py --logs ~/.mini-agent/log --base-url http://localhost:3000 --limit 1
""",
    )
    p.add_argument(
        "--logs",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help=f"Mini-Agent log directory (default: {DEFAULT_LOG_DIR})",
    )
    p.add_argument(
        "--base-url",
        default=None,
        help="deer-flow base URL, e.g. http://localhost:3000. Required unless --dry-run.",
    )
    p.add_argument(
        "--auth-token",
        default=None,
        help="Optional bearer token if your deer-flow gateway requires auth.",
    )
    p.add_argument("--dry-run", action="store_true", help="Parse + convert only; write nothing.")
    p.add_argument("--limit", type=int, default=None, help="Process at most N log files.")
    p.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="Resume state file (records imported source files).",
    )
    p.add_argument(
        "--failures-file",
        type=Path,
        default=DEFAULT_FAILURES_FILE,
        help="Where to record files that errored, for a later retry pass.",
    )
    p.add_argument(
        "--reset-state",
        action="store_true",
        help="Ignore the existing state file and re-import everything.",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to sleep between files (throttle, in case the API is rate-limited).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.dry_run and not args.base_url:
        print("error: --base-url is required unless --dry-run is set", file=sys.stderr)
        return 2

    log_dir: Path = args.logs
    if not log_dir.exists():
        print(f"error: log directory not found: {log_dir}", file=sys.stderr)
        return 2

    log_files = discover_logs(log_dir)
    if not log_files:
        print(f"No .log files found in {log_dir}", file=sys.stderr)
        return 1

    if args.limit is not None:
        log_files = log_files[: args.limit]

    # Resume support.
    done: set[str] = set()
    if args.reset_state and args.state_file.exists():
        args.state_file.unlink()
    else:
        done = load_state(args.state_file)

    client: DeerFlowClient | None = None
    if not args.dry_run:
        client = DeerFlowClient(args.base_url, auth_token=args.auth_token)

    mode = "DRY-RUN" if args.dry_run else "MIGRATE"
    print(f"{mode}: {len(log_files)} file(s) in {log_dir}")
    print(f"  (skipping {len(done)} already-imported from {args.state_file.name})")
    print()

    summaries: list[dict] = []
    processed = 0
    for log_path in log_files:
        key = log_path.name
        if key in done and not args.dry_run:
            continue
        result = migrate_one(client, log_path, dry_run=args.dry_run)
        summaries.append(result)
        processed += 1

        # Print incrementally for visibility on long runs.
        iter_summaries([result])

        if not args.dry_run:
            if result["status"] == "ok":
                done.add(key)
                save_state(args.state_file, done)
            else:
                record_failure(
                    args.failures_file,
                    {"file": str(log_path), "status": result["status"], "error": result["error"]},
                )
        if args.delay:
            time.sleep(args.delay)

    # Final tally.
    counts = {"ok": 0, "dry-run": 0, "skipped": 0, "error": 0}
    for s in summaries:
        counts[s["status"]] = counts.get(s["status"], 0) + 1

    print()
    print("Done.")
    print(f"  processed : {processed}")
    for k in ("ok", "dry-run", "skipped", "error"):
        if counts.get(k):
            print(f"  {k:<9}: {counts[k]}")
    if counts.get("error"):
        print(f"  failures recorded in: {args.failures_file}")
        print("  re-run the same command to retry (failed files are not in the state file).")
    return 0 if counts.get("error", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
