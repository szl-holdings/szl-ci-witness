"""Restore a verifiable Actions artifact predecessor using read-only API access."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request
import zipfile

from .witness import verify_witness_chain


def artifact_prefix(stream: str) -> str:
    if not stream:
        raise ValueError("WITNESS_STREAM is required")
    return "ci-witness-" + hashlib.sha256(stream.encode()).hexdigest()[:24] + "-"


def select_predecessor(artifacts: list[dict], prefix: str, run_id: str, attempt: str):
    """Choose the newest matching artifact; expired/corrupt evidence cannot reset history."""
    name = prefix + run_id + "-" + attempt
    if any(a["name"] == name for a in artifacts):
        raise ValueError("this run attempt already has a persisted witness")
    candidates = [a for a in artifacts if a["name"].startswith(prefix)]
    if not candidates:
        return None
    prior = max(candidates, key=lambda a: a["id"])
    if prior.get("expired"):
        raise ValueError("latest predecessor artifact expired; restore a trusted archive before continuing")
    return prior


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            redirected.remove_header("Authorization")
        return redirected


def api_get(path: str, token: str) -> bytes:
    req = urllib.request.Request("https://api.github.com/" + path, headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer " + token,
    })
    with urllib.request.build_opener(SafeRedirect()).open(req, timeout=60) as response:
        return response.read()


def validate_run_origin(run: dict, repo: str, stream: str) -> None:
    """Bind a named stream to GitHub's run metadata, not artifact-controlled text."""
    if (run.get("repository", {}).get("full_name") != repo
            or run.get("head_repository", {}).get("full_name") != repo
            or run.get("event") not in {"push", "workflow_dispatch", "schedule"}
            or not run.get("head_branch")
            or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))):
        raise ValueError("unsupported or mismatched workflow run origin")
    expected = f"{run['name']}:refs/heads/{run['head_branch']}:python-"
    if not stream.startswith(expected) or not re.fullmatch(r"\d+\.\d+", stream[len(expected):]):
        raise ValueError("workflow run does not own this witness stream")


def restore(repo: str, stream: str, run_id: str, attempt: str, token: str,
            destination: str = "ci-witness.jsonl", fetch=api_get) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    if not run_id.isdecimal() or not attempt.isdecimal():
        raise ValueError("run ID and attempt must be numeric")
    if Path(destination).exists():
        raise ValueError("refusing to overwrite an existing witness chain")
    prefix = artifact_prefix(stream)
    current = json.loads(fetch(f"repos/{repo}/actions/runs/{run_id}", token))
    validate_run_origin(current, repo, stream)
    if str(current["id"]) != run_id or str(current["run_attempt"]) != attempt:
        raise ValueError("current run identity mismatch")
    artifacts = []
    for page in range(1, 101):
        data = json.loads(fetch(f"repos/{repo}/actions/artifacts?per_page=100&page={page}", token))
        artifacts.extend(data["artifacts"])
        if len(data["artifacts"]) < 100:
            break
    else:
        raise ValueError("artifact history exceeds discovery bound; no reset allowed")
    prior = select_predecessor(artifacts, prefix, run_id, attempt)
    result = {"artifact_name": prefix + run_id + "-" + attempt,
              "predecessor_artifact_id": prior["id"] if prior else None,
              "state": "RESTORED" if prior else "GENESIS_OBSERVED_NO_ARTIFACTS"}
    if prior:
        prior_id = prior.get("workflow_run", {}).get("id")
        if type(prior_id) is not int:
            raise ValueError("predecessor has no GitHub workflow run provenance")
        identity = re.fullmatch(re.escape(prefix) + r"(\d+)-(\d+)", prior["name"])
        if not identity or identity[1] != str(prior_id):
            raise ValueError("artifact name does not match its originating run")
        origin = json.loads(fetch(f"repos/{repo}/actions/runs/{prior_id}/attempts/{identity[2]}", token))
        validate_run_origin(origin, repo, stream)
        if origin.get("workflow_id") != current.get("workflow_id"):
            raise ValueError("predecessor belongs to a different workflow")
        if prior["name"] != prefix + str(origin["id"]) + "-" + str(origin["run_attempt"]):
            raise ValueError("artifact name does not match its originating run attempt")
        archive = fetch(f"repos/{repo}/actions/artifacts/{prior['id']}/zip", token)
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            members = [i for i in z.infolist() if i.filename == "ci-witness.jsonl"]
            if len(members) != 1 or members[0].file_size > 50_000_000:
                raise ValueError("invalid predecessor archive")
            content = z.read(members[0])
        records = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
        ok, detail = verify_witness_chain(records)
        if not ok:
            raise ValueError(detail)
        if any(r["repo"] != repo or r.get("evidence", {}).get("stream") != stream for r in records):
            raise ValueError("predecessor repository or stream mismatch")
        terminal = records[-1]
        if (terminal["run_id"] != str(origin["id"])
                or terminal["commit"] != origin["head_sha"]
                or str(terminal["evidence"].get("run_attempt")) != str(origin["run_attempt"])):
            raise ValueError("terminal receipt does not match its originating run")
        with open(destination, "xb") as target:
            target.write(content)
        result.update(records=len(records), terminal=records[-1]["chain_hash"])
    return result


def main() -> None:
    result = restore(os.environ["GITHUB_REPOSITORY"], os.environ["WITNESS_STREAM"],
                     os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"],
                     os.environ["GH_TOKEN"])
    print(json.dumps(result, sort_keys=True))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("artifact_name=" + result["artifact_name"] + "\n")


if __name__ == "__main__":
    main()
