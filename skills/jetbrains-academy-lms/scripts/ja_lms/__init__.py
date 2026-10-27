"""Client for the JetBrains Academy teaching LMS (reverse-engineered, unofficial)."""

from .assignments import AssignmentFormError, AssignmentService, Plan
from .client import AuthExpiredError, Course, LMSClient, LMSError, LMSHTTPError, Response
from .credentials import (
    ChainCredentialProvider,
    CredentialProvider,
    Credentials,
    CredentialsError,
    EnvCredentialProvider,
    FileCredentialProvider,
    StaticCredentialProvider,
    default_provider,
)

__version__ = "0.3.0"

__all__ = [
    "AssignmentFormError",
    "AssignmentService",
    "AuthExpiredError",
    "ChainCredentialProvider",
    "Course",
    "CredentialProvider",
    "Credentials",
    "CredentialsError",
    "EnvCredentialProvider",
    "FileCredentialProvider",
    "LMSClient",
    "LMSError",
    "LMSHTTPError",
    "Plan",
    "Response",
    "StaticCredentialProvider",
    "default_provider",
]
