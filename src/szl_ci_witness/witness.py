"""szl-ci-witness: CI as witness.

Every workflow run appends one receipt to ci-witness.jsonl: repo, commit,
run id, conclusion, test counts, python version, timestamp - hash-chained to
the previous run. Anyone can recompute the chain offline. Regressions and
fixes are detected from the linkage, not from a dashboard.

Doctrine:
- The chain is append-only; a rewritten history breaks linkage and is INVALID.
- A red run is a witness event too - the chain records failures, never hides them.
- Timestamps are reported, never backfilled.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys
from typing import Any, Dict, List, Tuple

GENESIS = "0" * 64
DEFAULT_CHAIN = "ci-witness.jsonl"

def canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)

def witness_record(repo: str, commit: str, run_id: str, conclusion: str,
                   passed: int, failed: int, python_version: str,
                   prev_hash: str = GENESIS, ts: str = "") -> Dict[str, Any]:
    """One CI run as a hash-chained witness receipt."""
    rec = {"repo": repo, "commit": commit, "run_id": str(run_id),
           "conclusion": conclusion,
           "tests": {"passed": int(passed), "failed": int(failed)},
           "python": python_version, "ts": ts}
    payload = dict(rec)
    rec["prev_hash"] = prev_hash
    rec["chain_hash"] = hashlib.sha256((prev_hash + canon(payload)).encode()).hexdigest()
    return rec

def load_chain(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

def append_record(path: str, rec: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")

def verify_witness_chain(records: List[Dict[str, Any]]) -> Tuple[bool, str]:
    if not records:
        return False, "empty witness chain"
    prev = GENESIS
    for i, r in enumerate(records):
        if r.get("prev_hash") != prev:
            return False, f"link broken at record {i}"
        payload = {k: v for k, v in r.items() if k not in ("prev_hash", "chain_hash")}
        if r.get("chain_hash") != hashlib.sha256((prev + canon(payload)).encode()).hexdigest():
            return False, f"record {i} tampered"
        prev = r["chain_hash"]
    return True, f"witness chain valid ({len(records)} runs)"

def witness_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok, detail = verify_witness_chain(records)
    if not ok:
        return {"state": "INVALID", "reason": detail}
    total = len(records)
    greens = sum(1 for r in records if r["conclusion"] == "success")
    regressions = [r["run_id"] for i, r in enumerate(records)
                   if r["conclusion"] != "success" and i > 0 and records[i - 1]["conclusion"] == "success"]
    fixes = [r["run_id"] for i, r in enumerate(records)
             if r["conclusion"] == "success" and i > 0 and records[i - 1]["conclusion"] != "success"]
    return {"state": "MEASURED", "runs": total, "green": greens, "red": total - greens,
            "regressions": regressions, "fixes": fixes,
            "terminal": records[-1]["chain_hash"][:16],
            "label": "CI witness chain - recomputed, not asserted"}

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="szl-ci-witness")
    p.add_argument("command", choices=["record", "verify", "summary"])
    p.add_argument("--chain", default=DEFAULT_CHAIN)
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    p.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    p.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    p.add_argument("--conclusion", default="")
    p.add_argument("--passed", type=int, default=0)
    p.add_argument("--failed", type=int, default=0)
    p.add_argument("--python", default="%d.%d" % sys.version_info[:2])
    p.add_argument("--ts", default=os.environ.get("SZL_WITNESS_TS", ""))
    args = p.parse_args(argv)

    if args.command == "record":
        if not (args.repo and args.commit and args.run_id and args.conclusion):
            print("record needs repo, commit, run-id, conclusion (env or flags)", file=sys.stderr)
            return 2
        chain = load_chain(args.chain)
        prev = chain[-1]["chain_hash"] if chain else GENESIS
        rec = witness_record(args.repo, args.commit, args.run_id, args.conclusion,
                             args.passed, args.failed, args.python, prev, args.ts)
        append_record(args.chain, rec)
        print(json.dumps({"recorded": rec["run_id"], "chain_hash": rec["chain_hash"][:16]}))
        return 0
    chain = load_chain(args.chain)
    if args.command == "verify":
        ok, detail = verify_witness_chain(chain)
        print(detail)
        return 0 if ok else 1
    print(json.dumps(witness_summary(chain), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
