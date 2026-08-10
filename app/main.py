"""
Module: main
Purpose: FastAPI application factory for the GMC E-Commerce Platform.

Registers all routers under the /api/v1 prefix and configures CORS middleware.
The lifespan handler initialises the asyncpg connection pool on startup and
closes it cleanly on shutdown.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.constants import API_VERSION, API_V1_PREFIX
from app.core.database import init_pool, close_pool, get_conn
# ── Routers ────────────────────────────────────────────────────────────────
from app.modules.administration.routers.auth        import router as auth_router
from app.modules.administration.routers.admin_users import router as admin_users_router

from app.modules.company.routers.languages         import router as company_languages_router
from app.modules.company.routers.translations      import router as company_translations_router
from app.modules.dbtoolkit.routers.common_fields import router as toolkit_common_fields_router
from app.modules.dbtoolkit.routers.tables       import router as toolkit_tables_router
from app.modules.dbtoolkit.routers.fields       import router as toolkit_fields_router
from app.modules.dbtoolkit.routers.options      import router as toolkit_options_router
from app.modules.dbtoolkit.routers.references   import router as toolkit_refs_router
from app.modules.dbtoolkit.routers.translations import router as toolkit_translations_router
from app.modules.dbtoolkit.routers.data         import router as toolkit_data_router
from app.modules.dbtoolkit.routers.sql          import router as toolkit_sql_router
from app.modules.dbtoolkit.routers.ai           import router as toolkit_ai_router
from app.modules.dbtoolkit.routers.objects      import router as toolkit_objects_router
from app.modules.dbtoolkit.routers.schemas      import router as toolkit_schemas_router
from app.modules.dbtoolkit.routers.activity_log import router as toolkit_activity_log_router
from app.modules.dbtoolkit.routers.page_defs    import router as toolkit_page_defs_router

from app.modules.api_bridge.resources.routers.resources    import router as apib_resources_router
from app.modules.dbtoolkit.routers.export import router as toolkit_export_router
from app.modules.api_bridge.gateway.routers.endpoints      import router as gw_endpoints_router
from app.modules.api_bridge.gateway.routers.schema_browser import router as gw_schema_router
from app.modules.api_bridge.gateway.routers.runtime             import router as gw_runtime_router
from app.modules.api_bridge.gateway.routers.gateway_passthrough import router as gw_passthrough_router
from app.modules.api_bridge.gateway.routers.openapi_spec        import router as gw_openapi_router
from app.modules.api_bridge.gateway.routers.services            import router as gw_services_router
from app.modules.api_bridge.engine.routers.engine               import router as engine_router
import app.modules.api_bridge.services.loader  # noqa: F401  — registers all service classes
from app.modules.push_destinations.routers.destinations import router as push_dest_router

def _get_routers() -> list:
    """Return the ordered list of router objects to register.

    Returns:
        List of router objects to include under the API v1 prefix.
    """
    return [
        # Auth + users
        auth_router,
        admin_users_router,
        # Company
        company_languages_router,
        company_translations_router,
        # Toolkit
        toolkit_common_fields_router,
        toolkit_tables_router,
        toolkit_fields_router,
        toolkit_options_router,
        toolkit_refs_router,
        toolkit_translations_router,
        toolkit_data_router,
        toolkit_sql_router,
        toolkit_ai_router,
        toolkit_objects_router,
        toolkit_schemas_router,
        toolkit_activity_log_router,
        toolkit_page_defs_router,
        # API Bridge — external API resource connections
        apib_resources_router,
        # Gateway — API endpoint builder (admin CRUD)
        gw_endpoints_router,
        gw_schema_router,
        # Gateway — service registry (must be before passthrough to avoid catch-all interception)
        gw_services_router,
        # Gateway — runtime (serves the defined endpoints via /run/)
        gw_runtime_router,
        # Gateway — passthrough (serves defined endpoints at /gateway/ directly, before engine)
        gw_passthrough_router,
        # Gateway — OpenAPI spec generator
        gw_openapi_router,
        # Push Destinations
        push_dest_router,
        #Export Template
        toolkit_export_router
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the asyncpg connection pool lifecycle.

    Initialises the pool on startup, yields control to FastAPI,
    then closes the pool cleanly on shutdown.
    """
    await init_pool()
    yield
    await close_pool()


