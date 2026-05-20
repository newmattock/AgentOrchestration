import threading

import pytest

from src.deploy.migrations import (
    MigrationDeclarationError,
    MigrationJob,
    MigrationRunner,
    SQLiteMigrationStateStore,
)


def _store(tmp_path):
    return SQLiteMigrationStateStore(str(tmp_path / "migrations.sqlite"))


def test_concurrent_or_retried_jobs_cannot_execute_same_migration_twice(
    tmp_path,
):
    store = _store(tmp_path)
    runner = MigrationRunner(store)
    started = threading.Event()
    release = threading.Event()
    executions = []
    results = []

    def slow_handler():
        executions.append("first")
        started.set()
        assert release.wait(timeout=1)
        return "ok"

    first = threading.Thread(
        target=lambda: results.append(
            runner.run(
                MigrationJob(
                    "20260520_add_accounts_index",
                    slow_handler,
                    single_run=True,
                ),
                owner="deploy-a",
            )
        )
    )

    first.start()
    assert started.wait(timeout=1)

    blocked = runner.run(
        MigrationJob(
            "20260520_add_accounts_index",
            lambda: executions.append("second"),
            single_run=True,
        ),
        owner="deploy-b",
    )

    release.set()
    first.join(timeout=1)

    retried = runner.run(
        MigrationJob(
            "20260520_add_accounts_index",
            lambda: executions.append("retry"),
            single_run=True,
        ),
        owner="deploy-b",
    )

    assert blocked.status == "locked"
    assert blocked.lock_owner == "deploy-a"
    assert retried.status == "skipped"
    assert retried.completion is not None
    assert executions == ["first"]
    assert results[0].status == "completed"


def test_logs_record_lock_owner_and_completion_status(tmp_path):
    store = _store(tmp_path)
    runner = MigrationRunner(store)

    result = runner.run(
        MigrationJob(
            "20260520_create_workspace_table",
            lambda: None,
            single_run=True,
        ),
        owner="deploy-prod-1",
    )

    logs = store.logs("20260520_create_workspace_table")

    assert result.status == "completed"
    assert any(
        entry.event == "lock_acquired"
        and entry.owner == "deploy-prod-1"
        and entry.lock_owner == "deploy-prod-1"
        and entry.status == "running"
        for entry in logs
    )
    assert any(
        entry.event == "completed"
        and entry.owner == "deploy-prod-1"
        and entry.lock_owner == "deploy-prod-1"
        and entry.status == "completed"
        for entry in logs
    )


def test_deployment_retries_resume_from_recorded_migration_state(tmp_path):
    database_path = str(tmp_path / "migrations.sqlite")
    store = SQLiteMigrationStateStore(database_path)
    runner = MigrationRunner(store)
    executions = []

    first = runner.run(
        MigrationJob(
            "20260520_backfill_agent_versions",
            lambda: executions.append("first"),
            single_run=True,
        ),
        owner="deploy-a",
    )
    store.close()

    resumed_store = SQLiteMigrationStateStore(database_path)
    resumed_runner = MigrationRunner(resumed_store)
    retried = resumed_runner.run(
        MigrationJob(
            "20260520_backfill_agent_versions",
            lambda: executions.append("retry"),
            single_run=True,
        ),
        owner="deploy-b",
    )

    reopened_store = SQLiteMigrationStateStore(database_path)
    reopened_runner = MigrationRunner(reopened_store)
    resumed = reopened_runner.run(
        MigrationJob(
            "20260520_backfill_agent_versions",
            lambda: executions.append("resumed"),
            single_run=True,
        ),
        owner="deploy-c",
    )

    assert first.ran
    assert retried.status == "skipped"
    assert retried.lock_owner == "deploy-a"
    assert resumed.status == "skipped"
    assert resumed.lock_owner == "deploy-a"
    assert executions == ["first"]


