"""Safe migration execution for deployment retries."""

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol


class MigrationLockError(RuntimeError):
    """Raised when migration lock state is inconsistent."""


class MigrationAlreadyRunningError(MigrationLockError):
    """Raised when another deployment worker owns the migration lock."""


class MigrationDeclarationError(ValueError):
    """Raised when a migration lacks an explicit execution policy."""


@dataclass(frozen=True)
class MigrationJob:
    """Migration script plus the policy deployment retries must enforce."""

    identifier: str
    handler: Callable[[], Any]
    idempotent: bool = False
    single_run: bool = False

    def __post_init__(self) -> None:
        if not self.identifier or not self.identifier.strip():
            raise ValueError("migration identifier is required")
        if not callable(self.handler):
            raise TypeError("migration handler must be callable")


@dataclass(frozen=True)
class MigrationLock:
    migration_id: str
    owner: str
    acquired_at: float


@dataclass(frozen=True)
class MigrationCompletion:
    migration_id: str
    owner: str
    completed_at: float
    status: str = "completed"


@dataclass(frozen=True)
class MigrationBeginState:
    migration_id: str
    owner: str
    acquired: bool
    already_completed: bool = False
    lock_owner: Optional[str] = None
    completion: Optional[MigrationCompletion] = None


@dataclass(frozen=True)
class MigrationLogEntry:
    migration_id: str
    owner: str
    event: str
    status: str
    timestamp: float
    lock_owner: Optional[str] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class MigrationResult:
    migration_id: str
    owner: str
    status: str
    ran: bool
    completion: Optional[MigrationCompletion] = None
    lock_owner: Optional[str] = None
    value: Any = None


class MigrationStateStore(Protocol):
    def begin(self, migration_id: str, owner: str) -> MigrationBeginState:
        ...

    def complete(self, migration_id: str, owner: str) -> MigrationCompletion:
        ...

    def fail(self, migration_id: str, owner: str, error_type: str) -> None:
        ...

    def reject(self, migration_id: str, owner: str, detail: str) -> None:
        ...

    def completion_for(
        self,
        migration_id: str,
    ) -> Optional[MigrationCompletion]:
        ...

    def lock_for(self, migration_id: str) -> Optional[MigrationLock]:
        ...

    def logs(
        self,
        migration_id: Optional[str] = None,
    ) -> List[MigrationLogEntry]:
        ...


