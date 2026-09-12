"""Classifier tests - keyword to SMM2 section mapping."""

from __future__ import annotations

import pytest

from app.classifier import CLASSIFICATION_RULES, SMM2_SECTIONS, UNCLASSIFIED, classify


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Grade 35/20 ready-mixed concrete to pile caps and ground beams", "Concrete"),
        ("Concrete blinding to bases", "Concrete"),
        ("High yield deformed steel reinforcement bars, cut, bent and fixed", "Reinforcement"),
        ("Rebar to slab, fixed", "Reinforcement"),
        ("Supply and fix steel bar reinforcement", "Reinforcement"),
        ("Sawn timber formwork to soffits of suspended slabs", "Formwork"),
        ("Metal shutter to wall", "Formwork"),
        ("Clay brickwork to external walls 225mm thick", "Masonry"),
        ("Cement sand blockwork to internal partitions", "Masonry"),
        ("Cement and sand plaster to internal walls, 20mm thick", "Plaster"),
        ("Render to external soffits", "Plaster"),
        ("Cement sand screed to floor, 50mm thick", "Plaster"),
        ("Excavation to reduce level, depth not exceeding 2m", "Excavation"),
        ("Dig trenches for services", "Excavation"),
        ("Bored piling 600mm diameter, including casing", "Piling"),
        ("Precast pile driven to set", "Piling"),
        ("Tanking membrane waterproofing to basement retaining walls", "Waterproofing"),
        ("Polymeric membrane waterproofing to roof slab", "Waterproofing"),
        ("PVC conduit and trunking containment to electrical services", "M&E Containment"),
        ("Cable trunking to riser", "M&E Containment"),
        ("Preliminaries including site setup, temporary works and insurance", "Preliminaries"),
        ("Insurance of the works", "Preliminaries"),
    ],
)
def test_keyword_mapping(description: str, expected: str) -> None:
    assert classify(description).smm2_section == expected


@pytest.mark.parametrize(
    "description",
    [
        "Provisional sum for landscape artwork and water features",
        "Metal railing and handrail to staircases, galvanised",
        "",
        "   ",
        None,
    ],
)
def test_unmatched_becomes_unclassified(description) -> None:
    result = classify(description)
    assert result.smm2_section == UNCLASSIFIED
    assert result.is_unclassified is True
    assert result.rule is None


def test_classification_is_case_insensitive() -> None:
    assert classify("FORMWORK TO SOFFITS").smm2_section == "Formwork"
    assert classify("grade 40/20 Concrete").smm2_section == "Concrete"


def test_rule_that_fired_is_reported_for_auditability() -> None:
    result = classify("Bored piling 600mm diameter")
    assert result.rule is not None
    assert result.matched_text is not None
    assert result.matched_text.lower() in {"pile", "piling", "bored"}


def test_first_match_wins_is_documented_caveat() -> None:
    """Piling precedes Waterproofing in the rule order, so this resolves to Piling.

    This is exactly why PATCH /api/boq/item/{item_id} exists. The behaviour is
    asserted so that changing the rule order is a deliberate act.
    """
    assert classify("Waterproof membrane to pile cap tops").smm2_section == "Piling"


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # A real schedule line leads with its trade heading, and a keyword in the heading
        # beats one buried in the detail - where it is usually the surround or the
        # substrate rather than the work being measured.
        ("Timber Formwork: Timber formwork to in-situ concrete including strutting", "Formwork"),
        ("Metal Formwork: Metal formwork to in-situ concrete with strutting", "Formwork"),
        ("Clay Bricks: Common Brickwork: Common clay brick laid in cement mortar", "Masonry"),
        ("Concrete Blocks: Hollow Concrete Blockwork: Hollow concrete block laid", "Masonry"),
        ("Reinforced Concrete: Reinforced concrete to any location - grade 25", "Concrete"),
        ("Lean/Mass Concrete: Lean or Mass concrete binding to any location - grade 15",
         "Concrete"),
        # A DSR pipe item names the concrete bedding it is laid in.
        ("Providing and laying ductile iron pipes 100mm dia laid in cement concrete",
         "M&E Containment"),
        ("Providing and fixing soil, waste and vent pipes (75 mm dia)", "M&E Containment"),
        # 12 mm cement plaster of mix 1:4 ... - the first colon is inside the mix ratio.
        ("12 mm cement plaster of mix 1:4 (1 cement : 4 fine sand)", "Plaster"),
    ],
)
def test_real_schedule_vocabulary(description: str, expected: str) -> None:
    """Wording taken from the BCA schedule and the CPWD DSR that the app must place correctly.

    Before this, the generic concrete rule captured all ten Singapore formwork lines, four of
    its masonry lines and 220 of the India services lines, none of which could then be
    benchmarked against the section they were measured from.
    """
    assert classify(description, "IN" if "DSR" in description else "SG").smm2_section == expected


def test_the_heading_pass_does_not_override_a_rule_that_should_win() -> None:
    """A guarded rule stands down, but the heading does not hand the line to a wrong section."""
    # "Concrete Blocks" would match the concrete rule on the heading alone; the guard lets
    # masonry claim it instead, which is what the schedule measured.
    assert classify("Concrete Blocks: Hollow Concrete Blockwork", "SG").smm2_section == "Masonry"
    # A line that genuinely is concrete work is unaffected: nothing else claims it.
    assert classify("Concrete: grade 35/20 to pile caps", "SG").smm2_section == "Concrete"
    # Excavation keeps priority over services, because a trench for a drain is earthwork.
    assert classify("Excavate trench for drainage pipe", "SG").smm2_section == "Excavation"


def test_rule_table_shape() -> None:
    assert len(CLASSIFICATION_RULES) == 10
    assert set(SMM2_SECTIONS) == {
        "Concrete", "Reinforcement", "Formwork", "Masonry", "Plaster",
        "Excavation", "Piling", "Waterproofing", "M&E Containment", "Preliminaries",
    }
