"""szl-ci-witness: CI runs as a verifiable hash-chained witness record."""

from .witness import GENESIS, verify_witness_chain, witness_record, witness_summary

__all__ = ["GENESIS", "verify_witness_chain", "witness_record", "witness_summary"]
__version__ = "0.1.0"
