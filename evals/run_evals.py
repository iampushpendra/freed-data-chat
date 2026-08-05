#!/usr/bin/env python3
"""Query-evaluation harness for the FREED "Analyst" data chatbot.

Maps natural-language questions to canonical KB SQL recipes (evals/golden.yaml,
seeded from context/schema.md's "DERIVED BUSINESS METRICS" and "Example Q -> SQL"
sections) and checks whether the bot's generated SQL/answer agrees.

Pure stdlib Python 3. Two modes:

  --canonical-only (default)
      Run each enabled case's canonical_sql against the live query API and
      record the value. No LLM involved. Fast regression check that the KB
      SQL still matches the warehouse schema. Exits nonzero if any enabled
      case errors.

  --full
      Additionally logs into the live chatbot (POST /api/login), asks it
      each case's natural-language `question` (POST /api/chat), extracts the
      numeric/tabular result from the response's `tables[0]`, and compares it
      to the canonical value per the case's `match`/`tolerance`. Prints a
      PASS/FAIL per case with both SQL strings and both values, plus a
      summary. Exits nonzero if any enabled case FAILs.

Config (env vars):
  QUERY_API_URL     e.g. https://<tunnel>/query  (required for both modes)
  QUERY_API_SECRET  bearer token for the query API (required for both modes)
  BASE_URL          chatbot base URL (default https://freed-data-chat.vercel.app)
  CHAT_PASSWORD     shared chatbot password (required for --full)

Usage:
  python3 evals/run_evals.py --canonical-only
  python3 evals/run_evals.py --full
  python3 evals/run_evals.py --full --json evals/report.json
"""

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 70
DEFAULT_BASE_URL = "https://freed-data-chat.vercel.app"

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.yaml")


# --------------------------------------------------------------------------
# Minimal YAML loader (pure stdlib) - only needs to understand the shape of
# evals/golden.yaml: a top-level `version` scalar and a `cases:` list of flat
# mappings, one of whose values (`canonical_sql`) is a literal block scalar
# (`key: |`). Not a general-purpose YAML parser.
# --------------------------------------------------------------------------

def _coerce_scalar(raw):
    raw = raw.strip()
    if raw == "":
        return None
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    if raw in ("null", "~"):
        return None
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        return raw[1:-1]
    try:
        if re.fullmatch(r"-?\d+", raw):
            return int(raw)
        if re.fullmatch(r"-?\d+\.\d+", raw):
            return float(raw)
    except ValueError:
        pass
    return raw


def load_golden_yaml(path):
    with open(path, "r") as f:
        lines = f.readlines()

    # strip full-line comments and trailing newlines; keep blank lines (they
    # matter for block-scalar termination logic below is unaffected since we
    # only use indentation)
    raw_lines = [ln.rstrip("\n") for ln in lines]

    def indent_of(line):
        return len(line) - len(line.lstrip(" "))

    i = 0
    n = len(raw_lines)
    doc = {"version": None, "cases": []}

    # find top-level version:
    while i < n:
        line = raw_lines[i]
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "":
            i += 1
            continue
        m = re.match(r"^version:\s*(.*)$", stripped)
        if m:
            doc["version"] = _coerce_scalar(m.group(1))
            i += 1
            continue
        if stripped == "cases:":
            i += 1
            break
        i += 1

    current = None
    while i < n:
        line = raw_lines[i]
        stripped = line.strip()

        if stripped == "" or stripped.startswith("#"):
            i += 1
            continue

        ind = indent_of(line)
        m_item = re.match(r"^(\s*)-\s+(\S[\w-]*):\s*(.*)$", line)
        m_key = re.match(r"^(\s*)(\S[\w-]*):\s*(.*)$", line)

        if m_item and ind <= 2:
            # new case
            if current is not None:
                doc["cases"].append(current)
            key, val = m_item.group(2), m_item.group(3)
            current = {}
            i = _consume_field(raw_lines, i, ind + 2, key, val, current)
            continue

        if m_key and current is not None and ind >= 4:
            key, val = m_key.group(2), m_key.group(3)
            i = _consume_field(raw_lines, i, ind, key, val, current)
            continue

        i += 1

    if current is not None:
        doc["cases"].append(current)

    return doc


