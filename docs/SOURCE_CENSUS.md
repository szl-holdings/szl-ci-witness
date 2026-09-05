# Exact-revision source census

This is the file-coverage layer of the existing CI witness, not another product, publisher, or deployment controller. It complements `.github/tools/production/szl_production_auditor_v5.py` by recording which exact public Git blobs were actually read before a production audit makes claims.

## Run

```sh
python -S -m unittest discover -s tests -p 'test*census*.py' -v
python -S tools/source_census.py --org szl-holdings --output audit-out --workers 3 --max-repos 200 --archive-mib 384 --with-ci
```

There are 30 offline regression tests. `GH_TOKEN` is optional for public API rate limits. In the hosted workflow it is the ordinary repository-scoped, contents-read-only GitHub token, not an organization secret. Tokens are sent only to `api.github.com`; redirects are rejected. Source archives stream without credentials from `codeload.github.com`. The workflow performs no remote mutation, production restart, model execution, training, or package installation from audited repositories.

## What is measured

- Public repository pagination, including archived repositories, with a second inventory observation after collection.
- Exact default-branch commit for each repository; Git tree enumeration with an explicit fallback if the recursive tree is truncated.
- Every tracked regular blob present in the archive is read and verified against its Git blob SHA; a SHA-256 content commitment is retained too.
- Small UTF-8 files are statically inspected. Python AST parsing does not execute modules. A parse error is a review candidate for the auditor's Python version, not a confirmed production defect.
- Technology mentions are separated into source, tests, workflows, build files, documentation, and data. A mention is not evidence of integration.
- A second branch-tip observation records movement during the scan. `COMPLETE_AT_REVISION` describes the pinned snapshot, not an estate-wide simultaneous snapshot.
- Optional `--with-ci` retains the provider's paginated latest check-run observations at each exact source SHA. Repeated scheduled runs and similarly named jobs can coexist: do not collapse these into a claim about the latest logical workflow or branch protection. Required checks, reviews, legacy statuses, deployment acceptance, and merge policy are not evaluated by this collector. CI observation failures are explicit and distinct from source-byte coverage.

## Explicit exclusions

Private repositories are not collected or republished by this public workflow. They require a separate authorized review in a private context. Gitlinks are recorded as `SUBMODULE_NOT_INITIALIZED`; their external contents are not read. LFS pointer bytes are verified, but weight/object contents are not downloaded. Symlinks are verified as links and never followed. Binary and large files receive hash-only coverage, not semantic review. No branch history, unmerged branch, model quality, penetration test, license determination, or production-readiness certificate is implied.

Untracked archive files, omitted tracked blobs, unsupported archive types, hash mismatches, unsafe paths, incomplete pagination, network errors, and resource-budget exhaustion remain explicit coverage failures. The command exits 2 for incomplete collection and still retains available evidence. No failure is converted into an empty success.

## Budgets and evidence

At most four workers (three by default), 200 repositories by default, 20 inventory pages, and 192 MiB compressed per repository by default. The explicit `--archive-mib` choices are 192, 384, and 768. The hosted workflow uses 384 MiB because the measured platform snapshot contains 393,188,837 uncompressed bytes, including video and release archives. Archives stream to disk rather than being buffered in memory. A failed streaming download removes only its own partial file, never an existing destination.

The remaining limits are 1 GiB expanded per repository, 150,000 archive entries, and 2 MiB per text-analysis input. Every network operation has a timeout; API retries are bounded. Hosted execution is bounded to 20 minutes.

The output is `census.json`, `SUMMARY.md`, and a file-level JSON manifest per repository. The collector's own source hash, Python version, source revisions, observation times, and budget are retained. `--retain-review-archives` additionally retains the exact already-public archives for the explicitly named core repositories for human review; no archive is extracted by the collector. Hosted artifacts expire after 14 days. There is no background schedule and no auto-merge authority in this workflow.

## First complete observed census

Run `33994209575` completed source coverage between 2026-09-05T21:53:21Z and 21:55:55Z:

- 117 public repositories, including 34 archived repositories;
- 26,466 of 26,466 tracked blobs verified against their pinned Git objects;
- 24,855 UTF-8 files statically inspected;
- 704,743,304 regular-file bytes read;
- all 117 exact-source CI observations collected;
- one branch tip, Killinchu, advanced during the scan and is explicitly marked; its pinned snapshot remains reproducible.

These are point-in-time observations, not canonical forever-counts. Six additional private repositories were outside the public collector's scope. No claim is made that their content was read by this job.

The artifact `9977614025` has SHA-256 `3b4871d3fd6199a28c8e8c51f9dbcc0c3c90c6bc9455608d9d885e11a90e972e`. That digest was independently checked after download. Both Python 3.11 and Python 3.12 contract jobs passed, as did the existing witness tests. The earlier bounded failure remains retained as evidence rather than being mislabeled as complete.

Workflow: https://github.com/szl-holdings/szl-ci-witness/actions/runs/33994209575

Passing the source census means the declared public snapshot coverage is complete. It does not mean the software is correct, all product tests passed, all private files were read, every model is trained, or the estate is operational.
