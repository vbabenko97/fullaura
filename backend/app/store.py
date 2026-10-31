"""Small JSON documents with conditional writes, for the pilot's quotas, spending and feedback.

Every write states what it expects: a new document, or the version it last read (its ETag). A
write that finds another version raises VersionConflict, so two requests that change one counter
cannot both succeed from the same version; the caller reads again and retries. Documents never
hold photos, previews, provider responses or full plans (docs/DATA.md).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from xml.etree import ElementTree

import httpx2

BLOB_API_VERSION = "2023-11-03"
STORAGE_RESOURCE = "https://storage.azure.com/"
STORE_TIMEOUT_S = 10.0


class StoreUnavailable(RuntimeError):
    """The store could not be reached or answered with an error."""


class VersionConflict(RuntimeError):
    """The document is not the version the write expected."""


@dataclass(frozen=True, slots=True)
class Document:
    data: dict[str, Any]
    etag: str


class Store(Protocol):
    def read(self, name: str) -> Document | None: ...

    def create(self, name: str, data: dict[str, Any]) -> str:
        """Write a new document and return its ETag; VersionConflict if it exists."""
        ...

    def replace(self, name: str, data: dict[str, Any], etag: str) -> str:
        """Overwrite the version with this ETag; VersionConflict if it changed or is gone."""
        ...

    def delete(self, name: str) -> None: ...

    def names(self) -> list[str]:
        """Every document's name."""
        ...


