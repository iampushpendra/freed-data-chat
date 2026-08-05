#!/usr/bin/env python3
"""FREED Data Chat — audit-log reader (convenience tool, not a server).

The VM query API (vm/query_api.py) appends one JSON line per /query request
to /home/ubuntu/freed-data-chat/chat-query.log on the VM. Each line may
carry the optional "question"/"chat_id" logging-only fields (see
docs/CONTRACTS.md) alongside sql, row_count/error, elapsed_ms, etc.

This script prints a compact, newest-last table of recent logged runs so
they can be eyeballed for eval/review. It does not connect to the VM itself
-- either point it at a local copy of the log with --file, or it prints the
ssh/scp command to pull one.

Usage:
    python3 evals/read_log.py --file /path/to/chat-query.log
    python3 evals/read_log.py --file chat-query.log --limit 50
    python3 evals/read_log.py --file chat-query.log --errors-only
    python3 evals/read_log.py            # prints pull instructions
"""
import argparse
import json
import sys

VM_HOST = "ubuntu@172.20.4.15"
VM_LOG_PATH = "/home/ubuntu/freed-data-chat/chat-query.log"
SSH_KEY = "~/installs/keys/freed-live-gcp.pem"

QUESTION_COL_WIDTH = 60


def print_pull_instructions():
    print("No --file given. This log lives on the VM, not locally.")
    print()
    print("Pull a copy first, e.g.:")
    print()
    print(f"  scp -i {SSH_KEY} {VM_HOST}:{VM_LOG_PATH} ./chat-query.log")
    print()
    print("Then rerun:")
    print()
    print("  python3 evals/read_log.py --file ./chat-query.log")
    print()
    print("Or tail it live over ssh without copying:")
    print()
    print(f"  ssh -i {SSH_KEY} {VM_HOST} 'tail -n 200 {VM_LOG_PATH}'")


def parse_lines(path):
    """Yield parsed dicts for each valid JSON line in `path`. Lines that
    fail to parse are skipped (never raise) -- a corrupt line must not stop
    the whole read."""
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def is_error_record(record):
    return bool(record.get("error"))


def truncate(s, n):
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def format_row(record):
    ts = record.get("ts", "")
    chat_id = record.get("chat_id") or ""
    question = truncate(record.get("question"), QUESTION_COL_WIDTH)
    if record.get("error"):
        outcome = f"ERROR: {truncate(record.get('error'), 40)}"
    else:
        outcome = f"rows={record.get('row_count', '?')}"
    elapsed = record.get("elapsed_ms", "")
    return f"{ts:<26} {chat_id:<20} {question:<{QUESTION_COL_WIDTH}} {outcome:<50} {elapsed}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", help="local path to a chat-query.log copy")
    parser.add_argument("--limit", type=int, default=20, help="max rows to print (default 20)")
    parser.add_argument("--errors-only", action="store_true", help="only print records with an error")
    args = parser.parse_args(argv)

    if not args.file:
        print_pull_instructions()
        return 0

    try:
        records = list(parse_lines(args.file))
    except OSError as e:
        print(f"error reading {args.file}: {e}", file=sys.stderr)
        return 1

    if args.errors_only:
        records = [r for r in records if is_error_record(r)]

    # Newest last: the log is append-only, so file order is already
    # chronological -- just take the last N.
    records = records[-args.limit:] if args.limit > 0 else records

    header = f"{'ts':<26} {'chat_id':<20} {'question':<{QUESTION_COL_WIDTH}} {'outcome':<50} elapsed_ms"
    print(header)
    print("-" * len(header))
    for record in records:
        print(format_row(record))
    print()
    print(f"{len(records)} record(s) shown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
