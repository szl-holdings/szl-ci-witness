import copy
import io
import json
import zipfile

import pytest

from szl_ci_witness.github_artifacts import artifact_prefix, restore, select_predecessor, validate_run_origin
from szl_ci_witness.witness import (
    append_record, junit_counts, load_chain, main, verify_witness_chain, witness_record,
)


STREAM = "tests:refs/heads/main:python-3.12"
REPO = "szl-holdings/szl-ci-witness"


def run_metadata(run="124"):
    return {"id": int(run), "run_attempt": 1, "repository": {"full_name": REPO},
            "head_repository": {"full_name": REPO}, "event": "push", "name": "tests",
            "head_branch": "main", "head_sha": "a" * 40, "workflow_id": 42}


def api_fixture(artifact_payload, archive=b"", origin_mutation=None):
    def fetch(path, token):
        if path.endswith("/zip"):
            return archive
        if "/actions/runs/" in path:
            run = path.split("/actions/runs/")[1].split("/")[0]
            metadata = run_metadata(run)
            if run == "123" and origin_mutation:
                metadata.update(origin_mutation)
            return json.dumps(metadata).encode()
        return json.dumps({"artifacts": artifact_payload}).encode()
    return fetch


def receipt(run="123", prev="0" * 64):
    return witness_record(REPO, "a" * 40, run, "success", 3, 0, "3.12", prev,
                          "2026-09-05T00:00:00Z", {"stream": STREAM, "run_attempt": "1"})


def test_append_rejects_tampered_history_and_duplicate_run(tmp_path):
    path = str(tmp_path / "chain.jsonl")
    append_record(path, receipt())
    with pytest.raises(ValueError, match="duplicate"):
        append_record(path, receipt(prev=receipt()["chain_hash"]))
    damaged = receipt()
    damaged["tests"]["passed"] = 99
    (tmp_path / "chain.jsonl").write_text(json.dumps(damaged) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tampered"):
        append_record(path, receipt("124", damaged["chain_hash"]))


def test_junit_counts_failures_errors_and_skips(tmp_path):
    path = tmp_path / "report.xml"
    path.write_text("<testsuites><testsuite>"
                    "<testcase/><testcase><failure/></testcase>"
                    "<testcase><error/></testcase><testcase><skipped/></testcase>"
                    "</testsuite></testsuites>", encoding="utf-8")
    assert junit_counts(str(path)) == {"state": "MEASURED", "passed": 1, "failed": 2, "skipped": 1}
    assert junit_counts(str(tmp_path / "missing.xml"))["passed"] is None


def test_missing_junit_cannot_claim_success_but_failure_is_recorded(tmp_path):
    path = str(tmp_path / "chain.jsonl")
    args = ["record-junit", "--chain", path, "--repo", REPO, "--commit", "a" * 40,
            "--run-id", "123", "--stream", STREAM, "--junit", str(tmp_path / "missing.xml")]
    with pytest.raises(ValueError, match="success requires"):
        main(args + ["--conclusion", "success"])
    assert main(args + ["--conclusion", "failure"]) == 0
    records = load_chain(path)
    assert records[0]["tests"] == {"passed": None, "failed": None}
    assert records[0]["evidence"]["test_counts_state"] == "UNAVAILABLE"
    assert verify_witness_chain(records)[0]


def test_restore_then_append_preserves_prior_bytes(tmp_path):
    original = (json.dumps(receipt(), sort_keys=True) + "\n").encode()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("ci-witness.jsonl", original)
    artifact = {"id": 7, "name": artifact_prefix(STREAM) + "123-1", "expired": False,
                "workflow_run": {"id": 123}}
    fetch = api_fixture([artifact], archive.getvalue())

    destination = str(tmp_path / "ci-witness.jsonl")
    restored = restore(REPO, STREAM, "124", "1", "TEST_ONLY", destination, fetch)
    assert restored["state"] == "RESTORED"
    assert restored["predecessor_artifact_id"] == 7
    append_record(destination, receipt("124", receipt()["chain_hash"]))
    assert (tmp_path / "ci-witness.jsonl").read_bytes().startswith(original)
    assert len(load_chain(destination)) == 2
    assert verify_witness_chain(load_chain(destination))[0]


def test_latest_expired_artifact_does_not_fall_back():
    prefix = artifact_prefix(STREAM)
    artifacts = [{"id": 1, "name": prefix + "123-1", "expired": False},
                 {"id": 2, "name": prefix + "124-1", "expired": True}]
    with pytest.raises(ValueError, match="expired"):
        select_predecessor(artifacts, prefix, "125", "1")


def test_already_persisted_attempt_is_not_overwritten():
    prefix = artifact_prefix(STREAM)
    with pytest.raises(ValueError, match="already"):
        select_predecessor([{"id": 1, "name": prefix + "123-1"}], prefix, "123", "1")


@pytest.mark.parametrize("mutation", ["tamper", "wrong_repo", "wrong_stream"])
def test_restore_refuses_bad_predecessor(tmp_path, mutation):
    rec = receipt()
    if mutation == "tamper":
        rec["conclusion"] = "failure"
    else:
        evidence = copy.deepcopy(rec["evidence"])
        if mutation == "wrong_stream":
            evidence["stream"] = "other"
        rec = witness_record("other/repo" if mutation == "wrong_repo" else REPO,
                             "a" * 40, "123", "success", 3, 0, "3.12", evidence=evidence)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("ci-witness.jsonl", json.dumps(rec) + "\n")
    fetch = api_fixture([{"id": 1, "name": artifact_prefix(STREAM) + "123-1",
                          "workflow_run": {"id": 123}}], archive.getvalue())
    with pytest.raises(ValueError):
        restore(REPO, STREAM, "124", "1", "TEST_ONLY", str(tmp_path / "chain.jsonl"), fetch)


def test_discovery_error_is_not_genesis(tmp_path):
    def unavailable(path, token):
        raise OSError("API unavailable")
    with pytest.raises(OSError):
        restore(REPO, STREAM, "124", "1", "TEST_ONLY", str(tmp_path / "chain.jsonl"), unavailable)


def test_empty_observed_artifact_inventory_starts_explicit_genesis(tmp_path):
    result = restore(REPO, STREAM, "123", "1", "TEST_ONLY", str(tmp_path / "chain.jsonl"),
                     api_fixture([]))
    assert result["state"] == "GENESIS_OBSERVED_NO_ARTIFACTS"
    assert result["predecessor_artifact_id"] is None


@pytest.mark.parametrize("mutation", [{"event": "pull_request"}, {"head_branch": "other"},
    {"workflow_id": 43}, {"head_repository": {"full_name": "other/repo"}}])
def test_artifact_cannot_claim_another_workflow_origin(tmp_path, mutation):
    artifact = {"id": 7, "name": artifact_prefix(STREAM) + "123-1",
                "workflow_run": {"id": 123}}
    with pytest.raises(ValueError):
        restore(REPO, STREAM, "124", "1", "TEST_ONLY", str(tmp_path / "chain.jsonl"),
                api_fixture([artifact], origin_mutation=mutation))
    assert not (tmp_path / "chain.jsonl").exists()


def test_pull_request_cannot_bootstrap_a_main_stream():
    metadata = run_metadata()
    metadata["event"] = "pull_request"
    with pytest.raises(ValueError):
        validate_run_origin(metadata, REPO, STREAM)