def _consume_field(raw_lines, i, key_indent, key, val, current):
    """Consume the line at i (key: val) plus any following block-scalar
    continuation lines; store into `current`; return index of next
    unconsumed line."""
    n = len(raw_lines)
    val = val.strip()
    if val == "|":
        # literal block scalar: consume subsequent more-indented lines
        block_lines = []
        j = i + 1
        block_indent = None
        while j < n:
            line = raw_lines[j]
            if line.strip() == "":
                block_lines.append("")
                j += 1
                continue
            ind = len(line) - len(line.lstrip(" "))
            if ind <= key_indent:
                break
            if block_indent is None:
                block_indent = ind
            block_lines.append(line[block_indent:] if len(line) >= block_indent else line.strip())
            j += 1
        # trim trailing blank lines
        while block_lines and block_lines[-1] == "":
            block_lines.pop()
        current[key] = "\n".join(block_lines) + "\n"
        return j
    else:
        current[key] = _coerce_scalar(val)
        return i + 1


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

class HttpResult:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}


def http_post_json(url, payload, headers=None, timeout=DEFAULT_TIMEOUT, opener=None):
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    req_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    open_fn = opener.open if opener is not None else urllib.request.urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"_raw": body}
            return HttpResult(resp.status, parsed, dict(resp.headers))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"error": body}
        return HttpResult(e.code, parsed, dict(e.headers or {}))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return HttpResult(0, {"error": str(e)})


def run_query_api(sql, query_api_url, query_api_secret, timeout=DEFAULT_TIMEOUT):
    result = http_post_json(
        query_api_url,
        {"sql": sql},
        headers={"Authorization": f"Bearer {query_api_secret}"},
        timeout=timeout,
    )
    return result


# --------------------------------------------------------------------------
# Comparison helpers
# --------------------------------------------------------------------------

def to_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def compare_scalar(expected, actual, tolerance):
    exp_n = to_number(expected)
    act_n = to_number(actual)
    if exp_n is None or act_n is None:
        return exp_n == act_n, "non-numeric comparison"
    if exp_n == 0:
        ok = abs(act_n - exp_n) <= tolerance
    else:
        ok = abs(act_n - exp_n) / abs(exp_n) <= tolerance
    return ok, f"|{act_n} - {exp_n}| relative diff vs tolerance {tolerance}"


def compare_rowcount(expected_count, actual_count):
    return expected_count == actual_count, f"expected {expected_count} rows, got {actual_count}"


def _normalize_rows(rows):
    return set(tuple(str(v) for v in row) for row in rows)


def compare_resultset(expected_rows, actual_rows):
    exp_set = _normalize_rows(expected_rows)
    act_set = _normalize_rows(actual_rows)
    return exp_set == act_set, f"expected {len(exp_set)} distinct rows, got {len(act_set)} distinct rows"


# --------------------------------------------------------------------------
# Canonical execution
# --------------------------------------------------------------------------

def execute_canonical(case, query_api_url, query_api_secret, timeout):
    sql = case["canonical_sql"]
    t0 = time.time()
    result = run_query_api(sql, query_api_url, query_api_secret, timeout=timeout)
    elapsed = time.time() - t0
    if result.status != 200:
        err = result.body.get("error") if isinstance(result.body, dict) else str(result.body)
        return {"ok": False, "error": err, "elapsed_s": round(elapsed, 1), "raw": result.body}
    body = result.body
    return {
        "ok": True,
        "columns": body.get("columns"),
        "rows": body.get("rows"),
        "row_count": body.get("row_count"),
        "truncated": body.get("truncated"),
        "elapsed_s": round(elapsed, 1),
    }


def canonical_value_for_match(canon, match):
    """Extract the value to compare, given the match kind, from a query-API
    result dict (as returned by execute_canonical)."""
    if not canon["ok"]:
        return None
    rows = canon.get("rows") or []
    if match == "scalar":
        if rows and rows[0]:
            return rows[0][0]
        return None
    if match == "rowcount":
        return canon.get("row_count")
    if match == "resultset":
        return rows
    return None


# --------------------------------------------------------------------------
# Chatbot (--full mode)
# --------------------------------------------------------------------------

