"""
Service loader — import this module once at application startup to register
all built-in service classes via their @register decorators.

To add a new service:
  1. Create a module under services/  (or any sub-package)
  2. Decorate the class with @register
  3. Add an import below
"""
# Built-in example services
import app.modules.api_bridge.services.examples.schema_stats     # noqa: F401
import app.modules.api_bridge.services.examples.price_calculator  # noqa: F401
