"""Local synthetic redirects only. No real tokens, network, or GitHub state."""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.request import Request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from szl_ci_witness.github_artifacts import SafeRedirect

SOURCE = "https://api.github.com/repos/szl-holdings/szl-ci-witness/actions/artifacts/5/zip"
FAKE_HEADER = "Bearer SYNTHETIC_ONLY"


def response(target: str):
    request = Request(SOURCE, headers={"Authorization": FAKE_HEADER})
    return SafeRedirect().redirect_request(request, None, 302, "Found", {}, target)


def test_same_origin_https_redirect_retains_api_auth():
    result = response("https://api.github.com/other-api-path")
    assert result.full_url == "https://api.github.com/other-api-path"
    assert result.get_header("Authorization") == FAKE_HEADER


def test_explicit_default_https_port_is_same_origin():
    result = response("https://api.github.com:443/next")
    assert result.get_header("Authorization") == FAKE_HEADER


@pytest.mark.parametrize("target", [
    "https://pipelinesghubeus.blob.core.windows.net/signed-download",
    "https://objects.githubusercontent.com/signed-download",
    "https://artifact-storage.invalid/signed-download",
])
def test_cross_origin_https_artifact_redirect_strips_auth(target):
    result = response(target)
    assert result.full_url == target
    assert not result.has_header("Authorization")
    assert not result.get_header("Authorization")


@pytest.mark.parametrize("target", [
    "http://api.github.com/downgraded",
    "http://api.github.com:443/strange",
    "http://artifact-storage.invalid/unsigned",
    "ftp://api.github.com/backup",
    "file:///etc/passwd",
    "//api.github.com/relative-origin",
    "https://user:password@api.github.com/embedded-credential",
    "https://api.github.com:8443/other-port",
    "https://api.github.com:65536/invalid-port",
    "https:///missing-host",
])
def test_unsafe_or_ambiguous_redirect_denied(target):
    with pytest.raises(ValueError):
        response(target)


def test_redirects_do_not_change_original_request_headers():
    original = Request(SOURCE, headers={"Authorization": FAKE_HEADER})
    destination = SafeRedirect().redirect_request(
        original, None, 302, "Found", {}, "https://artifact-storage.invalid/zip")
    assert original.get_header("Authorization") == FAKE_HEADER
    assert destination.get_header("Authorization") is None


def test_cross_origin_cannot_be_mistaken_for_api_origin():
    result = response("https://api.github.com.attacker.invalid/zip")
    assert result.get_header("Authorization") is None


def test_redirect_handler_never_accesses_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected network call")
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    # Only constructs sanitized Requests. No host is contacted.
    result = response("https://artifact-storage.invalid/download")
    assert result.get_header("Authorization") is None