def _encode(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def _etag(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class MemoryStore:
    """For tests and local development; contents end with the process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._documents: dict[str, bytes] = {}

    def read(self, name: str) -> Document | None:
        with self._lock:
            content = self._documents.get(name)
        return None if content is None else Document(json.loads(content), _etag(content))

    def create(self, name: str, data: dict[str, Any]) -> str:
        content = _encode(data)
        with self._lock:
            if name in self._documents:
                raise VersionConflict(name)
            self._documents[name] = content
        return _etag(content)

    def replace(self, name: str, data: dict[str, Any], etag: str) -> str:
        content = _encode(data)
        with self._lock:
            current = self._documents.get(name)
            if current is None or _etag(current) != etag:
                raise VersionConflict(name)
            self._documents[name] = content
        return _etag(content)

    def delete(self, name: str) -> None:
        with self._lock:
            self._documents.pop(name, None)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._documents)


class DirectoryStore:
    """One file per document under a local folder, so local counts survive a restart. Assumes
    one process, which the single Uvicorn worker guarantees."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()

    def _path(self, name: str) -> Path:
        path = (self._root / name).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise ValueError(f"Document name escapes the store: {name!r}")
        return path

    def read(self, name: str) -> Document | None:
        path = self._path(name)
        with self._lock:
            try:
                content = path.read_bytes()
            except FileNotFoundError:
                return None
        return Document(json.loads(content), _etag(content))

    def create(self, name: str, data: dict[str, Any]) -> str:
        path = self._path(name)
        with self._lock:
            if path.exists():
                raise VersionConflict(name)
            return self._write(path, _encode(data))

    def replace(self, name: str, data: dict[str, Any], etag: str) -> str:
        path = self._path(name)
        with self._lock:
            try:
                current = path.read_bytes()
            except FileNotFoundError:
                raise VersionConflict(name) from None
            if _etag(current) != etag:
                raise VersionConflict(name)
            return self._write(path, _encode(data))

    def delete(self, name: str) -> None:
        with self._lock:
            self._path(name).unlink(missing_ok=True)

    def names(self) -> list[str]:
        with self._lock:
            if not self._root.is_dir():
                return []
            files = (path for path in self._root.rglob("*") if path.is_file())
            return sorted(path.relative_to(self._root).as_posix() for path in files)

    @staticmethod
    def _write(path: Path, content: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.part")
        partial.write_bytes(content)
        os.replace(partial, path)
        return _etag(content)


def managed_identity_token(client: httpx2.Client) -> tuple[str, float]:
    """A storage token from the Container Apps managed identity endpoint, and when it expires."""
    endpoint = os.environ.get("IDENTITY_ENDPOINT")
    secret = os.environ.get("IDENTITY_HEADER")
    if not endpoint or not secret:
        raise StoreUnavailable("No managed identity is available to this process")
    try:
        response = client.get(
            endpoint,
            params={"resource": STORAGE_RESOURCE, "api-version": "2019-08-01"},
            headers={"X-IDENTITY-HEADER": secret},
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["access_token"]), float(payload["expires_on"])
    except (httpx2.HTTPError, ValueError, KeyError, TypeError) as error:
        raise StoreUnavailable(f"Managed identity token failed: {type(error).__name__}") from None


@dataclass(slots=True)
class AzureBlobStore:
    """Block blobs in one private container, read and written with Microsoft Entra tokens.

    ETags come from the Blob service, and If-Match / If-None-Match make writes conditional
    (https://learn.microsoft.com/en-us/azure/storage/blobs/concurrency-manage)."""

    account_url: str  # such as https://stfullaura.blob.core.windows.net
    container: str
    token: Callable[[httpx2.Client], tuple[str, float]] = managed_identity_token
    client: httpx2.Client = field(
        default_factory=lambda: httpx2.Client(timeout=STORE_TIMEOUT_S, follow_redirects=False)
    )
    _cached: tuple[str, float] | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        with self._lock:
            if self._cached is None or self._cached[1] - time.time() < 300:
                self._cached = self.token(self.client)
            access_token = self._cached[0]
        return {
            "Authorization": f"Bearer {access_token}",
            "x-ms-version": BLOB_API_VERSION,
            **(extra or {}),
        }

    def _url(self, name: str) -> str:
        container = f"{self.account_url.rstrip('/')}/{self.container}"
        return f"{container}/{quote(name)}" if name else container

    def _send(self, method: str, name: str, **kwargs: Any) -> httpx2.Response:
        headers = self._headers(kwargs.pop("headers", None))
        try:
            return self.client.request(method, self._url(name), headers=headers, **kwargs)
        except httpx2.HTTPError as error:
            raise StoreUnavailable(f"Blob {method} failed: {type(error).__name__}") from None

    def read(self, name: str) -> Document | None:
        response = self._send("GET", name)
        if response.status_code == 404 and _error_code(response) == "BlobNotFound":
            return None
        if response.status_code != 200:
            raise StoreUnavailable(f"Blob GET returned HTTP {response.status_code}")
        try:
            return Document(json.loads(response.content), response.headers["ETag"])
        except (ValueError, KeyError) as error:
            raise StoreUnavailable(f"Blob {name} is unreadable: {type(error).__name__}") from None

    def create(self, name: str, data: dict[str, Any]) -> str:
        return self._put(name, data, {"If-None-Match": "*"})

    def replace(self, name: str, data: dict[str, Any], etag: str) -> str:
        return self._put(name, data, {"If-Match": etag})

    def _put(self, name: str, data: dict[str, Any], condition: dict[str, str]) -> str:
        headers = {"x-ms-blob-type": "BlockBlob", "Content-Type": "application/json", **condition}
        response = self._send("PUT", name, content=_encode(data), headers=headers)
        # A blob that exists answers If-None-Match: *, a changed ETag answers If-Match, and a
        # deleted blob answers a replace; any other error, such as a missing container, means
        # the store is not usable.
        if _error_code(response) in {"BlobAlreadyExists", "ConditionNotMet", "BlobNotFound"}:
            raise VersionConflict(name)
        if response.status_code != 201:
            raise StoreUnavailable(f"Blob PUT returned HTTP {response.status_code}")
        return response.headers["ETag"]

    def delete(self, name: str) -> None:
        response = self._send("DELETE", name)
        gone = response.status_code == 404 and _error_code(response) == "BlobNotFound"
        if response.status_code != 202 and not gone:
            raise StoreUnavailable(f"Blob DELETE returned HTTP {response.status_code}")

    def names(self) -> list[str]:
        """Every blob in the container, page by page
        (https://learn.microsoft.com/en-us/rest/api/storageservices/list-blobs)."""
        names: list[str] = []
        marker = ""
        while True:
            params = {"restype": "container", "comp": "list"}
            response = self._send("GET", "", params=params | ({"marker": marker} if marker else {}))
            if response.status_code != 200:
                raise StoreUnavailable(f"Blob list returned HTTP {response.status_code}")
            try:
                listing = ElementTree.fromstring(response.content)
            except ElementTree.ParseError:
                raise StoreUnavailable("Blob list is unreadable") from None
            names += [name.text or "" for name in listing.iterfind("Blobs/Blob/Name")]
            marker = listing.findtext("NextMarker") or ""
            if not marker:
                return names


def open_store(url: str) -> Store:
    if url == "memory://":
        return MemoryStore()
    if url.startswith("file://"):
        return DirectoryStore(Path(url.removeprefix("file://")))
    if url.startswith("https://") and ".blob.core.windows.net/" in url:
        account_url, container = url.rstrip("/").rsplit("/", 1)
        return AzureBlobStore(account_url, container)
    raise ValueError("FULLAURA_STORE_URL must be memory://, file:///folder or a blob container URL")


def _error_code(response: httpx2.Response) -> str | None:
    return response.headers.get("x-ms-error-code")
