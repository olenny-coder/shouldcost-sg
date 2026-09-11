"""Raw BoQ description to measurement-section classifier.

Two measurement standards are supported because the two markets use different
documents for the same purpose:

  SG  SMM2 - Standard Method of Measurement of Building Works, 2nd Edition.
  IN  IS 1200 (BIS, Methods of Measurement of Building and Civil Engineering
      Works) together with the CPWD Delhi Schedule of Rates chapter structure.

Both standards partition building work into the same ten canonical sections, so
the section vocabulary is shared; only the VOCABULARY OF THE DESCRIPTIONS
differs. Indian BoQs say "shuttering" not "formwork", "M25" not "grade 35/20",
"TMT" not "high yield deformed bar", "earthwork in excavation" not "excavation to
reduce level". Country-specific patterns are therefore layered on top of the
shared rule table, and the SG rule set is unchanged.

The rule set is deliberately simple and fully explainable: a case-insensitive
keyword match evaluated in a fixed order, first match wins. The rule that fired
is returned alongside the section so the UI can show exactly why a line was
classified as it was, and the user can override the result with
PATCH /api/boq/item/{item_id}.

Known caveat of first-match-wins ordering: "Waterproof membrane to pile cap tops"
classifies as Piling, because the Piling rule precedes the Waterproofing rule.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from .countries import DEFAULT_COUNTRY, get_country

UNCLASSIFIED = "Unclassified"

# (regex, smm2_section, human readable rule). Order is significant.
CLASSIFICATION_RULES: tuple[tuple[str, str, str], ...] = (
    (r"concrete|grade \d+", "Concrete", "concrete OR grade <number>"),
    (r"rebar|reinforcement|steel bar", "Reinforcement", "rebar OR reinforcement OR steel bar"),
    (r"formwork|shutter", "Formwork", "formwork OR shutter"),
    (r"brick|blockwork|masonry", "Masonry", "brick OR blockwork OR masonry"),
    (r"plaster|render|screed", "Plaster", "plaster OR render OR screed"),
    (r"excavate|excavation|dig", "Excavation", "excavate OR excavation OR dig"),
    (r"pile|piling|bored", "Piling", "pile OR piling OR bored"),
    (r"waterproof|membrane", "Waterproofing", "waterproof OR membrane"),
    (r"conduit|containment|trunking", "M&E Containment", "conduit OR containment OR trunking"),
    (r"preliminar|site setup|insurance", "Preliminaries", "preliminar OR site setup OR insurance"),
)

# Extra patterns layered on top of the shared table, keyed by country and
# canonical section. Anything not listed uses the shared table unchanged.
COUNTRY_PATTERNS: dict[str, dict[str, str]] = {
    "SG": {},
    "IN": {
        "Concrete": r"rcc|pcc|reinforced cement concrete|plain cement concrete|\b[Mm]\d{2}\b",
        "Reinforcement": r"tmt|tor steel|hysd|fe500|fe415",
        "Formwork": r"shuttering",
        "Excavation": r"earthwork|earth work|jungle clearance",
        "Masonry": r"aac block|fly ash brick|burnt clay",
        "Plaster": r"rendering",
        "Waterproofing": r"bituminous|app membrane|damp proof",
        "M&E Containment": r"casing capping|casing-capping|cable tray",
        "Preliminaries": r"work charged|contingency|site establishment",
    },
}

SMM2_SECTIONS: tuple[str, ...] = tuple(section for _, section, _ in CLASSIFICATION_RULES)

_CACHE: dict[str, tuple[tuple[re.Pattern[str], str, str], ...]] = {}
_LOCK = threading.Lock()


def _compiled(country: str) -> tuple[tuple[re.Pattern[str], str, str], ...]:
    key = (country or DEFAULT_COUNTRY).strip().upper()
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    extras = COUNTRY_PATTERNS.get(key, {})
    built = []
    for pattern, section, human in CLASSIFICATION_RULES:
        extra = extras.get(section)
        if extra:
            pattern = f"{pattern}|{extra}"
            human = f"{human} OR {extra}"
        built.append((re.compile(pattern, re.IGNORECASE), section, human))
    result = tuple(built)
    with _LOCK:
        _CACHE[key] = result
    return result


@dataclass(frozen=True)
class Classification:
    """Outcome of classifying one raw description."""

    smm2_section: str
    rule: str | None
    matched_text: str | None

    @property
    def is_unclassified(self) -> bool:
        return self.smm2_section == UNCLASSIFIED


def classify(description: str | None, country: str = DEFAULT_COUNTRY) -> Classification:
    """Classify a raw BoQ description into a canonical measurement section.

    Unmatched descriptions return "Unclassified" and are surfaced in the UI for
    manual reclassification rather than being silently bucketed.
    """
    text = (description or "").strip()
    if not text:
        return Classification(UNCLASSIFIED, None, None)
    for pattern, section, human in _compiled(country):
        match = pattern.search(text)
        if match:
            return Classification(section, human, match.group(0))
    return Classification(UNCLASSIFIED, None, None)


def rules_as_dicts(country: str = DEFAULT_COUNTRY) -> list[dict[str, str]]:
    """Expose the effective rule table so the API and UI can document it."""
    registry = get_country(country)
    return [
        {
            "pattern": pattern.pattern,
            "smm2_section": section,
            "rule": human,
            "classification_standard": registry.measurement_standard,
        }
        for pattern, section, human in _compiled(country)
    ]


def sections_for(country: str = DEFAULT_COUNTRY) -> tuple[str, ...]:
    return SMM2_SECTIONS
