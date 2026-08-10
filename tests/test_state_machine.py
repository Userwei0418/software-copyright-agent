import tempfile
import unittest
from pathlib import Path

from software_copyright_agent.domain import TaskStatus
from software_copyright_agent.repositories import ConcurrentUpdateError
from software_copyright_agent.service import (
    QUALITY_POLICY_VERSION,
    WORKFLOW_VERSION,
    utc_now,
)
from software_copyright_agent.state_machine import (
    InvalidTaskTransitionError,
    TaskStateMachine,
)
from software_copyright_agent.storage import Database
from software_copyright_agent.unit_of_work import UnitOfWork


class TaskStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "app.db")
        self.database.initialize()
        now = utc_now()
        with UnitOfWork(self.database) as unit_of_work:
            unit_of_work.sources.add_directory("source-1", "/project", "project", now)
            unit_of_work.tasks.add(
                "task-1",
                "source-1",
                WORKFLOW_VERSION,
                QUALITY_POLICY_VERSION,
                now,
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_invalid_transition_is_rejected_without_mutation(self) -> None:
        machine = TaskStateMachine()
        with UnitOfWork(self.database) as unit_of_work:
            task = unit_of_work.tasks.get("task-1")
            with self.assertRaises(InvalidTaskTransitionError):
                machine.transition(
                    unit_of_work,
                    task,
                    TaskStatus.COMPLETED,
                    utc_now(),
                )

        with UnitOfWork(self.database) as unit_of_work:
            task = unit_of_work.tasks.get("task-1")
            self.assertEqual(task.status, TaskStatus.CREATED)
            self.assertEqual(task.row_version, 1)

    def test_stale_version_is_rejected(self) -> None:
        machine = TaskStateMachine()
        with UnitOfWork(self.database) as unit_of_work:
            stale_task = unit_of_work.tasks.get("task-1")
            machine.transition(
                unit_of_work,
                stale_task,
                TaskStatus.RUNNING,
                utc_now(),
                current_stage_key="02_scan",
            )

        with UnitOfWork(self.database) as unit_of_work:
            with self.assertRaises(ConcurrentUpdateError):
                machine.transition(
                    unit_of_work,
                    stale_task,
                    TaskStatus.RUNNING,
                    utc_now(),
                    current_stage_key="02_scan",
                )
