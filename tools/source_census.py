#!/usr/bin/env python3
"""Read-only, exact-revision public source census. Static coverage is not readiness.

Never imports audited code, installs its dependencies, follows repository links,
retrieves LFS objects, initializes submodules, or changes a remote resource.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_JSON = 32 * 1024 * 1024
MAX_ARCHIVE = 192 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_TEXT = 2 * 1024 * 1024
TEXT_EXT = {'.py', '.pyi', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.jsx', '.json', '.toml', '.yaml', '.yml', '.md', '.txt', '.html', '.css', '.sh', '.sql', '.proto', '.lean', '.rs', '.go', '.cu', '.cpp', '.h'}
TECH = {
    'otel': r'opentelemetry|OTEL_EXPORTER',
    'temporal': r'temporalio|@temporalio',
    'vllm': r'\bvllm\b', 'sglang': r'\bsglang\b',
    'llama_cpp': r'llama_cpp|llama-cpp|llama\.cpp',
    'qdrant': r'qdrant', 'pgvector': r'pgvector',
    'docling': r'docling', 'reranker': r'rerank|CrossEncoder',
    'trl': r'\btrl\b|SFTTrainer|DPOTrainer',
    'peft': r'\bpeft\b|LoraConfig', 'mlflow': r'mlflow',
    'webgpu': r'WebGPU|navigator\.gpu|huggingface/kernels',
    'playwright': r'playwright', 'hypothesis': r'hypothesis',
    'openfga': r'openfga', 'postgres': r'psycopg|asyncpg|postgres',
    'otel_collector': r'otelcol|opentelemetry-collector',
    'sbom': r'cyclonedx|syft|spdx|sbom',
}
TECH = {name: re.compile(pattern, re.I) for name, pattern in TECH.items()}
RETAIN = {'a11oy', '.github', 'killinchu', 'szl-forge', 'szl-nemo', 'szl-second-brain', 'szl-serve', 'szl-kernels', 'szl-engine-bench', 'lyte-services', 'vertical-services', 'szl-frontier', 'szl-constellation', 'hatun-mcp', 'szl-ci-witness'}


class CensusError(RuntimeError):
    pass


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha40(value: Any) -> str:
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise CensusError('expected exact lowercase Git SHA')
    return value


def safe_path(value: str) -> str:
    p = PurePosixPath(value)
    if not value or p.is_absolute() or any(x in ('..', '.', '') for x in value.split('/')) or '\\' in value or any(ord(c) < 32 for c in value):
        raise CensusError('unsafe repository path')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CensusError('unexpected redirect; credentials were not forwarded')


def fetch(url: str, *, token: str | None = None, cap: int = MAX_JSON) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'api.github.com', 'codeload.github.com'} or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise CensusError('network destination is not allowlisted')
    headers = {'User-Agent': 'SZL-Source-Census/1', 'Accept': 'application/vnd.github+json'}
    if token:
        if parsed.hostname != 'api.github.com':
            raise CensusError('API credential cannot be sent to archive host')
        headers['Authorization'] = 'Bearer ' + token
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=40) as response:
                body = response.read(cap + 1)
            if len(body) > cap:
                raise CensusError('response byte budget exceeded')
            return body
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise CensusError(f'HTTP_{exc.code}') from None
            delay = exc.headers.get('Retry-After', '')
            if delay.isdigit() and int(delay) > 30:
                raise CensusError('retry delay exceeds budget') from None
            time.sleep(int(delay) if delay.isdigit() else 2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise CensusError('transport unavailable') from None
            time.sleep(2 ** attempt)
    raise CensusError('fetch attempts exhausted')


def api(path: str, token: str | None) -> Any:
    return json.loads(fetch('https://api.github.com' + path, token=token))


def inventory(org: str, token: str | None) -> list[dict]:
    if not NAME.fullmatch(org):
        raise CensusError('invalid organization')
    result = {}
    for page in range(1, 21):
        rows = api(f'/orgs/{org}/repos?type=public&sort=full_name&direction=asc&per_page=100&page={page}', token)
        if not isinstance(rows, list):
            raise CensusError('inventory is not an array')
        for repo in rows:
            if repo.get('private') is not False:
                raise CensusError('private repository refused by public collector')
            if repo.get('owner', {}).get('login', '').lower() != org.lower():
                raise CensusError('repository owner mismatch')
            if repo['id'] in result:
                raise CensusError('inventory changed during pagination; retry snapshot')
            if not NAME.fullmatch(repo['name']):
                raise CensusError('invalid repository name')
            result[repo['id']] = repo
        if len(rows) < 100:
            return sorted(result.values(), key=lambda r: r['full_name'].lower())
    raise CensusError('inventory pagination limit reached')


def tree_entries(repo: str, revision: str, token: str | None) -> list[dict]:
    data = api(f'/repos/{repo}/git/trees/{sha40(revision)}?recursive=1', token)
    if data.get('truncated') is False:
        entries = data['tree']
    else:
        entries, queue, expanded = [], [('', revision)], 0
        while queue:
            prefix, tree_sha = queue.pop()
            expanded += 1
            if expanded > 5000:
                raise CensusError('tree traversal budget exceeded')
            branch = api(f'/repos/{repo}/git/trees/{sha40(tree_sha)}', token)
            if branch.get('truncated') is not False:
                raise CensusError('individual tree is incomplete')
            for node in branch['tree']:
                item = dict(node, path=prefix + node['path'])
                entries.append(item)
                if node['type'] == 'tree':
                    queue.append((item['path'] + '/', node['sha']))
    seen = set()
    for entry in entries:
        path = safe_path(entry['path'])
        if path in seen:
            raise CensusError('duplicate tree entry')
        seen.add(path)
        sha40(entry['sha'])
    return entries


def category(path: str) -> str:
    p = PurePosixPath(path)
    if path.startswith('.github/workflows/'):
        return 'workflow'
    if any(part.lower() in {'tests', 'test', '__tests__', 'fixtures'} for part in p.parts) or p.name.startswith('test_') or '.test.' in p.name or '.spec.' in p.name:
        return 'test'
    if p.suffix.lower() in {'.md', '.rst'} or p.name.lower().startswith(('license', 'notice')):
        return 'documentation'
    if p.name in {'package.json', 'pyproject.toml', 'uv.lock', 'pnpm-lock.yaml', 'Cargo.toml'} or p.name.startswith(('requirements', 'Dockerfile')):
        return 'build'
    if p.suffix in {'.py', '.pyi', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.jsx', '.go', '.rs', '.cpp', '.cu', '.lean'}:
        return 'source'
    return 'data_or_asset'


def inspect_text(path: str, body: bytes) -> dict:
    if body.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
        return {'analysis': 'LFS_POINTER_ONLY'}
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        return {'analysis': 'BINARY_HASH_ONLY'}
    if '\x00' in text:
        return {'analysis': 'BINARY_HASH_ONLY'}
    result: dict[str, Any] = {'analysis': 'STATIC_TEXT', 'lines': len(text.splitlines()), 'technology_mentions': [k for k, pattern in TECH.items() if pattern.search(text)]}
    result['engineering_markers'] = len(re.findall(r'\b(?:TODO|FIXME|HACK|NotImplementedError)\b', text))
    if path.endswith('.py'):
        try:
            tree = ast.parse(text, filename=path)
            result['python_parse'] = 'PASS_ON_AUDITOR_PYTHON'
            result['python_imports'] = sorted({node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names})
        except (SyntaxError, ValueError, RecursionError) as exc:
            result['python_parse'] = 'REVIEW_REQUIRED_NOT_RUNTIME_PROOF'
            result['parse_line'] = getattr(exc, 'lineno', None)
    if category(path) == 'workflow':
        refs = re.findall(r'^\s*(?:-\s*)?uses:\s*[\"\']?([^\s\"\'#]+)', text, re.M)
        result['external_action_refs'] = [r for r in refs if not r.startswith(('./', 'docker://'))]
        result['action_refs_not_commit_pinned'] = [r for r in result['external_action_refs'] if not SHA.fullmatch(r.rsplit('@', 1)[-1])]
        result['has_timeout_literal'] = 'timeout-minutes:' in text
    return result


def scan_archive(archive: Path, entries: list[dict]) -> tuple[list[dict], list[str]]:
    expected = {e['path']: e for e in entries if e['type'] != 'tree'}
    records = {path: {'path': path, 'mode': e['mode'], 'git_blob': e['sha'], 'bytes_declared': e.get('size'), 'category': category(path), 'analysis': 'METADATA_ONLY', 'content_verified': False} for path, e in expected.items()}
    errors, seen, expanded, root = [], set(), 0, None
    with tarfile.open(archive, 'r:gz') as tar:
        for n, member in enumerate(tar, start=1):
            if n > 150000:
                raise CensusError('archive entry budget exceeded')
            name = member.name.rstrip('/')
            safe_path(name)
            parts = name.split('/', 1)
            if root is None:
                root = parts[0]
            if parts[0] != root:
                raise CensusError('multiple archive roots')
            if member.isdir():
                continue
            if len(parts) != 2:
                raise CensusError('file outside archive root')
            path = safe_path(parts[1])
            if path in seen:
                raise CensusError('duplicate archive member')
            seen.add(path)
            if path not in expected:
                errors.append('archive path not in Git tree: ' + path)
                continue
            record = records[path]
            if member.issym() and expected[path]['mode'] == '120000':
                body = member.linkname.encode('utf-8')
                record.update(analysis='SYMLINK_NOT_FOLLOWED', content_verified=hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest() == expected[path]['sha'])
                continue
            if not member.isfile() or expected[path]['mode'] not in {'100644', '100755'}:
                errors.append('unsupported archive type: ' + path)
                continue
            if member.size < 0:
                raise CensusError('negative member size')
            expanded += member.size
            if expanded > MAX_EXPANDED:
                raise CensusError('expanded archive byte budget exceeded')
            content = tar.extractfile(member)
            if content is None:
                raise CensusError('missing archive content')
            h256 = hashlib.sha256()
            hgit = hashlib.sha1(b'blob ' + str(member.size).encode() + b'\0')
            small, count = bytearray(), 0
            for block in iter(lambda: content.read(1024 * 1024), b''):
                count += len(block)
                h256.update(block)
                hgit.update(block)
                if member.size <= MAX_TEXT:
                    small.extend(block)
            verified = count == member.size and hgit.hexdigest() == expected[path]['sha']
            record.update(bytes_read=count, sha256=h256.hexdigest(), content_verified=verified)
            if not verified:
                errors.append('archive content differs from Git blob: ' + path)
            record.update(inspect_text(path, bytes(small)) if member.size <= MAX_TEXT else {'analysis': 'LARGE_FILE_HASH_ONLY'})
    for path, entry in expected.items():
        if entry['type'] == 'commit':
            records[path]['analysis'] = 'SUBMODULE_NOT_INITIALIZED'
        elif path not in seen:
            errors.append('tracked blob absent from archive: ' + path)
        elif not records[path]['content_verified']:
            errors.append('unverified blob: ' + path)
    return sorted(records.values(), key=lambda r: r['path']), sorted(set(errors))


def audit_repo(repo: dict, output: Path, token: str | None, retain: bool) -> dict:
    name, full = repo['name'], repo['full_name']
    result = {'repository': full, 'archived': repo['archived'], 'observed_at': utc(), 'status': 'INCOMPLETE'}
    try:
        ref = urllib.parse.quote(repo['default_branch'], safe='')
        revision = sha40(api(f'/repos/{full}/commits/{ref}', token)['sha'])
        result['revision'] = revision
        entries = tree_entries(full, revision, token)
        result['tree_entries'] = len(entries)
        result['tracked_blobs'] = sum(e['type'] == 'blob' for e in entries)
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'source.tar.gz'
            archive.write_bytes(fetch(f'https://codeload.github.com/{full}/tar.gz/{revision}', cap=MAX_ARCHIVE))
            records, errors = scan_archive(archive, entries)
            if retain and name in RETAIN:
                dest = output / 'review-archives'
                dest.mkdir(exist_ok=True)
                shutil.copyfile(archive, dest / f'{name}-{revision}.tar.gz')
        files = output / 'files'
        files.mkdir(exist_ok=True)
        (files / f'{name}.json').write_text(json.dumps(records, sort_keys=True), encoding='utf-8')
        result.update(errors=errors, status='COMPLETE_AT_REVISION' if not errors else 'INCOMPLETE', verified_blobs=sum(r['content_verified'] for r in records), analyzed_text=sum(r['analysis'] == 'STATIC_TEXT' for r in records), categories=dict(Counter(r['category'] for r in records)), bytes_read=sum(r.get('bytes_read', 0) for r in records), coverage_classes=dict(Counter(r['analysis'] for r in records)))
        result['python_parse_candidates'] = [{'path': r['path'], 'line': r.get('parse_line')} for r in records if r.get('python_parse') == 'REVIEW_REQUIRED_NOT_RUNTIME_PROOF']
        result['unpinned_action_references'] = sum(len(r.get('action_refs_not_commit_pinned', [])) for r in records)
        result['technologies_by_category'] = {kind: dict(Counter(t for r in records if r['category'] == kind for t in r.get('technology_mentions', []))) for kind in ('source', 'test', 'workflow', 'build', 'documentation')}
        result['tip_after_scan'] = sha40(api(f'/repos/{full}/commits/{ref}', token)['sha'])
        result['tip_moved'] = result['tip_after_scan'] != revision
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        result['status'] = 'INCOMPLETE'
        if isinstance(exc, CensusError):
            result['error'] = str(exc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--org', default='szl-holdings')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=3, choices=range(1, 5))
    parser.add_argument('--max-repos', type=int, default=200)
    parser.add_argument('--retain-review-archives', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    token = os.getenv('GH_TOKEN')
    report: dict[str, Any] = {'schema': 'szl.source-census/v1', 'started_at': utc(), 'organization': args.org, 'scope': 'PUBLIC_DEFAULT_BRANCH_SNAPSHOTS_INCLUDING_ARCHIVED', 'manual_file_review': False, 'runtime_certification': False, 'private_repository_coverage': 'NOT_COLLECTED', 'repositories': []}
    try:
        repos = inventory(args.org, token)
        if not repos or len(repos) > args.max_repos:
            raise CensusError('repository count is outside explicit budget')
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(audit_repo, repo, args.output, token, args.retain_review_archives) for repo in repos]
            for future in concurrent.futures.as_completed(futures):
                row = future.result()
                report['repositories'].append(row)
                print(json.dumps({k: row[k] for k in ('repository', 'revision', 'status', 'tracked_blobs', 'verified_blobs', 'error_type') if k in row}), flush=True)
        report['repositories'].sort(key=lambda r: r['repository'].lower())
        report['inventory_unchanged_at_end'] = [r['id'] for r in repos] == [r['id'] for r in inventory(args.org, token)]
        report['complete'] = report['inventory_unchanged_at_end'] and all(r['status'] == 'COMPLETE_AT_REVISION' for r in report['repositories'])
    except Exception as exc:
        report.update(complete=False, fatal_type=type(exc).__name__)
    report['finished_at'] = utc()
    report['totals'] = {key: sum(r.get(key, 0) for r in report['repositories']) for key in ('tracked_blobs', 'verified_blobs', 'analyzed_text', 'bytes_read', 'unpinned_action_references')}
    report['repository_count'] = len(report['repositories'])
    (args.output / 'census.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    lines = ['# SZL exact-revision public source census', '', 'Automated static coverage; not manual review, a vulnerability verdict, a benchmark, or production acceptance.', '', f"Complete coverage: {report['complete']}. Repositories observed: {report['repository_count']}.", '', '| Repository | State | Verified / tracked blobs | Source files | Tests | Workflows |', '|---|---|---:|---:|---:|---:|']
    for r in report['repositories']:
        c = r.get('categories', {})
        lines.append(f"| {r['repository']} | {r['status']} | {r.get('verified_blobs', 0)}/{r.get('tracked_blobs', 0)} | {c.get('source', 0)} | {c.get('test', 0)} | {c.get('workflow', 0)} |")
    (args.output / 'SUMMARY.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return 0 if report['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
