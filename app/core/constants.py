"""
Module: core.constants
Purpose: Application-wide constants shared across all modules.
         All magic numbers, string literals, and configuration defaults live here.
         Import from this module instead of hardcoding values anywhere.
"""
import string

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
API_VERSION = "3.0.0"
API_V1_PREFIX = "/api/v1"

# ---------------------------------------------------------------------------
# Security / Auth
# ---------------------------------------------------------------------------
ADMIN_ROLES = ("admin", "local_admin", "writer", "reader")
"""Tuple of all valid admin role names."""

WRITER_ROLES = ("admin", "local_admin", "writer")
"""Roles that are allowed to create/modify data."""

ADMIN_ONLY_ROLES = ("admin",)
"""Roles restricted to super-admin operations."""

MIN_PASSWORD_LENGTH = 8
TEMP_PASSWORD_LENGTH = 10
TEMP_PASSWORD_CHARSET = string.ascii_letters + string.digits + "!@#$"
INVITE_TOKEN_EXPIRY_DAYS = 7
WEBHOOK_MAX_AGE_SECONDS = 300
BEARER_SCHEME = "bearer"

# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
MAX_QUERY_LIMIT = 500
DEFAULT_QUERY_LIMIT = 50

# ---------------------------------------------------------------------------
# Customer defaults
# ---------------------------------------------------------------------------
CUSTOMER_CODE_PREFIX = "CUST"
DEFAULT_ACCOUNT_TYPE = "retail"
DEFAULT_CURRENCY = "USD"
