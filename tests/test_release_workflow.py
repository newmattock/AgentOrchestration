from pathlib import Path


WORKFLOW = Path(".github/workflows/release.yml")


def workflow_text():
    return WORKFLOW.read_text()


def test_release_signing_uses_tag_specific_concurrency_group():
    workflow = workflow_text()

    assert "group: release-signing-${{ needs.build.outputs.release-tag }}" in workflow


def test_release_signing_never_cancels_in_progress_jobs():
    workflow = workflow_text()

    assert "cancel-in-progress: false" in workflow


def test_release_summary_fails_when_signing_is_partial_or_missing():
    workflow = workflow_text()

    assert "if: ${{ always() }}" in workflow
    assert 'needs.sign.result }}" != "success"' in workflow
    assert '"$expected" -eq 0' in workflow
    assert '"$expected" -ne "$signed"' in workflow
    assert "Partial release signing detected" in workflow
