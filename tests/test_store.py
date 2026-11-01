"""Conditional writes in every store, and the Blob REST calls without a network."""

import json
import time
from pathlib import Path

import httpx2
import pytest

from backend.app.store import (
    BLOB_API_VERSION,
    SEND_BY,
    AzureBlobStore,
    DirectoryStore,
    MemoryStore,
    StoreUnavailable,
    VersionConflict,
    blob_container,
    managed_identity_token,
    open_store,
)


@pytest.fixture(params=["memory", "directory"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> MemoryStore | DirectoryStore:
    return MemoryStore() if request.param == "memory" else DirectoryStore(tmp_path / "store")


def test_a_write_needs_the_version_it_read(store: MemoryStore | DirectoryStore) -> None:
    assert store.read("ledger.json") is None
    first = store.create("ledger.json", {"spent": 1})
    with pytest.raises(VersionConflict):
        store.create("ledger.json", {"spent": 2})
    second = store.replace("ledger.json", {"spent": 2}, first)
    with pytest.raises(VersionConflict):  # another writer already moved past this version
        store.replace("ledger.json", {"spent": 3}, first)
    document = store.read("ledger.json")
    assert document is not None and document.data == {"spent": 2} and document.etag == second
    store.delete("ledger.json")
    with pytest.raises(VersionConflict):
        store.replace("ledger.json", {"spent": 4}, second)


def test_directory_documents_survive_a_new_store_object(tmp_path: Path) -> None:
    DirectoryStore(tmp_path).create("invites/a.json", {"plan_requests": 3})
    document = DirectoryStore(tmp_path).read("invites/a.json")
    assert document is not None and document.data == {"plan_requests": 3}
    with pytest.raises(ValueError, match="escapes"):
        DirectoryStore(tmp_path).read("../outside.json")


def blob_store(handler: httpx2.MockTransport) -> AzureBlobStore:
    return AzureBlobStore(
        "https://stfullaura.blob.core.windows.net",
        "pilot",
        token=lambda client: ("token-1", 9e12),
        client=httpx2.Client(transport=handler),
    )


def test_blob_writes_are_conditional_and_authenticated() -> None:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx2.Response(200, content=b'{"spent": 1}', headers={"ETag": '"0x1"'})
        if request.headers.get("If-None-Match") == "*":
            return httpx2.Response(409, headers={"x-ms-error-code": "BlobAlreadyExists"})
        if request.headers.get("If-Match") == '"0x1"':
            return httpx2.Response(201, headers={"ETag": '"0x2"'})
        return httpx2.Response(412, headers={"x-ms-error-code": "ConditionNotMet"})

    store = blob_store(httpx2.MockTransport(handle))
    document = store.read("invites/a b.json")
    assert document is not None and document.data == {"spent": 1} and document.etag == '"0x1"'
    with pytest.raises(VersionConflict):
        store.create("ledger.json", {"spent": 0})
    assert store.replace("ledger.json", {"spent": 2}, '"0x1"') == '"0x2"'
    with pytest.raises(VersionConflict):
        store.replace("ledger.json", {"spent": 3}, '"0x0"')

    blob = "https://stfullaura.blob.core.windows.net/pilot/invites/a%20b.json"
    assert str(seen[0].url) == f"{blob}?timeout=10"  # the service gives up after 10 seconds
    assert all(request.headers["Authorization"] == "Bearer token-1" for request in seen)
    assert all(request.headers["x-ms-version"] == BLOB_API_VERSION for request in seen)
    put = seen[2]
    assert put.headers["x-ms-blob-type"] == "BlockBlob"
    assert json.loads(put.content) == {"spent": 2}


def test_a_write_that_missed_its_closure_window_is_never_sent() -> None:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(201, headers={"ETag": '"0x1"'})

    store = blob_store(httpx2.MockTransport(handle))
    late = SEND_BY.set(time.monotonic() - 1)  # a token refresh outlasted the window
    try:
        with pytest.raises(StoreUnavailable, match="not sent in time"):
            store.create("invites/a.json", {})
    finally:
        SEND_BY.reset(late)
    assert seen == []
    in_time = SEND_BY.set(time.monotonic() + 5)
    try:
        assert store.create("invites/a.json", {}) == '"0x1"'
    finally:
        SEND_BY.reset(in_time)
    assert len(seen) == 1


@pytest.mark.parametrize("failure", [httpx2.Response(500), httpx2.Response(403)])
def test_blob_errors_make_the_store_unavailable(failure: httpx2.Response) -> None:
    store = blob_store(httpx2.MockTransport(lambda request: failure))
    with pytest.raises(StoreUnavailable):
        store.read("ledger.json")
    with pytest.raises(StoreUnavailable):
        store.replace("ledger.json", {}, '"0x1"')


def test_unreachable_blob_storage_is_unavailable_not_a_crash() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(StoreUnavailable):
        blob_store(httpx2.MockTransport(refuse)).read("ledger.json")


def test_managed_identity_token_uses_the_container_apps_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"access_token": "abc", "expires_on": "1900000000"})

    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://localhost:42356/msi/token")
    monkeypatch.setenv("IDENTITY_HEADER", "header-secret")
    token = managed_identity_token(httpx2.Client(transport=httpx2.MockTransport(handle)))
    assert token == ("abc", 1900000000.0)
    assert seen[0].headers["X-IDENTITY-HEADER"] == "header-secret"
    assert seen[0].url.params["resource"] == "https://storage.azure.com/"

    monkeypatch.delenv("IDENTITY_ENDPOINT")
    with pytest.raises(StoreUnavailable, match="managed identity"):
        managed_identity_token(httpx2.Client(transport=httpx2.MockTransport(handle)))


def test_a_missing_blob_reads_as_none_but_a_missing_container_is_an_outage() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        code = "BlobNotFound" if "ledger" in request.url.path else "ContainerNotFound"
        return httpx2.Response(404, headers={"x-ms-error-code": code})

    store = blob_store(httpx2.MockTransport(handle))
    assert store.read("ledger.json") is None
    with pytest.raises(StoreUnavailable):
        store.read("invites/a.json")
    with pytest.raises(StoreUnavailable):
        store.create("invites/a.json", {})


@pytest.mark.parametrize(
    "url",
    [
        "https://stfullaura.blob.core.windows.net/pilot",
        "https://stfullaura.blob.core.windows.net/pilot/",
        "https://STFULLAURA.blob.core.windows.net/pilot",
    ],
)
def test_a_blob_container_url_names_an_account_and_a_container(url: str) -> None:
    assert blob_container(url) == ("https://stfullaura.blob.core.windows.net", "pilot")
    assert isinstance(open_store(url), AzureBlobStore)


@pytest.mark.parametrize(
    "url",
    [
        "http://stfullaura.blob.core.windows.net/pilot",  # the token would travel in clear
        "https://evil.example/.blob.core.windows.net/pilot",  # the account name is a path
        "https://stfullaura.blob.core.windows.net.evil.example/pilot",
        "https://stfullaura.blob.core.windows.net:8443/pilot",
        "https://user@stfullaura.blob.core.windows.net/pilot",
        "https://stfullaura.blob.core.windows.net/",
        "https://stfullaura.blob.core.windows.net/pilot/invites",
        "https://stfullaura.blob.core.windows.net/pilot?sv=2023",
        "https://[::1.blob.core.windows.net/pilot",
    ],
)
def test_no_other_url_gets_a_storage_token(url: str) -> None:
    assert blob_container(url) is None
    with pytest.raises(ValueError):
        open_store(url)
