"""Offline regression tests; the collector never executes inspected source."""
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('source_census', Path(__file__).resolve().parents[1] / 'tools/source_census.py')
census = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(census)


def blob(body):
    return hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()


def entry(path='app.py', body=b'print("hello")\n', mode='100644'):
    return {'path': path, 'sha': blob(body), 'size': len(body), 'mode': mode, 'type': 'blob'}


class SourceCensusTests(unittest.TestCase):
    def archive(self, members, expected):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.tar.gz'
            with tarfile.open(path, 'w:gz') as tar:
                for name, body, mode in members:
                    member = tarfile.TarInfo(name)
                    if mode == 'symlink':
                        member.type = tarfile.SYMTYPE
                        member.linkname = body.decode()
                        tar.addfile(member)
                    else:
                        member.size = len(body)
                        tar.addfile(member, io.BytesIO(body))
            return census.scan_archive(path, expected)

    def test_exact_blob_and_sha256_are_measured(self):
        data = b'print("hello")\n'
        rows, errors = self.archive([('root/app.py', data, 'file')], [entry(body=data)])
        self.assertEqual(errors, [])
        self.assertTrue(rows[0]['content_verified'])
        self.assertEqual(rows[0]['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(rows[0]['python_parse'], 'PASS_ON_AUDITOR_PYTHON')

    def test_payload_is_parsed_but_never_executed(self):
        data = b'raise RuntimeError("must not execute")\n'
        rows, errors = self.archive([('root/app.py', data, 'file')], [entry(body=data)])
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]['python_parse'], 'PASS_ON_AUDITOR_PYTHON')

    def test_modified_blob_is_not_covered(self):
        rows, errors = self.archive([('root/app.py', b'wrong', 'file')], [entry()])
        self.assertFalse(rows[0]['content_verified'])
        self.assertTrue(any('differs' in e for e in errors))

    def test_missing_tracked_file_is_not_silently_ignored(self):
        _, errors = self.archive([], [entry()])
        self.assertIn('tracked blob absent from archive: app.py', errors)

    def test_untracked_extra_entry_is_a_coverage_error(self):
        _, errors = self.archive([('root/extra.txt', b'x', 'file')], [])
        self.assertIn('archive path not in Git tree: extra.txt', errors)

    def test_duplicate_archive_members_fail(self):
        with self.assertRaises(census.CensusError):
            self.archive([('root/app.py', b'x', 'file')] * 2, [entry(body=b'x')])

    def test_archive_path_traversal_and_absolute_names_fail(self):
        for name in ('root/../x', '/root/x', 'root/a\\b', 'root//x'):
            with self.subTest(name=name), self.assertRaises(census.CensusError):
                self.archive([(name, b'x', 'file')], [])

    def test_multiple_roots_fail(self):
        with self.assertRaises(census.CensusError):
            self.archive([('root/app.py', b'x', 'file'), ('other/b.py', b'y', 'file')], [entry(body=b'x')])

    def test_symlink_is_verified_without_following_target(self):
        target = b'../../etc/passwd'
        rows, errors = self.archive([('root/app.py', target, 'symlink')], [entry(body=target, mode='120000')])
        self.assertEqual(errors, [])
        self.assertTrue(rows[0]['content_verified'])
        self.assertEqual(rows[0]['analysis'], 'SYMLINK_NOT_FOLLOWED')

    def test_wrong_symlink_target_fails_coverage(self):
        _, errors = self.archive([('root/app.py', b'other', 'symlink')], [entry(body=b'expected', mode='120000')])
        self.assertIn('unverified blob: app.py', errors)

    def test_submodule_is_explicitly_not_initialized(self):
        rows, errors = self.archive([], [{'path': 'vendor', 'type': 'commit', 'mode': '160000', 'sha': 'a' * 40}])
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]['analysis'], 'SUBMODULE_NOT_INITIALIZED')
        self.assertFalse(rows[0]['content_verified'])

    def test_lfs_pointer_is_not_reported_as_weight_content(self):
        result = census.inspect_text('model.bin', b'version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 10\n')
        self.assertEqual(result['analysis'], 'LFS_POINTER_ONLY')

    def test_binary_and_large_files_have_explicit_boundaries(self):
        self.assertEqual(census.inspect_text('x.bin', b'\xff')['analysis'], 'BINARY_HASH_ONLY')
        data = b'x' * 30
        with patch.object(census, 'MAX_TEXT', 10):
            rows, errors = self.archive([('root/app.py', data, 'file')], [entry(body=data)])
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]['analysis'], 'LARGE_FILE_HASH_ONLY')

    def test_expanded_byte_budget_is_enforced(self):
        with patch.object(census, 'MAX_EXPANDED', 1), self.assertRaises(census.CensusError):
            self.archive([('root/app.py', b'xx', 'file')], [entry(body=b'xx')])

    def test_syntax_candidate_is_not_a_security_or_runtime_verdict(self):
        result = census.inspect_text('x.py', b'def broken(:\n')
        self.assertEqual(result['python_parse'], 'REVIEW_REQUIRED_NOT_RUNTIME_PROOF')
        self.assertNotIn('source', result)

    def test_action_refs_and_technology_mentions_are_scope_separated(self):
        result = census.inspect_text('.github/workflows/test.yml', b'jobs:\n  x:\n    steps:\n      - uses: actions/checkout@v4\n      - uses: ./local\n      - uses: a/b@' + b'a'*40 + b'\n')
        self.assertEqual(result['action_refs_not_commit_pinned'], ['actions/checkout@v4'])
        self.assertEqual(census.category('docs/vllm.md'), 'documentation')
        self.assertEqual(census.category('tests/test_vllm.py'), 'test')
        self.assertEqual(census.category('src/serve.py'), 'source')

    def test_credential_cannot_reach_archive_or_other_host(self):
        for url in ('https://codeload.github.com/x/y/tar.gz/main', 'https://evil.invalid'):
            with self.subTest(url=url), self.assertRaises(census.CensusError):
                census.fetch(url, token='not-a-real-token')

    def test_redirects_are_rejected_instead_of_forwarding_auth(self):
        with self.assertRaises(census.CensusError):
            census.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid')

    def test_inventory_paginates_and_keeps_archived_public_repos(self):
        row = {'id': 1, 'name': 'r', 'full_name': 'szl-holdings/r', 'private': False, 'archived': True, 'owner': {'login': 'szl-holdings'}}
        first = [{**row, 'id': n, 'name': f'r{n}', 'full_name': f'szl-holdings/r{n}'} for n in range(100)]
        with patch.object(census, 'api', side_effect=[first, [{**row, 'id': 101}]]):
            result = census.inventory('szl-holdings', None)
        self.assertEqual(len(result), 101)
        self.assertTrue(all(r['archived'] for r in result))

    def test_private_and_wrong_owner_inventory_is_refused(self):
        base = {'id': 1, 'name': 'r', 'full_name': 'szl-holdings/r', 'private': False, 'owner': {'login': 'szl-holdings'}}
        for row in ({**base, 'private': True}, {**base, 'owner': {'login': 'other'}}):
            with patch.object(census, 'api', return_value=[row]), self.assertRaises(census.CensusError):
                census.inventory('szl-holdings', None)

    def test_truncated_recursive_tree_falls_back_to_complete_walk(self):
        leaf = entry('x.py')
        responses = [{'truncated': True, 'tree': []}, {'truncated': False, 'tree': [{'path': 'src', 'type': 'tree', 'mode': '040000', 'sha': 'b'*40}]}, {'truncated': False, 'tree': [leaf]}]
        with patch.object(census, 'api', side_effect=responses):
            entries = census.tree_entries('szl-holdings/r', 'a'*40, None)
        self.assertEqual([e['path'] for e in entries], ['src', 'src/x.py'])

    def test_incomplete_individual_tree_never_becomes_complete(self):
        with patch.object(census, 'api', return_value={'truncated': True}), self.assertRaises(census.CensusError):
            census.tree_entries('szl-holdings/r', 'a'*40, None)

    def test_sha_and_paths_are_strict(self):
        for value in ('main', 'a'*39, 'A'*40, None):
            with self.subTest(value=value), self.assertRaises(census.CensusError):
                census.sha40(value)
        for value in ('../x', '/x', 'a/./x', 'a\n/x', ''):
            with self.subTest(value=value), self.assertRaises(census.CensusError):
                census.safe_path(value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