def test_deployment_batch_retries_skip_completed_migrations(tmp_path):
    database_path = str(tmp_path / "migrations.sqlite")
    store = SQLiteMigrationStateStore(database_path)
    runner = MigrationRunner(store)
    calls = []
    attempts = {"second": 0}

    def flaky_second():
        attempts["second"] += 1
        calls.append("second")
        if attempts["second"] == 1:
            raise RuntimeError("transient database timeout")

    first = MigrationJob(
        "20260520_create_accounts",
        lambda: calls.append("first"),
        single_run=True,
    )
    second = MigrationJob(
        "20260520_backfill_accounts",
        flaky_second,
        idempotent=True,
    )

    with pytest.raises(RuntimeError):
        runner.run_many([first, second], owner="deploy-a")

    store.close()
    retry_runner = MigrationRunner(SQLiteMigrationStateStore(database_path))
    retry_results = retry_runner.run_many([first, second], owner="deploy-b")

    assert [result.status for result in retry_results] == [
        "skipped",
        "completed",
    ]
    assert [result.ran for result in retry_results] == [False, True]
    assert retry_results[0].lock_owner == "deploy-a"
    assert calls == ["first", "second", "second"]


def test_migrations_must_declare_idempotent_or_single_run_behavior(tmp_path):
    store = _store(tmp_path)
    runner = MigrationRunner(store)
    calls = []

    with pytest.raises(MigrationDeclarationError):
        runner.run(
            MigrationJob(
                "20260520_missing_policy",
                lambda: calls.append("ran"),
            ),
            owner="deploy-a",
        )

    logs = store.logs("20260520_missing_policy")
    assert calls == []
    assert logs[0].event == "rejected"
    assert logs[0].status == "rejected"


def test_failure_releases_lock_without_recording_completion(tmp_path):
    store = _store(tmp_path)
    runner = MigrationRunner(store)
    attempts = []

    def fail_once():
        attempts.append("failed")
        raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        runner.run(
            MigrationJob(
                "20260520_retryable_migration",
                fail_once,
                idempotent=True,
            ),
            owner="deploy-a",
        )

    retry = runner.run(
        MigrationJob(
            "20260520_retryable_migration",
            lambda: attempts.append("retried"),
            idempotent=True,
        ),
        owner="deploy-b",
    )

    assert retry.status == "completed"
    assert retry.ran
    assert attempts == ["failed", "retried"]
    assert store.completion_for("20260520_retryable_migration") is not None
    assert store.failure_for("20260520_retryable_migration") is None


def test_failed_single_run_migration_is_rejected_on_retry(tmp_path):
    database_path = str(tmp_path / "migrations.sqlite")
    store = SQLiteMigrationStateStore(database_path)
    runner = MigrationRunner(store)
    attempts = []

    def fail_once():
        attempts.append("failed")
        raise RuntimeError("partially applied schema change")

    with pytest.raises(RuntimeError):
        runner.run(
            MigrationJob(
                "20260520_non_idempotent_index",
                fail_once,
                single_run=True,
            ),
            owner="deploy-a",
        )

    failure = store.failure_for("20260520_non_idempotent_index")
    assert failure is not None
    assert failure.owner == "deploy-a"
    assert failure.error_type == "RuntimeError"
    store.close()

    retry_store = SQLiteMigrationStateStore(database_path)
    retry_runner = MigrationRunner(retry_store)
    retry = retry_runner.run(
        MigrationJob(
            "20260520_non_idempotent_index",
            lambda: attempts.append("retried"),
            single_run=True,
        ),
        owner="deploy-b",
    )

    logs = retry_store.logs("20260520_non_idempotent_index")
    assert retry.status == "rejected"
    assert not retry.ran
    assert retry.failure is not None
    assert retry.failure.owner == "deploy-a"
    assert retry.lock_owner == "deploy-a"
    assert "cannot be retried" in retry.detail
    assert attempts == ["failed"]
    assert any(entry.event == "failed" for entry in logs)
    assert any(
        entry.event == "rejected"
        and "cannot be retried" in entry.detail
        for entry in logs
    )
