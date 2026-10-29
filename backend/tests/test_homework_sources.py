# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which note fields homework is read from, and how a list item becomes a task.

The failures these catch: a type the clinician actually uses (DAP, BIRP,
GIRP, the psychiatric ones) silently proposing nothing because only SOAP was
wired; a draft's "None assigned." becoming an assignment the client sees;
"Client will ..." reaching the portal in the third person; and an item's
index shifting when a placeholder sits before it, which would let a second
read propose the same item again.
"""

from __future__ import annotations

from dataclasses import replace

from app.homework.sources import (
    HOMEWORK_SOURCES,
    HomeworkSource,
    as_task,
    list_items,
    sources_for,
    sources_for_definition,
    text_of,
)
from app.notes.registry import BasedOn, get_default_registry

_LIST = HomeworkSource("plan", "homework_assignments", "list")


def test_every_source_names_a_field_its_type_actually_has() -> None:
    """A renamed field in a note type would otherwise read as "no homework" forever."""
    registry = get_default_registry()
    for type_key, sources in HOMEWORK_SOURCES.items():
        definition = registry.get(type_key)
        fields = {f"{s.key}.{f.key}" for s in definition.sections for f in s.fields}
        for source in sources:
            assert source.key in fields, f"{type_key} has no field {source.key}"


def test_psychiatric_types_are_read_not_only_soap() -> None:
    for key in ("soap", "dap", "birp", "girp", "psychiatric_follow_up", "psychiatric_evaluation"):
        assert sources_for(key), key
    assert all(s.shape == "text" for s in sources_for("psychiatric_follow_up"))


def test_psychotherapy_and_patient_context_types_are_never_read() -> None:
    for key in ("psychotherapy", "intake", "treatment_plan", "safety_plan", "narrative"):
        assert sources_for(key) == ()


def test_list_items_drop_placeholders_and_keep_their_stored_index() -> None:
    content = {
        "plan": {
            "homework_assignments": [
                {"text": "None assigned."},
                {"text": "Client will complete two thought records."},
                "",
                {"text": "Practice paced breathing before bed."},
            ]
        }
    }
    items = list_items(content, _LIST)
    assert [(i.item_index, i.text) for i in items] == [
        (1, "Complete two thought records."),
        (3, "Practice paced breathing before bed."),
    ]
    assert items[0].quote == "Client will complete two thought records."
    assert items[0].source_field == "plan.homework_assignments"


def test_a_single_string_field_is_one_item() -> None:
    content = {"plan": {"homework_assignments": "Read chapter 2 of the workbook."}}
    assert [i.text for i in list_items(content, _LIST)] == ["Read chapter 2 of the workbook."]


def test_an_older_flat_plan_proposes_nothing_rather_than_guessing() -> None:
    assert list_items({"plan": "Continue weekly sessions; practise breathing."}, _LIST) == []


def test_leading_subject_comes_off_in_its_common_forms() -> None:
    assert as_task("The client agreed to log panic episodes") == "Log panic episodes"
    assert as_task("Pt to walk 20 minutes daily.") == "Walk 20 minutes daily."
    assert as_task("Patient was encouraged to journal.") == "Journal."
    # Not a subject lead: kept as written.
    assert as_task("Clients' group on Thursday") == "Clients' group on Thursday"


def test_text_of_joins_list_parts_one_per_line_and_skips_placeholders() -> None:
    source = HomeworkSource("treatment_plan", "plan_items", "text")
    content = {"treatment_plan": {"plan_items": [{"text": "Start sertraline."}, "Not discussed."]}}
    assert text_of(content, source) == "Start sertraline."


def test_a_practice_type_reads_its_base_or_a_homework_list_it_has() -> None:
    soap = get_default_registry().get("soap")
    based = replace(soap, key="custom.mine", based_on=BasedOn("birp", "BIRP", 0, 0))
    assert sources_for_definition(based) == HOMEWORK_SOURCES["birp"]
    built = replace(soap, key="custom.scratch", based_on=None)
    assert sources_for_definition(built) == (
        HomeworkSource("plan", "homework_assignments", "list"),
    )
    assert sources_for_definition(None) == ()
