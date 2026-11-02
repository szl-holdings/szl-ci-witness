"""A skipped test can also have a teardown error in real pytest JUnit XML."""
import pytest

from szl_ci_witness.witness import junit_counts, load_chain, main, verify_witness_chain


@pytest.mark.parametrize("failure", ["error", "failure"])
@pytest.mark.parametrize("skip_first", [True, False])
def test_overlapping_nonpassing_outcomes_do_not_subtract_passes(tmp_path, failure, skip_first):
    tags = ["<skipped/>", f"<{failure}/>"]
    if not skip_first:
        tags.reverse()
    report = tmp_path / "results.xml"
    report.write_text("<testsuite><testcase>" + "".join(tags) + "</testcase></testsuite>")
    assert junit_counts(str(report)) == {
        "state": "MEASURED", "passed": 0, "failed": 1, "skipped": 1,
    }


def test_overlapping_outcomes_do_not_hide_a_separate_passing_case(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text("<testsuites><testsuite>"
                      "<testcase/><testcase><skipped/><error/></testcase>"
                      "</testsuite></testsuites>")
    assert junit_counts(str(report)) == {
        "state": "MEASURED", "passed": 1, "failed": 1, "skipped": 1,
    }


def test_record_junit_preserves_error_and_skip_without_negative_passes(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text("<testsuite><testcase><skipped/><error/></testcase></testsuite>")
    chain = tmp_path / "chain.jsonl"
    args = ["record-junit", "--chain", str(chain), "--junit", str(report),
            "--repo", "example/repo", "--commit", "a" * 40, "--run-id", "123"]
    with pytest.raises(ValueError, match="success requires"):
        main(args + ["--conclusion", "success"])
    assert not chain.exists()
    assert main(args + ["--conclusion", "failure"]) == 0
    records = load_chain(str(chain))
    assert records[0]["tests"] == {"passed": 0, "failed": 1}
    assert records[0]["evidence"]["skipped"] == 1
    assert verify_witness_chain(records)[0]
