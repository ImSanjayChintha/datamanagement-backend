"""
Package: dbtoolkit.core.ai
Purpose: AI-assisted code generation, translation, and schema improvement
         powered by Anthropic Claude models.

Sub-modules:
  client         — HTTP client for the Anthropic Messages API.
  prompts        — Load and build system prompts from ai_config/prompts.json.
  schema_context — Fetch and format DB schema context for AI prompts.
  generator      — Generate tables, views, and functions from natural language.
  translator     — Translate field labels and option values to multiple languages.
  suggester      — Suggest improvements to existing table definitions.
"""
