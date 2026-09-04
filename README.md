# szl-ci-witness

CI as witness. Every workflow run appends one hash-chained receipt to
`ci-witness.jsonl`: repo, commit, run id, conclusion, test counts, python
version, timestamp — chained to the previous run. Anyone can recompute the
chain offline. Regressions and fixes are detected from the linkage, not from
dashboards.

## Why

"CI is green" is a screenshot claim today. A witness chain is append-only:
rewrite history and the linkage breaks — INVALID, loudly. Red runs stay in
the chain; the estate's own doctrine says failure is evidence too.

## Usage in any repo

```yaml
- run: pip install -e . pytest && python -m pytest tests/ -q
- name: Witness this run
  if: always()
  run: |
    python -m szl_ci_witness.witness record --conclusion ${{ job.status }} --passed $P --failed $F
    python -m szl_ci_witness.witness verify
```

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
Suite: 5/5 green on 3.11/3.12.

## Doctrine

- Append-only. A rewritten history is an invalid history.
- Failures are recorded, never hidden.
- Timestamps are reported, never backfilled.
- Python 3.11+, standard library only.

## License

Apache-2.0 — canonical org text (see LICENSE pointer).