def chatbot_login(base_url, password, timeout):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    result = http_post_json(
        base_url.rstrip("/") + "/api/login",
        {"password": password},
        timeout=timeout,
        opener=opener,
    )
    if result.status != 200:
        raise RuntimeError(f"login failed: HTTP {result.status} {result.body}")
    return opener


def chatbot_ask(opener, base_url, question, timeout):
    result = http_post_json(
        base_url.rstrip("/") + "/api/chat",
        {"messages": [{"role": "user", "content": question}]},
        timeout=timeout,
        opener=opener,
    )
    return result


def chat_value_for_match(chat_body, match):
    """Extract the value to compare from a /api/chat response body, using
    tables[0] per the harness contract."""
    tables = chat_body.get("tables") if isinstance(chat_body, dict) else None
    if not tables:
        return None, None
    table = tables[0]
    sql = table.get("sql")
    rows = table.get("rows") or []
    if match == "scalar":
        if rows and rows[0]:
            return rows[0][0], sql
        return None, sql
    if match == "rowcount":
        return table.get("row_count"), sql
    if match == "resultset":
        return rows, sql
    return None, sql


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_config():
    return {
        "query_api_url": os.environ.get("QUERY_API_URL"),
        "query_api_secret": os.environ.get("QUERY_API_SECRET"),
        "base_url": os.environ.get("BASE_URL", DEFAULT_BASE_URL),
        "chat_password": os.environ.get("CHAT_PASSWORD"),
    }


def run_canonical_only(cases, cfg, timeout):
    report = {"mode": "canonical-only", "cases": []}
    any_error = False
    print(f"Running canonical-only checks against {cfg['query_api_url']}\n")
    for case in cases:
        cid = case["id"]
        if not case.get("enabled", True):
            print(f"[SKIP ] {cid}  ({case.get('skip_reason', 'disabled')})")
            report["cases"].append({"id": cid, "status": "SKIP", "skip_reason": case.get("skip_reason")})
            continue
        canon = execute_canonical(case, cfg["query_api_url"], cfg["query_api_secret"], timeout)
        if canon["ok"]:
            value = canonical_value_for_match(canon, case["match"])
            print(f"[OK   ] {cid}  ({canon['elapsed_s']}s, {canon['row_count']} rows) -> {value}")
            report["cases"].append(
                {
                    "id": cid,
                    "status": "OK",
                    "elapsed_s": canon["elapsed_s"],
                    "row_count": canon["row_count"],
                    "value": value,
                }
            )
        else:
            any_error = True
            print(f"[ERROR] {cid}  ({canon['elapsed_s']}s) -> {canon['error']}")
            report["cases"].append({"id": cid, "status": "ERROR", "error": canon["error"]})

    n_ok = sum(1 for c in report["cases"] if c["status"] == "OK")
    n_err = sum(1 for c in report["cases"] if c["status"] == "ERROR")
    n_skip = sum(1 for c in report["cases"] if c["status"] == "SKIP")
    print(f"\nSummary: {n_ok} ok, {n_err} error, {n_skip} skipped (disabled)")
    report["summary"] = {"ok": n_ok, "error": n_err, "skipped": n_skip}
    return report, any_error


