# szl-ci-witness

CI as witness. Each witnessed non-PR workflow run appends one hash-chained receipt to
`ci-witness.jsonl`: repo, commit, run id, conclusion, test counts, python
version, timestamp — chained to the previous run. Anyone can recompute the
chain offline. Regressions and fixes are detected from the linkage, not from
dashboards.

## Why

The next run restores the preceding Actions artifact, verifies its hashes
and terminal run provenance, and appends measured JUnit test outcomes.
Changing existing content without rehashing fails verification. Failures
are recorded as well as successes.

## Usage in any repo

Use the SHA-pinned, complete pattern in `.github/workflows/tests.yml`.
It grants only `contents: read` and `actions: read`, serializes runs per
workflow/ref, separates Python-version streams, and retains artifacts for
90 days. `queue: max` retains up to 100 pending runs rather than replacing
the single pending run under the default concurrency behavior. Consumer
repositories must pin this package to an exact reviewed
commit. Pull requests and tag dispatches run tests but do not publish
trusted branch history.

For each non-PR run:

1. Set `WITNESS_STREAM` to `workflow-name:refs/heads/branch:python-X.Y`.
2. Restore using `python -m szl_ci_witness.github_artifacts` with the scoped
   Actions token in `GH_TOKEN`; retain the step's `artifact_name` output.
3. Run pytest with `--junitxml=test-results.xml`.
4. Use `python -m szl_ci_witness.witness record-junit --conclusion` with the
   test step's actual outcome, including when tests fail.
5. Verify and upload `ci-witness.jsonl` plus `test-results.xml` using the
   restore step's artifact name, even after test failures.

The recorded scope is **test-step outcome**, not an assertion that every
workflow job succeeded. Never substitute guessed counts or a dashboard
color for the JUnit report. The legacy `record` command accepts caller
counts; `record-junit` is the measured CI path.

`record` reads `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_RUN_ID` from the
Actions environment by default. The chain file commits or uploads as an
artifact — either way it is verifiable:

```bash
python -m szl_ci_witness.witness verify    # linkage recompute
python -m szl_ci_witness.witness summary   # runs, green/red, regressions, fixes
```

## Verified behavior (pre-push, 2026-09-04)

A four-run synthetic chain (green, green, red, green) yields exactly
`regressions: [102]` and `fixes: [103]`; flipping one recorded conclusion
without re-hashing invalidates the whole chain; an empty chain fails closed.
Persistence tests restore ZIP fixtures, preserve predecessor bytes, and
reject mismatched repositories, branches, workflows, terminal source
commits, expired predecessors, and API failures. Missing test reports cannot
produce successful measured counts.

## Trust boundary

GitHub artifact storage is retention-bounded, not a permanent independent
witness. When the latest matching artifact is expired or the API fails,
restoration fails closed. An observed empty artifact inventory is labeled
`GENESIS_OBSERVED_NO_ARTIFACTS`; it cannot prove that no artifacts were
previously deleted. A fully rewritten and rehashed chain cannot be detected
from that chain alone. Archive a trusted terminal hash and the full chain
outside this repository for independent continuity assurance. Receipts are
explicitly `UNSIGNED_HONEST`; GitHub origin checks do not turn them into
cryptographic signatures or proof of production runtime health.

The chain covers runs which reached the witness step. Manual cancellation,
setup failures, and queue overflow beyond GitHub's 100-pending-run limit may
leave runs without receipts. Do not use chain length as proof that every
trigger or source revision was witnessed. See the official
[GitHub concurrency contract](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).

## Doctrine

- Verify predecessors before appending; retain the preceding bytes.
- Failures are recorded, never hidden.
- Timestamps are reported, never backfilled.
- Python 3.11+, standard library only.

## License

Apache-2.0 — canonical org text (see LICENSE pointer).
