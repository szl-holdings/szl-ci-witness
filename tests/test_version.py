"""Distribution and public runtime version must agree."""
from importlib.metadata import version
import unittest
import szl_ci_witness


class VersionTests(unittest.TestCase):
    def test_runtime_matches_distribution(self):
        self.assertEqual(szl_ci_witness.__version__, version("szl-ci-witness"))
