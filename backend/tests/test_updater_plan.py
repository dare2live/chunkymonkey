from routers.updater_plan import (
    selected_dependency_ids,
    selected_step_specs,
    skipped_step_ids_outside,
    step_ids_for,
    step_index_for,
    step_name_for,
    step_specs_for_group,
)


SAMPLE_STEPS = [
    {"id": "sync_raw", "name": "Raw", "group": "data", "order": 1},
    {"id": "match_inst", "name": "Match", "group": "data", "order": 2},
    {"id": "build_profiles", "name": "Profiles", "group": "mart", "order": 10},
]


def test_step_ids_and_index_preserve_execution_metadata():
    index = step_index_for(SAMPLE_STEPS)

    assert step_ids_for(SAMPLE_STEPS) == ["sync_raw", "match_inst", "build_profiles"]
    assert index["match_inst"]["name"] == "Match"
    assert step_name_for(index, "match_inst") == "Match"
    assert step_name_for(index, "missing_step") == "missing_step"


def test_step_specs_for_group_preserves_order():
    assert step_specs_for_group(SAMPLE_STEPS, "data") == SAMPLE_STEPS[:2]
    assert step_specs_for_group(SAMPLE_STEPS, "missing") == []


def test_selected_step_specs_preserves_global_order():
    assert selected_step_specs(SAMPLE_STEPS, {"build_profiles", "sync_raw"}) == [
        SAMPLE_STEPS[0],
        SAMPLE_STEPS[2],
    ]


def test_selected_dependency_ids_keeps_only_selected_dependencies_in_order():
    assert selected_dependency_ids(
        ["sync_calendar", "sync_raw", "sync_market_data"],
        {"sync_raw", "sync_market_data"},
    ) == ["sync_raw", "sync_market_data"]


def test_skipped_step_ids_outside_preserves_step_order():
    assert skipped_step_ids_outside(SAMPLE_STEPS, {"match_inst"}) == [
        "sync_raw",
        "build_profiles",
    ]
