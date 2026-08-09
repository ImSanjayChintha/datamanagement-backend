-- ════════════════════════════════════════════════════════════════════════════
-- seed_pim_attributes.sql  —  PIM attribute selectable values
--
-- Safe to re-run: uses UPDATE … WHERE code = '…'
-- Run manually in psql / pgAdmin after schema.sql has been applied.
-- ════════════════════════════════════════════════════════════════════════════

-- cage_type — bearing cage material
UPDATE pim.attributes
SET    options = '[
  {"code": "steel",            "label": {"en": "Steel",            "fr": "Acier",            "de": "Stahl",            "es": "Acero",            "it": "Acciaio"}},
  {"code": "steel_riveted",    "label": {"en": "Steel Riveted",    "fr": "Acier Riveté",     "de": "Stahl Genietet",   "es": "Acero Remachado",  "it": "Acciaio Rivettato"}},
  {"code": "stainless",        "label": {"en": "Stainless Steel",  "fr": "Inox",             "de": "Edelstahl",        "es": "Acero Inoxidable", "it": "Acciaio Inox"}},
  {"code": "brass",            "label": {"en": "Brass",            "fr": "Laiton",           "de": "Messing",          "es": "Latón",            "it": "Ottone"}},
  {"code": "polyamide",        "label": {"en": "Polyamide",        "fr": "Polyamide",        "de": "Polyamid",         "es": "Poliamida",        "it": "Poliammide"}},
  {"code": "polyamide_gf",     "label": {"en": "Polyamide GF",     "fr": "Polyamide GF",     "de": "Polyamid GF",      "es": "Poliamida GF",     "it": "Poliammide GF"}},
  {"code": "phenolic",         "label": {"en": "Phenolic",         "fr": "Phénolique",       "de": "Phenolharz",       "es": "Fenólico",         "it": "Fenolico"}},
  {"code": "laminated",        "label": {"en": "Laminated",        "fr": "Lamifié",          "de": "Laminiert",        "es": "Laminado",         "it": "Laminato"}},
  {"code": "plastic",          "label": {"en": "Plastic",          "fr": "Plastique",        "de": "Kunststoff",       "es": "Plástico",         "it": "Plastica"}},
  {"code": "full_complement",  "label": {"en": "Full Complement",  "fr": "Plein de Billes",  "de": "Vollkugelig",      "es": "Complemento Completo", "it": "Pieno Complemento"}}
]'::jsonb
WHERE  code = 'cage_type';