class InMemoryMigrationStateStore:
    """State-store implementation with database-like atomic operations."""

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock = clock or time.time
        self._mutex = RLock()
        self._locks: Dict[str, MigrationLock] = {}
        self._completions: Dict[str, MigrationCompletion] = {}
        self._logs: List[MigrationLogEntry] = []

    def begin(self, migration_id: str, owner: str) -> MigrationBeginState:
        with self._mutex:
            completion = self._completions.get(migration_id)
            if completion is not None:
                self._append_log(
                    migration_id,
                    owner,
                    event="resume",
                    status=completion.status,
                    lock_owner=completion.owner,
                )
                return MigrationBeginState(
                    migration_id=migration_id,
                    owner=owner,
                    acquired=False,
                    already_completed=True,
                    lock_owner=completion.owner,
                    completion=completion,
                )

            lock = self._locks.get(migration_id)
            if lock is not None:
                self._append_log(
                    migration_id,
                    owner,
                    event="lock_conflict",
                    status="locked",
                    lock_owner=lock.owner,
                )
                return MigrationBeginState(
                    migration_id=migration_id,
                    owner=owner,
                    acquired=False,
                    lock_owner=lock.owner,
                )

            self._locks[migration_id] = MigrationLock(
                migration_id=migration_id,
                owner=owner,
                acquired_at=self._clock(),
            )
            self._append_log(
                migration_id,
                owner,
                event="lock_acquired",
                status="running",
                lock_owner=owner,
            )
            return MigrationBeginState(
                migration_id=migration_id,
                owner=owner,
                acquired=True,
            )

    def complete(self, migration_id: str, owner: str) -> MigrationCompletion:
        with self._mutex:
            self._require_lock_owner(migration_id, owner)
            completion = MigrationCompletion(
                migration_id=migration_id,
                owner=owner,
                completed_at=self._clock(),
            )
            self._completions[migration_id] = completion
            self._locks.pop(migration_id, None)
            self._append_log(
                migration_id,
                owner,
                event="completed",
                status=completion.status,
                lock_owner=owner,
            )
            return completion

    def fail(self, migration_id: str, owner: str, error_type: str) -> None:
        with self._mutex:
            self._require_lock_owner(migration_id, owner)
            self._locks.pop(migration_id, None)
            self._append_log(
                migration_id,
                owner,
                event="failed",
                status="failed",
                lock_owner=owner,
                detail=error_type,
            )

    def reject(self, migration_id: str, owner: str, detail: str) -> None:
        with self._mutex:
            self._append_log(
                migration_id,
                owner,
                event="rejected",
                status="rejected",
                detail=detail,
            )

    def completion_for(
        self,
        migration_id: str,
    ) -> Optional[MigrationCompletion]:
        with self._mutex:
            return self._completions.get(migration_id)

    def lock_for(self, migration_id: str) -> Optional[MigrationLock]:
        with self._mutex:
            return self._locks.get(migration_id)

    def logs(
        self,
        migration_id: Optional[str] = None,
    ) -> List[MigrationLogEntry]:
        with self._mutex:
            if migration_id is None:
                return list(self._logs)
            return [
                entry
                for entry in self._logs
                if entry.migration_id == migration_id
            ]

    def _require_lock_owner(self, migration_id: str, owner: str) -> None:
        lock = self._locks.get(migration_id)
        if lock is None:
            raise MigrationLockError(
                f"migration {migration_id!r} is not locked"
            )
        if lock.owner != owner:
            raise MigrationLockError(
                f"migration {migration_id!r} is locked by {lock.owner!r}"
            )

    def _append_log(
        self,
        migration_id: str,
        owner: str,
        *,
        event: str,
        status: str,
        lock_owner: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        self._logs.append(
            MigrationLogEntry(
                migration_id=migration_id,
                owner=owner,
                event=event,
                status=status,
                lock_owner=lock_owner,
                detail=detail,
                timestamp=self._clock(),
            )
        )


class SQLiteMigrationStateStore:
    """SQLite-backed migration state store for deployment retry safety."""

    def __init__(
        self,
        database_path: str = ":memory:",
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.database_path = database_path
        self._clock = clock or time.time
        self._mutex = RLock()
        self._connection = sqlite3.connect(
            database_path,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def begin(self, migration_id: str, owner: str) -> MigrationBeginState:
        with self._transaction() as connection:
            completion = self._select_completion(connection, migration_id)
            if completion is not None:
                self._append_log(
                    connection,
                    migration_id,
                    owner,
                    event="resume",
                    status=completion.status,
                    lock_owner=completion.owner,
                )
                return MigrationBeginState(
                    migration_id=migration_id,
                    owner=owner,
                    acquired=False,
                    already_completed=True,
                    lock_owner=completion.owner,
                    completion=completion,
                )

            lock = self._select_lock(connection, migration_id)
            if lock is not None:
                self._append_log(
                    connection,
                    migration_id,
                    owner,
                    event="lock_conflict",
                    status="locked",
                    lock_owner=lock.owner,
                )
                return MigrationBeginState(
                    migration_id=migration_id,
                    owner=owner,
                    acquired=False,
                    lock_owner=lock.owner,
                )

            acquired_at = self._clock()
            connection.execute(
                """
                INSERT INTO migration_locks
                    (migration_id, owner, acquired_at)
                VALUES (?, ?, ?)
                """,
                (migration_id, owner, acquired_at),
            )
            self._append_log(
                connection,
                migration_id,
                owner,
                event="lock_acquired",
                status="running",
                lock_owner=owner,
            )
            return MigrationBeginState(
                migration_id=migration_id,
                owner=owner,
                acquired=True,
            )

    def complete(self, migration_id: str, owner: str) -> MigrationCompletion:
        with self._transaction() as connection:
            self._require_lock_owner(connection, migration_id, owner)
            completion = MigrationCompletion(
                migration_id=migration_id,
                owner=owner,
                completed_at=self._clock(),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO migration_completions
                    (migration_id, owner, completed_at, status)
                VALUES (?, ?, ?, ?)
                """,
                (
                    completion.migration_id,
                    completion.owner,
                    completion.completed_at,
                    completion.status,
                ),
            )
            connection.execute(
                "DELETE FROM migration_locks WHERE migration_id = ?",
                (migration_id,),
            )
            self._append_log(
                connection,
                migration_id,
                owner,
                event="completed",
                status=completion.status,
                lock_owner=owner,
            )
            return completion

    def fail(self, migration_id: str, owner: str, error_type: str) -> None:
        with self._transaction() as connection:
            self._require_lock_owner(connection, migration_id, owner)
            connection.execute(
                "DELETE FROM migration_locks WHERE migration_id = ?",
                (migration_id,),
            )
            self._append_log(
                connection,
                migration_id,
                owner,
                event="failed",
                status="failed",
                lock_owner=owner,
                detail=error_type,
            )

    def reject(self, migration_id: str, owner: str, detail: str) -> None:
        with self._transaction() as connection:
            self._append_log(
                connection,
                migration_id,
                owner,
                event="rejected",
                status="rejected",
                detail=detail,
            )

    def completion_for(
        self,
        migration_id: str,
    ) -> Optional[MigrationCompletion]:
        with self._mutex:
            return self._select_completion(self._connection, migration_id)

    def lock_for(self, migration_id: str) -> Optional[MigrationLock]:
        with self._mutex:
            return self._select_lock(self._connection, migration_id)

    def logs(
        self,
        migration_id: Optional[str] = None,
    ) -> List[MigrationLogEntry]:
        with self._mutex:
            if migration_id is None:
                rows = self._connection.execute(
                    """
                    SELECT migration_id, owner, event, status, timestamp,
                           lock_owner, detail
                    FROM migration_logs
                    ORDER BY id
                    """
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT migration_id, owner, event, status, timestamp,
                           lock_owner, detail
                    FROM migration_logs
                    WHERE migration_id = ?
                    ORDER BY id
                    """,
                    (migration_id,),
                ).fetchall()
            return [self._log_from_row(row) for row in rows]

    def close(self) -> None:
        with self._mutex:
            self._connection.close()

    def _initialize_schema(self) -> None:
        with self._mutex:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS migration_locks (
                    migration_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    acquired_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS migration_completions (
                    migration_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    completed_at REAL NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS migration_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    event TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    lock_owner TEXT,
                    detail TEXT
                );
                """
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._mutex:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def _require_lock_owner(
        self,
        connection: sqlite3.Connection,
        migration_id: str,
        owner: str,
    ) -> None:
        lock = self._select_lock(connection, migration_id)
        if lock is None:
            raise MigrationLockError(
                f"migration {migration_id!r} is not locked"
            )
        if lock.owner != owner:
            raise MigrationLockError(
                f"migration {migration_id!r} is locked by {lock.owner!r}"
            )

    def _append_log(
        self,
        connection: sqlite3.Connection,
        migration_id: str,
        owner: str,
        *,
        event: str,
        status: str,
        lock_owner: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO migration_logs
                (migration_id, owner, event, status, timestamp,
                 lock_owner, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                migration_id,
                owner,
                event,
                status,
                self._clock(),
                lock_owner,
                detail,
            ),
        )

    @staticmethod
    def _select_completion(
        connection: sqlite3.Connection,
        migration_id: str,
    ) -> Optional[MigrationCompletion]:
        row = connection.execute(
            """
            SELECT migration_id, owner, completed_at, status
            FROM migration_completions
            WHERE migration_id = ?
            """,
            (migration_id,),
        ).fetchone()
        if row is None:
            return None
        return MigrationCompletion(
            migration_id=row["migration_id"],
            owner=row["owner"],
            completed_at=row["completed_at"],
            status=row["status"],
        )

    @staticmethod
    def _select_lock(
        connection: sqlite3.Connection,
        migration_id: str,
    ) -> Optional[MigrationLock]:
        row = connection.execute(
            """
            SELECT migration_id, owner, acquired_at
            FROM migration_locks
            WHERE migration_id = ?
            """,
            (migration_id,),
        ).fetchone()
        if row is None:
            return None
        return MigrationLock(
            migration_id=row["migration_id"],
            owner=row["owner"],
            acquired_at=row["acquired_at"],
        )

    @staticmethod
    def _log_from_row(row: sqlite3.Row) -> MigrationLogEntry:
        return MigrationLogEntry(
            migration_id=row["migration_id"],
            owner=row["owner"],
            event=row["event"],
            status=row["status"],
            timestamp=row["timestamp"],
            lock_owner=row["lock_owner"],
            detail=row["detail"],
        )


class MigrationRunner:
    """Runs deployment migrations once, then resumes from recorded state."""

    def __init__(
        self,
        store: Optional[MigrationStateStore] = None,
    ) -> None:
        self.store = store or SQLiteMigrationStateStore()

    def run(self, job: MigrationJob, *, owner: str) -> MigrationResult:
        owner = self._normalize_owner(owner)
        self._validate_declaration(job, owner)

        begin = self.store.begin(job.identifier, owner)
        if begin.already_completed:
            return MigrationResult(
                migration_id=job.identifier,
                owner=owner,
                status="skipped",
                ran=False,
                completion=begin.completion,
                lock_owner=begin.lock_owner,
            )
        if not begin.acquired:
            return MigrationResult(
                migration_id=job.identifier,
                owner=owner,
                status="locked",
                ran=False,
                lock_owner=begin.lock_owner,
            )

        try:
            value = job.handler()
        except Exception as exc:
            self.store.fail(job.identifier, owner, type(exc).__name__)
            raise

        completion = self.store.complete(job.identifier, owner)
        return MigrationResult(
            migration_id=job.identifier,
            owner=owner,
            status=completion.status,
            ran=True,
            completion=completion,
            value=value,
        )

    def _validate_declaration(self, job: MigrationJob, owner: str) -> None:
        if job.idempotent == job.single_run:
            detail = (
                "declare exactly one of idempotent=True or single_run=True"
            )
            self.store.reject(job.identifier, owner, detail)
            raise MigrationDeclarationError(
                f"migration {job.identifier!r} must {detail}"
            )

    @staticmethod
    def _normalize_owner(owner: str) -> str:
        normalized = (owner or "").strip()
        if not normalized:
            raise ValueError("migration lock owner is required")
        return normalized
