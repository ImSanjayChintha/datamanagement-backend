"""
Module: administration.core.constants
Purpose: Administration-module role constants — thin aliases over app.core.constants.
         Never redefine role tuples here; always import from the single source of truth.
"""
from app.core.constants import ADMIN_ROLES, WRITER_ROLES

# Alias used within the administration module so callers read naturally
VALID_ROLES = ADMIN_ROLES
