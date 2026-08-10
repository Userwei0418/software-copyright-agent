from typing import Dict, FrozenSet, Optional

from .domain import TaskStatus
from .repositories import TaskRecord
from .unit_of_work import UnitOfWork


class InvalidTaskTransitionError(ValueError):
    pass


ALLOWED_TRANSITIONS: Dict[TaskStatus, FrozenSet[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.RUNNING}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING_FOR_USER,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.FAILED,
            TaskStatus.COMPLETED_WITH_WARNINGS,
            TaskStatus.COMPLETED,
        }
    ),
    TaskStatus.WAITING_FOR_USER: frozenset(
        {TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED}
    ),
    TaskStatus.CANCEL_REQUESTED: frozenset({TaskStatus.CANCELED}),
    TaskStatus.CANCELED: frozenset({TaskStatus.RUNNING}),
    TaskStatus.FAILED: frozenset({TaskStatus.RUNNING}),
    TaskStatus.COMPLETED_WITH_WARNINGS: frozenset({TaskStatus.RUNNING}),
    TaskStatus.COMPLETED: frozenset({TaskStatus.RUNNING}),
}


class TaskStateMachine:
    def transition(
        self,
        unit_of_work: UnitOfWork,
        task: TaskRecord,
        target: TaskStatus,
        now: str,
        current_stage_key: Optional[str] = None,
        failure_category: Optional[str] = None,
        safe_error_message: Optional[str] = None,
    ) -> TaskRecord:
        if target not in ALLOWED_TRANSITIONS[task.status]:
            raise InvalidTaskTransitionError(
                "Invalid task transition: {0} -> {1}".format(
                    task.status.value, target.value
                )
            )
        updated = unit_of_work.tasks.transition(
            task_id=task.id,
            expected_version=task.row_version,
            from_status=task.status,
            to_status=target,
            now=now,
            current_stage_key=current_stage_key,
            failure_category=failure_category,
            safe_error_message=safe_error_message,
        )
        level = "error" if target == TaskStatus.FAILED else "info"
        unit_of_work.events.add(
            task_id=task.id,
            event_type="task.{0}".format(target.value),
            level=level,
            message="Task status changed from {0} to {1}".format(
                task.status.value, target.value
            ),
            payload={
                "from": task.status.value,
                "to": target.value,
                "row_version": updated.row_version,
            },
            now=now,
        )
        return updated
