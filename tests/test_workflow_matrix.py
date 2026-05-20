import pytest

from src.orchestrator.workflow import (
    StepStatus,
    WorkflowLifecycleError,
    WorkflowManager,
    WorkflowMatrixError,
    WorkflowStep,
)


def noop():
    return "ok"


def test_rejects_oversized_matrix_before_registration():
    workflow = WorkflowManager().create_workflow(
        "deploy",
        max_matrix_expansion=3,
    )
    step = WorkflowStep(
        "fanout",
        noop,
        matrix={
            "region": ["us", "eu"],
            "runtime": ["py311", "py312"],
        },
    )

    with pytest.raises(WorkflowMatrixError):
        workflow.add_step(step)

    assert workflow.status is StepStatus.PENDING
    assert workflow.steps == []
    assert workflow.get_step(step.id) is None
    assert (
        workflow.audit_log[-1]["reason"]
        == "matrix_expansion_limit_exceeded"
    )
    assert workflow.audit_log[-1]["details"] == {
        "dimension_count": 2,
        "expansion_count": 4,
        "limit": 3,
    }


def test_registered_matrix_is_immutable_snapshot():
    workflow = WorkflowManager().create_workflow(
        "deploy",
        max_matrix_expansion=4,
    )
    regions = ["us", "eu"]
    step = WorkflowStep(
        "fanout",
        noop,
        matrix={
            "region": regions,
            "runtime": ["py311", "py312"],
        },
    )

    workflow.add_step(step)
    regions.append("apac")

    assert workflow.expand_matrix(step) == [
        {"region": "us", "runtime": "py311"},
        {"region": "us", "runtime": "py312"},
        {"region": "eu", "runtime": "py311"},
        {"region": "eu", "runtime": "py312"},
    ]
    with pytest.raises(TypeError):
        step.matrix["region"] = ("apac",)


def test_late_matrix_mutation_is_rejected_before_dispatch():
    manager = WorkflowManager()
    workflow = manager.create_workflow("deploy", max_matrix_expansion=4)
    step = WorkflowStep(
        "fanout",
        noop,
        matrix={
            "region": ["us"],
            "runtime": ["py311"],
        },
    )
    workflow.add_step(step)
    step.matrix = {
        "region": ["us", "eu", "apac"],
        "runtime": ["py311", "py312"],
    }

    assert not manager.execute_workflow(workflow.id)
    assert workflow.status is StepStatus.PENDING
    assert step.status is StepStatus.PENDING
    assert (
        workflow.audit_log[-1]["reason"]
        == "matrix_expansion_limit_exceeded"
    )
    assert workflow.audit_log[-1]["details"] == {
        "dimension_count": 2,
        "expansion_count": 6,
        "limit": 4,
    }


def test_duplicate_matrix_values_are_rejected_without_value_logging():
    workflow = WorkflowManager().create_workflow("deploy")
    step = WorkflowStep(
        "fanout",
        noop,
        matrix={"region": ["us", "us"]},
    )

    with pytest.raises(WorkflowMatrixError):
        workflow.add_step(step)

    assert workflow.steps == []
    assert workflow.audit_log[-1] == {
        "event": "workflow_step_rejected",
        "workflow_id": workflow.id,
        "step_id": step.id,
        "step_name": "fanout",
        "reason": "duplicate_matrix_dimension_values",
        "details": {"dimension": "region"},
    }


def test_rejects_graph_changes_after_execution_started():
    workflow = WorkflowManager().create_workflow("deploy")
    workflow.status = StepStatus.RUNNING

    with pytest.raises(WorkflowLifecycleError):
        workflow.add_step(WorkflowStep("late-fanout", noop))

    assert workflow.steps == []
    assert workflow.audit_log[-1]["reason"] == "workflow_not_pending"
