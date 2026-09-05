import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SPEC = importlib.util.spec_from_file_location('source_census', Path(__file__).resolve().parents[1] / 'tools/source_census.py')
census = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(census)


class StreamingTests(unittest.TestCase):
    def fetch(self, data, cap, declared=None):
        opener = MagicMock()
        response = io.BytesIO(data)
        response.headers = {} if declared is None else {'Content-Length': str(declared)}
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.tar.gz'
            with patch.object(census.urllib.request, 'build_opener', return_value=opener):
                try:
                    size = census.archive_to_file('https://codeload.github.com/szl-holdings/test/tar.gz/' + 'a' * 40, path, cap)
                except census.CensusError:
                    self.assertFalse(path.exists())
                    raise
            self.assertEqual(path.read_bytes(), data)
            request = opener.open.call_args.args[0]
            self.assertNotIn('Authorization', request.headers)
            return size

    def test_exact_cap_streams_without_credentials(self):
        self.assertEqual(self.fetch(b'abcd', 4), 4)

    def test_overflow_removes_partial_file(self):
        with self.assertRaises(census.CensusError):
            self.fetch(b'abcde', 4)

    def test_declared_oversize_is_rejected_before_body_read(self):
        with self.assertRaises(census.CensusError):
            self.fetch(b'', 4, 5)

    def test_archive_other_host_is_refused(self):
        with self.assertRaises(census.CensusError):
            census.archive_to_file('https://example.com/file', Path('unused'), 4)

    def test_preexisting_destination_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.tar.gz'
            path.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError):
                census.archive_to_file('https://codeload.github.com/szl-holdings/test/tar.gz/' + 'a' * 40, path, 4)
            self.assertEqual(path.read_bytes(), b'preserve')

    def test_check_pagination_and_failures_are_preserved(self):
        row = {'id': 1, 'name': 'test', 'status': 'completed', 'conclusion': 'failure', 'html_url': 'https://github.com/szl-holdings/test'}
        with patch.object(census, 'api', side_effect=[{'total_count': 2, 'check_runs': [row]}, {'total_count': 2, 'check_runs': [{**row, 'id': 2, 'conclusion': 'success'}]}]):
            result = census.source_checks('szl-holdings/test', 'a' * 40, None)
        self.assertEqual(result['counts'], {'failure': 1, 'success': 1})
        self.assertEqual(result['required_status_or_review_policy'], 'NOT_EVALUATED')

    def test_absent_checks_are_not_a_green_build(self):
        with patch.object(census, 'api', return_value={'total_count': 0, 'check_runs': []}):
            result = census.source_checks('szl-holdings/test', 'a' * 40, None)
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['runtime_acceptance'], 'NOT_CLAIMED')


if __name__ == '__main__':
    unittest.main(verbosity=2)
