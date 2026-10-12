"""Piper voices the app knows about.

Only voices whose files are actually present in the voices folder show up in
the dropdown, so it is safe for this list to be longer than what you install.
Install extra ones by setting PIPER_VOICES (comma separated) in the Render
build environment, e.g.  PIPER_VOICES=pt_BR-faber-medium,es_ES-davefx-medium
"""

CATALOG = [
    {"id": "pt_BR-faber-medium", "label": "Faber (Portuguese/Brazil)"},
    {"id": "pt_BR-cadu-medium", "label": "Cadu (Portuguese/Brazil)"},
    {"id": "pt_BR-jeff-medium", "label": "Jeff (Portuguese/Brazil)"},
    {"id": "en_US-lessac-medium", "label": "Lessac (US)"},
    {"id": "en_US-amy-medium", "label": "Amy (US)"},
    {"id": "en_US-ryan-medium", "label": "Ryan (US)"},
    {"id": "en_GB-alba-medium", "label": "Alba (UK)"},
    {"id": "en_GB-alan-medium", "label": "Alan (UK)"},
    {"id": "es_ES-davefx-medium", "label": "Davefx (Spanish)"},
    {"id": "fr_FR-siwis-medium", "label": "Siwis (French)"},
    {"id": "de_DE-thorsten-medium", "label": "Thorsten (German)"},
    {"id": "it_IT-paola-medium", "label": "Paola (Italian)"},
]

# Downloaded at build time unless PIPER_VOICES overrides it. Each medium voice
# is roughly 60 MB.
DEFAULT_DOWNLOAD = [
    "pt_BR-faber-medium",
    "pt_BR-cadu-medium",
    "en_US-lessac-medium",
    "en_US-amy-medium",
    "en_GB-alba-medium",
]