_OPENAPI_TAGS = [
    {
        "name": "Authentication",
        "description": (
            "Admin and customer login, token refresh, and password management. "
            "Admin endpoints return a JWT with role `admin | local_admin | writer | reader`. "
            "Customer endpoints return a JWT with role `customer`."
        ),
    },
    {
        "name": "Admin - Users",
        "description": (
            "Manage the back-office admin user accounts. Invite new users, "
            "update profiles, reset passwords, and deactivate accounts."
        ),
    },
    {
        "name": "Company - Languages",
        "description": "Configure the languages available for multilingual content.",
    },
    {
        "name": "Company - Translations",
        "description": "Manage UI translation strings (key/value pairs per language).",
    },
    {
        "name": "Toolkit - Tables",
        "description": (
            "Create and manage toolkit-managed PostgreSQL tables. "
            "Each table generates a corresponding view and helper functions. "
            "DDL preview, execution, and schema-context export for AI prompts."
        ),
    },
    {
        "name": "Toolkit - Fields",
        "description": (
            "Add, update, reorder, and delete columns on toolkit tables. "
            "Backed by `information_schema`; physical DDL is executed on every change."
        ),
    },
    {
        "name": "Toolkit - Field Options",
        "description": "Manage inline-select option values for `select` type fields.",
    },
    {
        "name": "Toolkit - References",
        "description": (
            "Configure foreign-key reference metadata for `reference` type fields: "
            "target table/column, label column, and filter rules."
        ),
    },
    {
        "name": "Toolkit - Common Fields",
        "description": (
            "Reusable field definitions that can be applied across multiple tables. "
            "Includes bulk translation management."
        ),
    },
    {
        "name": "Toolkit - Translations",
        "description": "Language list endpoint for multilingual UI components.",
    },
    {
        "name": "Toolkit - Data",
        "description": (
            "Generic CRUD for any toolkit-managed table using the table `code` as a path param "
            "(`{schema}.{table}`). Supports list, get, create, update, delete, and reference-option lookup."
        ),
    },
    {
        "name": "Toolkit - SQL Console",
        "description": (
            "Execute arbitrary SQL against the database with validation, DDL cataloguing, "
            "and a history log. Restricted to admin role."
        ),
    },
    {
        "name": "Toolkit - AI",
        "description": (
            "AI-assisted SQL generation and content translation. "
            "Generate tables/views/functions from natural language, regenerate existing objects, "
            "and translate field values into multiple languages."
        ),
    },
    {
        "name": "Toolkit - Objects",
        "description": (
            "Manage toolkit *objects* (views and functions) that are defined by stored SQL "
            "and re-executed on demand."
        ),
    },
    {
        "name": "Toolkit - Schemas",
        "description": "List, inspect, and create PostgreSQL schemas for toolkit domains.",
    },
    {
        "name": "Toolkit - Activity Log",
        "description": "Paginated log of all toolkit DDL and data-change activities.",
    },
    {
        "name": "Toolkit - Page Defs",
        "description": (
            "Dynamic admin page definitions stored as JSONB. "
            "Each page def references toolkit table codes and field codes; "
            "the /resolve endpoint merges in multilingual labels, field types, "
            "and option values from toolkit_fields and toolkit_field_options."
        ),
    },
    {
        "name": "API Bridge - Resources",
        "description": (
            "Manage external API resource connections (credentials, base URLs, auth config). "
            "Supports `none | bearer | basic | api_key | oauth2` authentication types."
        ),
    },
    {
        "name": "Gateway",
        "description": (
            "Build and manage dynamic API endpoints. "
            "Endpoints are configured in the database and served at runtime under `/run/{path}`. "
            "Includes schema browser for column introspection and a self-describing OpenAPI spec "
            "at `GET /api/v1/gateway/openapi.json`."
        ),
    },
    {
        "name": "Gateway Runtime",
        "description": (
            "Serve the gateway-defined endpoints. Routes match active endpoint records by path and method. "
            "`GET` — query rows; `POST` — insert (or configured operation); "
            "`PATCH` — partial update; `DELETE` — remove rows."
        ),
    },
    {
        "name": "Push Destinations",
        "description": (
            "Manage push destination configurations for exporting data to external systems. "
            "Supports Azure Blob, Email, S3-compatible, RabbitMQ, SFTP, and HTTP API. "
            "Secrets (passwords, tokens) are stored encrypted and never returned by the API."
        ),
    },
    {
        "name": "API Engine",
        "description": (
            "Metadata-driven CRUD engine. Objects and fields are registered in "
            "`toolkit.api_objects` / `toolkit.api_fields` (use `SELECT toolkit.fn_register_table(schema, table)` "
            "to bootstrap from an existing table). "
            "All actions support a rich filter DSL, sorting, pagination, i18n, and JSONB merge. "
            "\n\n"
            "**URL pattern:** `GET|POST /api/v1/{pg_schema}/{table}/{action}`  \n"
            "**Actions:** `list`, `get`, `insert`, `update`, `delete`  \n"
            "**Auth:** optional — no token resolves to `reader` role; "
            "restricted actions require a valid admin Bearer token."
        ),
    },
]


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance.

    Returns:
        Configured FastAPI application with CORS middleware and all routers.
    """
    application = FastAPI(
        title="GMC E-Commerce Platform",
        version=API_VERSION,
        description=(
            "## GMC E-Commerce Platform — Admin API\n\n"
            "All endpoints (except Gateway Runtime and API Engine) follow the **all-POST** convention "
            "and require an `Authorization: Bearer <token>` header obtained from `/api/v1/auth/admin/login`.\n\n"
            "### Base URL\n"
            "`/api/v1`\n\n"
            "### Authentication\n"
            "- **Admin routes** — Bearer JWT with role `admin | local_admin | writer | reader`\n"
            "- **API Engine** — Bearer JWT optional; unauthenticated requests receive `reader` role\n"
            "- **Gateway Runtime** — configured per-endpoint (none / bearer / api-key)\n\n"
            "### Response envelope (admin routes)\n"
            "```json\n"
            "{\"success\": true, \"data\": ..., \"error\": null}\n"
            "```\n\n"
            "### Response envelope (API Engine)\n"
            "```json\n"
            "{\"ok\": true, \"rows\": [...], \"total\": 0}\n"
            "```\n\n"
            "### Registering tables for the API Engine\n"
            "```sql\n"
            "SELECT toolkit.fn_register_table('my_schema', 'my_table');\n"
            "-- then call: GET /api/v1/my_schema/my_table/list\n"
            "```"
        ),
        openapi_tags=_OPENAPI_TAGS,
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in _get_routers():
        application.include_router(router, prefix=API_V1_PREFIX)

    # Engine catch-all routes under /api/v1
    application.include_router(engine_router, prefix=API_V1_PREFIX)

    @application.post("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "ok", "version": API_VERSION}

    return application


app = create_app()
