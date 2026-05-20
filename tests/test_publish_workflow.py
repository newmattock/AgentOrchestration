from pathlib import Path

import yaml


WORKFLOW_PATH = Path(".github/workflows/publish-package.yml")


def _load_workflow():
    with WORKFLOW_PATH.open() as file:
        return yaml.safe_load(file)


def _workflow_on(workflow):
    return workflow.get("on") or workflow.get(True)


def _steps(workflow):
    return workflow["jobs"]["publish"]["steps"]


def test_publish_workflow_exists():
    assert WORKFLOW_PATH.exists()


def test_manual_dispatch_has_no_ref_override_input():
    workflow_on = _workflow_on(_load_workflow())
    dispatch_inputs = workflow_on["workflow_dispatch"]["inputs"]

    assert "version" in dispatch_inputs
    assert "dry_run" in dispatch_inputs
    forbidden_inputs = {"ref", "source_ref", "branch", "tag", "protected_ref"}
    assert forbidden_inputs.isdisjoint(dispatch_inputs)


def test_publish_ref_guard_runs_before_registry_authentication():
    steps = _steps(_load_workflow())
    guard_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("id") == "ref_guard"
    )
    publish_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Publish package"
    )

    assert guard_index < publish_index
    publish_step = steps[publish_index]
    assert "UV_PUBLISH_TOKEN" not in steps[guard_index].get("env", {})
    assert (
        "secrets.PYPI_API_TOKEN"
        in publish_step["env"]["UV_PUBLISH_TOKEN"]
    )
    assert (
        "steps.ref_guard.outputs.publish_allowed == 'true'"
        in publish_step["if"]
    )


def test_guard_uses_actual_github_ref_context_not_dispatch_inputs():
    guard = next(
        step
        for step in _steps(_load_workflow())
        if step.get("id") == "ref_guard"
    )

    assert guard["env"]["ACTUAL_REF_NAME"] == "${{ github.ref_name }}"
    assert guard["env"]["ACTUAL_REF_TYPE"] == "${{ github.ref_type }}"
    assert (
        guard["env"]["ACTUAL_REF_PROTECTED"]
        == "${{ github.ref_protected }}"
    )
    assert "${{ inputs.version }}" not in guard["run"]


def test_guard_accepts_only_release_branches_and_verified_signed_tags():
    guard_script = next(
        step["run"]
        for step in _steps(_load_workflow())
        if step.get("id") == "ref_guard"
    )

    assert '[ "${ACTUAL_REF_TYPE}" = "branch" ]' in guard_script
    assert '[ "${ACTUAL_REF_PROTECTED}" != "true" ]' in guard_script
    assert 'main|release/*)' in guard_script
    assert (
        "Package publishing branches must be main or release/*."
        in guard_script
    )
    assert "publish_ref_kind=protected-branch" in guard_script

    assert '[ "${ACTUAL_REF_TYPE}" = "tag" ]' in guard_script
    assert "vMAJOR.MINOR.PATCH release tag policy" in guard_script
    assert "/git/ref/tags/${ACTUAL_REF_NAME}" in guard_script
    assert "/git/tags/${tag_object_sha}" in guard_script
    assert ".verification.verified" in guard_script
    assert "publish_ref_kind=signed-tag" in guard_script


def test_every_post_guard_step_is_conditioned_on_guard_success():
    workflow = _load_workflow()
    steps = _steps(workflow)
    guard_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("id") == "ref_guard"
    )

    for step in steps[guard_index + 1:]:
        assert (
            "steps.ref_guard.outputs.publish_allowed == 'true'"
            in step["if"]
        )