def run_full(cases, cfg, timeout):
    report = {"mode": "full", "cases": []}
    any_fail = False

    print(f"Logging into chatbot at {cfg['base_url']}")
    opener = chatbot_login(cfg["base_url"], cfg["chat_password"], timeout)
    print(f"Running full end-to-end checks (canonical query API: {cfg['query_api_url']})\n")

    for case in cases:
        cid = case["id"]
        if not case.get("enabled", True):
            print(f"[SKIP ] {cid}  ({case.get('skip_reason', 'disabled')})")
            report["cases"].append({"id": cid, "status": "SKIP", "skip_reason": case.get("skip_reason")})
            continue

        canon = execute_canonical(case, cfg["query_api_url"], cfg["query_api_secret"], timeout)
        if not canon["ok"]:
            any_fail = True
            print(f"[FAIL ] {cid}  canonical_sql errored: {canon['error']}")
            report["cases"].append(
                {"id": cid, "status": "FAIL", "reason": "canonical_sql errored", "error": canon["error"]}
            )
            continue
        expected = canonical_value_for_match(canon, case["match"])

        chat_result = chatbot_ask(opener, cfg["base_url"], case["question"], timeout)
        if chat_result.status != 200:
            any_fail = True
            err = chat_result.body.get("error") if isinstance(chat_result.body, dict) else chat_result.body
            print(f"[FAIL ] {cid}  /api/chat errored: HTTP {chat_result.status} {err}")
            report["cases"].append(
                {"id": cid, "status": "FAIL", "reason": "chat request errored", "http_status": chat_result.status}
            )
            continue

        actual, actual_sql = chat_value_for_match(chat_result.body, case["match"])
        reply = chat_result.body.get("reply") if isinstance(chat_result.body, dict) else None

        if actual is None:
            any_fail = True
            print(f"[FAIL ] {cid}  bot returned no table/result to compare")
            report["cases"].append(
                {
                    "id": cid,
                    "status": "FAIL",
                    "reason": "no table in chat response",
                    "canonical_sql": case["canonical_sql"],
                    "expected": expected,
                    "reply": reply,
                }
            )
            continue

        if case["match"] == "scalar":
            ok, detail = compare_scalar(expected, actual, case.get("tolerance", 0.0))
        elif case["match"] == "rowcount":
            ok, detail = compare_rowcount(expected, actual)
        elif case["match"] == "resultset":
            ok, detail = compare_resultset(expected, actual)
        else:
            ok, detail = False, f"unknown match type {case['match']}"

        status = "PASS" if ok else "FAIL"
        if not ok:
            any_fail = True
        print(f"[{status}] {cid}  {detail}")
        print(f"         question: {case['question']}")
        print(f"         canonical_sql: {_one_line(case['canonical_sql'])}")
        print(f"         canonical value: {expected}")
        print(f"         bot_sql:       {_one_line(actual_sql or '')}")
        print(f"         bot value:     {actual}")
        print()

        report["cases"].append(
            {
                "id": cid,
                "status": status,
                "detail": detail,
                "question": case["question"],
                "canonical_sql": case["canonical_sql"],
                "expected": expected,
                "bot_sql": actual_sql,
                "actual": actual,
                "reply": reply,
            }
        )

    n_pass = sum(1 for c in report["cases"] if c["status"] == "PASS")
    n_fail = sum(1 for c in report["cases"] if c["status"] == "FAIL")
    n_skip = sum(1 for c in report["cases"] if c["status"] == "SKIP")
    print(f"Summary: {n_pass} passed, {n_fail} failed, {n_skip} skipped (disabled)")
    report["summary"] = {"passed": n_pass, "failed": n_fail, "skipped": n_skip}
    return report, any_fail


def _one_line(sql):
    return " ".join(sql.split())


def main():
    parser = argparse.ArgumentParser(description="FREED Analyst chatbot query-evaluation harness")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--canonical-only",
        action="store_true",
        help="Run only the canonical KB SQL against the query API (default mode).",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="Additionally exercise the live chatbot end-to-end and compare answers.",
    )
    parser.add_argument("--json", metavar="PATH", help="Also write a machine-readable JSON report to PATH.")
    parser.add_argument(
        "--golden",
        default=GOLDEN_PATH,
        help=f"Path to golden.yaml (default: {GOLDEN_PATH})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Per-request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    args = parser.parse_args()

    cfg = load_config()
    if not cfg["query_api_url"] or not cfg["query_api_secret"]:
        print("ERROR: QUERY_API_URL and QUERY_API_SECRET must be set.", file=sys.stderr)
        sys.exit(2)

    doc = load_golden_yaml(args.golden)
    cases = doc["cases"]
    if not cases:
        print(f"ERROR: no cases loaded from {args.golden}", file=sys.stderr)
        sys.exit(2)

    if args.full:
        if not cfg["chat_password"]:
            print("ERROR: CHAT_PASSWORD must be set for --full mode.", file=sys.stderr)
            sys.exit(2)
        report, failed = run_full(cases, cfg, args.timeout)
    else:
        report, failed = run_canonical_only(cases, cfg, args.timeout)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nJSON report written to {args.json}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
