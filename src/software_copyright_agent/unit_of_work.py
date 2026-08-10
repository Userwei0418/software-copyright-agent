import sqlite3
from typing import Optional, Type

from .repositories import (
    EventRepository,
    EvidenceRepository,
    FactRepository,
    ConfirmationRepository,
    SnapshotRepository,
    SourceRepository,
    StageRepository,
    TaskRepository,
    SourcePlanRepository,
    CodePreviewRepository,
)
from .storage import Database


class UnitOfWork:
    def __init__(self, database: Database) -> None:
        self._database = database
        self.connection: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "UnitOfWork":
        connection = sqlite3.connect(str(self._database.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        self.connection = connection
        self.sources = SourceRepository(connection)
        self.snapshots = SnapshotRepository(connection)
        self.tasks = TaskRepository(connection)
        self.stages = StageRepository(connection)
        self.events = EventRepository(connection)
        self.evidence = EvidenceRepository(connection)
        self.facts = FactRepository(connection)
        self.confirmations = ConfirmationRepository(connection)
        self.source_plans = SourcePlanRepository(connection)
        self.code_previews = CodePreviewRepository(connection)
        return self

    def __exit__(
        self,
        exception_type: Optional[Type[BaseException]],
        exception: Optional[BaseException],
        traceback: object,
    ) -> None:
        if self.connection is None:
            return
        try:
            if exception_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()
            self.connection = None
