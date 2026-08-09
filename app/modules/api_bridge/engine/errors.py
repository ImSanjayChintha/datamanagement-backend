from __future__ import annotations

from fastapi.responses import JSONResponse


class EngineError(Exception):
    """Raised by any engine component; route handlers convert this to a uniform JSON response."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        errors: dict | None = None,
        http_status: int = 400,
    ) -> None:
        super().__init__(message or code)
        self.code        = code
        self.message     = message or code
        self.errors      = errors or {}
        self.http_status = http_status

    def to_response(self) -> JSONResponse:
        body: dict = {"ok": False, "error": self.code}
        if self.message and self.message != self.code:
            body["message"] = self.message
        if self.errors:
            body["errors"] = self.errors
        return JSONResponse(content=body, status_code=self.http_status)


# ── Factory helpers ──────────────────────────────────────────────────────────

def unknown_object(code: str) -> EngineError:
    return EngineError("unknown_object", f"No registered object: {code!r}", http_status=404)


def unknown_action(action: str) -> EngineError:
    return EngineError("unknown_action", f"Action not mapped: {action!r}", http_status=404)


def forbidden(detail: str = "Forbidden") -> EngineError:
    return EngineError("forbidden", detail, http_status=403)


def unauthorized(detail: str = "Unauthorized") -> EngineError:
    return EngineError("unauthorized", detail, http_status=401)


def validation_failed(errors: dict) -> EngineError:
    return EngineError("validation_failed", "Validation failed", errors=errors, http_status=400)


def filter_required() -> EngineError:
    return EngineError(
        "filter_required",
        "A non-empty filter is required for this action",
        http_status=400,
    )


def expected_mismatch(expected: int, actual: int) -> EngineError:
    return EngineError(
        "expected_mismatch",
        f"Expected to affect {expected} row(s) but matched {actual}",
        http_status=409,
    )


def not_found() -> EngineError:
    return EngineError("not_found", "Record not found", http_status=404)


def not_unique() -> EngineError:
    return EngineError(
        "not_unique",
        "Multiple records matched; expected exactly one",
        http_status=409,
    )


def unknown_field(name: str) -> EngineError:
    return EngineError("unknown_field", f"Unknown field: {name!r}", http_status=400)


def limit_exceeded(limit: int, max_limit: int) -> EngineError:
    return EngineError(
        "limit_exceeded",
        f"Requested limit {limit} exceeds maximum {max_limit}",
        http_status=400,
    )


def internal(detail: str = "Internal error") -> EngineError:
    return EngineError("internal", detail, http_status=500)
