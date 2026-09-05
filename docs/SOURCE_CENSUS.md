# Exact-revision source census

This is the file-coverage layer of the existing CI witness, not another product, publisher, or deployment controller. It complements `.github/tools/production/szl_production_auditor_v5.py` by recording which exact public Git blobs were actually read before a production audit makes claims.

## Run

```sh
python -S -m unittest discover -s tests -p test_source_census.py -v
python -S tools/source_census.py --org szl-holdings --output audit-out --workers 3 --max-repos 200
```

`GH_TOKEN` is optional for public API rate limits. In the hosted workflow it is the ordinary repository-scoped, contents-read-only GitHub token, not an organization secret. Tokens are sent only to `api.github.com`; redirects are rejected. Source archives are downloaded without credentials from `codeload.github.com`. The workflow performs no remote mutation, production restart, model execution, training, or package installation from audited repositories.

## What is measured

- Public repository pagination, including archived repositories, with a second inventory observation after collection.
- Exact default-branch commit for each repository; Git tree enumeration with an explicit fallback if the recursive tree is truncated.
- Every tracked regular blob present in the archive is read and verified against its Git blob SHA; a SHA-256 content commitment is retained too.
- Small UTF-8 files are statically inspected. Python AST parsing does not execute modules. A parse error is a review candidate for the auditor's Python version, not a confirmed production defect.
- Technology mentions are separated into source, tests, workflows, build files, documentation, and data. A mention is not evidence of integration.
- A second branch-tip observation records movement during the scan. `COMPLETE_AT_REVISION` describes the pinned snapshot, not an estate-wide simultaneous snapshot.

## Explicit exclusions

Private repositories are not collected or republished by this public workflow. They require a separate authorized review in a private context. Gitlinks are recorded as `SUBMODULE_NOT_INITIALIZED`; their external contents are not read. LFS pointer bytes are verified, but weight/object contents are not downloaded. Symlinks are verified as links and never followed. Binary and large files receive hash-only coverage, not semantic review. No branch history, unmerged branch, model quality, penetration test, license determination, or production-readiness certificate is implied.

Untracked archive files, omitted tracked blobs, unsupported archive types, hash mismatches, unsafe paths, incomplete pagination, network errors, and resource-budget exhaustion remain explicit coverage failures. The command exits 2 for incomplete collection and still retains available evidence. No failure is converted into an empty success.

## Budgets and evidence

At most four workers (three by default), 200 repositories by default, 20 inventory pages, 192 MiB compressed and 1 GiB expanded per repository, 150,000 archive entries, and 2 MiB per text-analysis input. Every network request has a timeout and bounded retry count. Hosted execution is bounded to 20 minutes.

The output is `census.json`, `SUMMARY.md`, and a file-level JSON manifest per repository. `--retain-review-archives` additionally retains the exact already-public archives for the explicitly named core repositories for human review; no archive is extracted by the collector. Hosted artifacts expire after 14 days. There is no background schedule and no auto-merge authority in this workflow.

Passing the source census means the declared public snapshot coverage is complete. It does not mean the software is correct, all tests passed, all private files were read, every model is trained, or the estate is operational.
