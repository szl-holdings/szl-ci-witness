"""Every assertion executed green before this file was pushed."""
import copy

from szl_ci_witness import GENESIS, verify_witness_chain, witness_record, witness_summary


def _chain():
    chain, prev = [], GENESIS
    for i, (concl, p, f) in enumerate([("success", 14, 0), ("success", 14, 0),
                                       ("failure", 12, 2), ("success", 14, 0)]):
        r = witness_record("szl-retrieval-bench", f"commit{i:040x}", 100 + i,
                           concl, p, f, "3.12", prev, f"2026-09-04T0{i}:00Z")
        prev = r["chain_hash"]
        chain.append(r)
    return chain


def test_regressions_and_fixes_detected_from_chain():
    s = witness_summary(_chain())
    assert s["state"] == "MEASURED"
    assert s["runs"] == 4 and s["green"] == 3 and s["red"] == 1
    assert s["regressions"] == ["102"]
    assert s["fixes"] == ["103"]


def test_tampered_record_invalidates_chain():
    bad = copy.deepcopy(_chain())
    bad[1]["conclusion"] = "failure"
    assert witness_summary(bad)["state"] == "INVALID"


def test_empty_chain_fails_closed():
    assert witness_summary([])["state"] == "INVALID"
    ok, detail = verify_witness_chain([])
    assert not ok


def test_terminal_deterministic():
    assert witness_summary(_chain())["terminal"] == witness_summary(_chain())["terminal"]


def test_record_fields_are_complete():
    r = witness_record("repo", "c" * 40, "1", "success", 5, 0, "3.11")
    for k in ("repo", "commit", "run_id", "conclusion", "tests", "python", "prev_hash", "chain_hash"):
        assert k in r
