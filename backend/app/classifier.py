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

# Nouns that mark a line as mechanical/electrical or public-health services. A DSR pipe
# item names the concrete bedding it is laid in ("S&S centrifugally cast iron pipes laid in
# cement concrete 1:5:10"), so the generic Concrete rule is guarded against these: the
# concrete is the surround, not the work being measured. Excavation, Piling, Waterproofing
# and the rest keep priority over services, because "excavate a trench for a drain" is
# earthwork and the rule order must still settle that first.
SERVICES_NOUNS = (
    r"pipe|pipes|pipework|piping|conduit|trunking|containment|cable|cables|wiring|"
    r"casing capping|casing-capping|cable tray|"
    r"sanitary|drainage|soil waste|water supply|manhole|inspection chamber|"
    r"fitting|fittings|valve|tap|cistern|wash basin|water closet|urinal|sink|shower|"
    r"water meter|pump|fan|luminaire|switch|socket|distribution board"
)

# (regex, smm2_section, human readable rule). Order is significant.
CLASSIFICATION_RULES: tuple[tuple[str, str, str], ...] = (
    (r"concrete|grade \d+", "Concrete", "concrete OR grade <number>"),
    (r"rebar|reinforcement|steel bar", "Reinforcement", "rebar OR reinforcement OR steel bar"),
    (r"formwork|shutter", "Formwork", "formwork OR shutter"),
    (r"brick|blockwork|\bblocks?\b|masonry", "Masonry", "brick OR block OR blockwork OR masonry"),
    (r"plaster|render|screed", "Plaster", "plaster OR render OR screed"),
    (r"excavate|excavation|dig", "Excavation", "excavate OR excavation OR dig"),
    (r"pile|piling|bored", "Piling", "pile OR piling OR bored"),
    (r"waterproof|membrane", "Waterproofing", "waterproof OR membrane"),
    (
        rf"conduit|containment|trunking|casing capping|casing-capping|cable tray|cable|"
        rf"pipe|pipes|pipework|sanitary|drainage|soil waste|water supply|manhole|"
        rf"fitting|fittings|valve|cistern|wash basin|water closet|urinal",
        "M&E Containment",
        "conduit OR containment OR trunking OR pipework OR a sanitary or drainage fitting",
    ),
    (r"preliminar|site setup|insurance", "Preliminaries", "preliminar OR site setup OR insurance"),
)

# A section whose rule must stand down when the line names something more specific.
#
# Concrete is the case that matters, and it is the same problem three times over: the
# generic rule reads "concrete|grade N" anywhere in the sentence, and in real schedules the
# word turns up as the SURROUND or the SUBSTRATE of work that belongs to another section.
#
#   "S&S centrifugally cast iron pipes laid in cement concrete 1:5:10"   -> M&E, not Concrete
#   "Timber formwork to in-situ concrete including strutting"            -> Formwork, not Concrete
#   "Common clay brick laid in cement mortar 1:4"                        -> Masonry, not Concrete
#
# Without the guard, 220 of the India services lines the rate library covers, all 10
# Singapore formwork lines and 4 of its masonry lines classified as Concrete and could
# never be benchmarked against the section they were measured from.
#
# The guard stands a rule down and lets the search continue down the table, so a line that
# genuinely IS concrete work is unaffected: nothing else in the table claims it, and it
# falls back to Concrete.
#
# Expressed as a guard rather than as a pattern prefix on purpose: a regex would have to be
# anchored to stop re.search walking past the lookahead, and anchoring the pattern then
# stops the keyword matching anywhere but position 0. The guard is therefore applied in the
# match loop, where the rule order is already explicit.
SECTION_GUARDS: dict[str, tuple[str, str]] = {
    "Concrete": (
        r"\b(?:pipe|pipes|pipework|piping|conduit|trunking|containment|cable|cables|wiring|"
        r"casing capping|casing-capping|cable tray|sanitary|drainage|soil waste|water supply|"
        r"manhole|inspection chamber|fitting|fittings|valve|tap|cistern|wash basin|"
        r"water closet|urinal|sink|shower|luminaire|distribution board|"
        r"formwork|shuttering|brick|bricks|brickwork|blocks?|blockwork|masonry)\b",
        "not applied when the line names pipework, cable, a sanitary fitting, formwork or masonry",
    ),
}

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

# Compiled once; see SECTION_GUARDS for why the guard is not part of the rule pattern.
_GUARD_RE = re.compile("|".join(pattern for pattern, _ in SECTION_GUARDS.values()), re.IGNORECASE)

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
            pattern = f"(?:{pattern})|(?:{extra})"
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
    # 1. The leading phrase, when the description has one. Both standards lead with the
    #    trade heading - "Timber Formwork: Timber formwork to in-situ concrete ..." - and a
    #    keyword in the heading is far stronger evidence than one buried in the detail,
    #    where it is usually the surround or the substrate rather than the work itself.
    #    Without this pass, "Clay Bricks: ... including reinforcement" classified as
    #    Reinforcement and "Timber Formwork: ... in-situ concrete" as Concrete.
    if ":" in text:
        head = text.split(":", 1)[0]
        found = _first_match(head, head, country)
        if found is not None:
            return found
    # 2. The whole description, against the same ordered table.
    found = _first_match(text, text, country)
    if found is not None:
        return found
    return Classification(UNCLASSIFIED, None, None)


def _first_match(needle: str, guard_text: str, country: str) -> Classification | None:
    """First rule in the table that matches, skipping any rule its guard stands down."""
    for pattern, section, human in _compiled(country):
        match = pattern.search(needle)
        if not match:
            continue
        guard = SECTION_GUARDS.get(section)
        if guard and _GUARD_RE.search(guard_text):
            # The line names something more specific than this section, so the rule stands
            # down and the search continues down the table. See SECTION_GUARDS.
            continue
        return Classification(section, human, match.group(0))
    return None


def rules_as_dicts(country: str = DEFAULT_COUNTRY) -> list[dict[str, str]]:
    """Expose the effective rule table so the API and UI can document it."""
    registry = get_country(country)
    return [
        {
            "pattern": pattern.pattern,
            "smm2_section": section,
            "rule": (
                f"{human} ({SECTION_GUARDS[section][1]})"
                if section in SECTION_GUARDS
                else human
            ),
            "classification_standard": registry.measurement_standard,
        }
        for pattern, section, human in _compiled(country)
    ]


def sections_for(country: str = DEFAULT_COUNTRY) -> tuple[str, ...]:
    return SMM2_SECTIONS
