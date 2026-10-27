"""Session credentials for the JetBrains Academy LMS.

The LMS login form is protected by reCAPTCHA, so a script cannot log in with a
username and password. Instead it reuses an authenticated *browser session*: the
``sessionid`` and ``csrftoken`` cookies. Those are live credentials. Nothing in
this module ships with, logs, or prints their values.

Credential sources are pluggable. A provider implements ``load()`` and, if it
can persist changes, ``save()``. Built-in providers:

* ``EnvCredentialProvider`` - ``JA_LMS_SESSIONID`` / ``JA_LMS_CSRFTOKEN`` /
  ``JA_LMS_BASE_URL``. Read-only; suited to CI secrets.
* ``FileCredentialProvider`` - a JSON file outside the repository, written
  atomically with ``0600`` permissions. Default location:
  ``$JA_LMS_CREDENTIALS`` or ``$XDG_CONFIG_HOME/ja-lms/credentials.json``
  (``~/.config/ja-lms/credentials.json``).
* ``ChainCredentialProvider`` - first provider that yields credentials wins;
  saves go to the first provider that supports saving.

Plug in anything else (a keyring, a secrets manager, a prompt) by implementing
the ``CredentialProvider`` protocol.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional, Protocol, Sequence

DEFAULT_BASE_URL = "https://lms.academy.jetbrains.org"

ENV_SESSIONID = "JA_LMS_SESSIONID"
ENV_CSRFTOKEN = "JA_LMS_CSRFTOKEN"
ENV_BASE_URL = "JA_LMS_BASE_URL"
ENV_CREDENTIALS_FILE = "JA_LMS_CREDENTIALS"


class CredentialsError(Exception):
    """Credentials are missing, unreadable, or rejected by the LMS."""


@dataclass(frozen=True)
class Credentials:
    sessionid: str
    csrftoken: str
    base_url: str = DEFAULT_BASE_URL

    def __post_init__(self) -> None:
        if not self.sessionid or not self.csrftoken:
            raise CredentialsError("both sessionid and csrftoken are required")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def with_cookies(self, sessionid: Optional[str] = None, csrftoken: Optional[str] = None) -> "Credentials":
        return replace(
            self,
            sessionid=sessionid or self.sessionid,
            csrftoken=csrftoken or self.csrftoken,
        )

    def __repr__(self) -> str:  # never leak secrets through repr/logging
        return f"Credentials(base_url={self.base_url!r}, sessionid=<hidden>, csrftoken=<hidden>)"

    __str__ = __repr__


class CredentialProvider(Protocol):
    def load(self) -> Optional[Credentials]:
        """Return credentials, or ``None`` if this source has none."""

    def save(self, credentials: Credentials) -> None:
        """Persist credentials. Raise ``NotImplementedError`` if read-only."""


class StaticCredentialProvider:
    """In-memory credentials, e.g. supplied by a host application."""

    def __init__(self, credentials: Optional[Credentials] = None) -> None:
        self._credentials = credentials

    def load(self) -> Optional[Credentials]:
        return self._credentials

    def save(self, credentials: Credentials) -> None:
        self._credentials = credentials


class EnvCredentialProvider:
    """Read-only credentials from environment variables."""

    def __init__(self, environ: Optional[dict] = None) -> None:
        self._environ = os.environ if environ is None else environ

    def load(self) -> Optional[Credentials]:
        sessionid = self._environ.get(ENV_SESSIONID)
        csrftoken = self._environ.get(ENV_CSRFTOKEN)
        if not sessionid or not csrftoken:
            return None
        return Credentials(sessionid, csrftoken, self._environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL)

    def save(self, credentials: Credentials) -> None:
        raise NotImplementedError("environment credentials are read-only")


def default_credentials_path(environ: Optional[dict] = None) -> Path:
    environ = os.environ if environ is None else environ
    explicit = environ.get(ENV_CREDENTIALS_FILE)
    if explicit:
        return Path(explicit).expanduser()
    config_home = environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home).expanduser() / "ja-lms" / "credentials.json"


class FileCredentialProvider:
    """Credentials in a private JSON file outside the repository."""

    def __init__(self, path: Optional[os.PathLike] = None) -> None:
        self.path = Path(path) if path is not None else default_credentials_path()

    def load(self) -> Optional[Credentials]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CredentialsError(f"cannot read {self.path}: {exc.strerror}") from None
        try:
            data = json.loads(raw)
            return Credentials(
                sessionid=data["sessionid"],
                csrftoken=data["csrftoken"],
                base_url=data.get("base_url") or DEFAULT_BASE_URL,
            )
        except (ValueError, KeyError, TypeError):
            raise CredentialsError(f"{self.path} is not a valid credentials file") from None

    def save(self, credentials: Credentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix=".credentials-", dir=self.path.parent)
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(credentials), fh, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def clear(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False


class ChainCredentialProvider:
    """Try providers in order.

    Saves go back to the provider that supplied the current credentials, so
    rotated cookies from a read-only source (environment, CI secret) are never
    silently copied to disk. With nothing loaded yet, saves go to the first
    provider that accepts writes.
    """

    def __init__(self, providers: Sequence[CredentialProvider]) -> None:
        self.providers = list(providers)
        self._source: Optional[CredentialProvider] = None

    def load(self) -> Optional[Credentials]:
        for provider in self.providers:
            credentials = provider.load()
            if credentials is not None:
                self._source = provider
                return credentials
        self._source = None
        return None

    def save(self, credentials: Credentials) -> None:
        if self._source is not None:
            self._source.save(credentials)
            return
        for provider in self.providers:
            try:
                provider.save(credentials)
                return
            except NotImplementedError:
                continue
        raise NotImplementedError("no writable credential provider configured")


def default_provider() -> ChainCredentialProvider:
    """Environment variables first, then the private credentials file."""
    return ChainCredentialProvider([EnvCredentialProvider(), FileCredentialProvider()])
