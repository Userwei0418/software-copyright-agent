from dataclasses import dataclass

from .domain import TaskStatus
from .repositories import RepositoryError
from .service import new_id, utc_now
from .state_machine import TaskStateMachine
from .storage import Database
from .unit_of_work import UnitOfWork


class ConfirmationError(ValueError):
    pass


@dataclass(frozen=True)
class ConfirmationResult:
    task_id: str
    field_key: str
    confirmation_id: str
    fact_id: str
    remaining_required: int
    task_status: TaskStatus


class ConfirmationService:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._state_machine = TaskStateMachine()

    def answer(self, task_id: str, field_key: str, value: str) -> ConfirmationResult:
        normalized_field = field_key.strip()
        normalized_value = value.strip()
        if not normalized_field:
            raise ConfirmationError("Field key must not be empty")
        if not normalized_value:
            raise ConfirmationError("Confirmation value must not be empty")

        self._database.initialize()
        now = utc_now()
        evidence_id = new_id()
        fact_id = new_id()
        try:
            with UnitOfWork(self._database) as unit_of_work:
                task = unit_of_work.tasks.get(task_id)
                if task.status != TaskStatus.WAITING_FOR_USER:
                    raise ConfirmationError(
                        "Task is not waiting for user confirmation: {0}".format(
                            task.status.value
                        )
                    )
                if task.snapshot_id is None:
                    raise ConfirmationError("Task does not have a project snapshot")

                confirmation = unit_of_work.confirmations.get_pending(
                    task_id, normalized_field
                )
                unit_of_work.confirmations.answer(
                    confirmation["id"], normalized_value, now
                )
                unit_of_work.evidence.add_user_confirmation(
                    evidence_id,
                    task.snapshot_id,
                    normalized_field,
                    confirmation["id"],
                    now,
                )
                unit_of_work.facts.supersede_active(task_id, normalized_field)
                unit_of_work.facts.add_user_confirmed(
                    fact_id,
                    task_id,
                    normalized_field,
                    normalized_value,
                    evidence_id,
                    now,
                )
                remaining = unit_of_work.confirmations.pending_required_count(task_id)
                unit_of_work.events.add(
                    task_id,
                    "confirmation.answered",
                    "info",
                    "User confirmed project metadata",
                    {
                        "field_key": normalized_field,
                        "remaining_required": remaining,
                    },
                    now,
                )

                final_task = task
                if remaining == 0:
                    confirmation_stage_id = unit_of_work.stages.find_waiting(
                        task_id, "04_confirm_metadata"
                    )
                    if confirmation_stage_id is not None:
                        unit_of_work.stages.complete_waiting(
                            confirmation_stage_id,
                            {"remaining_required": 0},
                            now,
                        )
                    running = self._state_machine.transition(
                        unit_of_work,
                        task,
                        TaskStatus.RUNNING,
                        now,
                        current_stage_key="04_confirm_metadata",
                    )
                    final_task = self._state_machine.transition(
                        unit_of_work,
                        running,
                        TaskStatus.COMPLETED,
                        now,
                        current_stage_key="04_confirm_metadata",
                    )

                return ConfirmationResult(
                    task_id=task_id,
                    field_key=normalized_field,
                    confirmation_id=confirmation["id"],
                    fact_id=fact_id,
                    remaining_required=remaining,
                    task_status=final_task.status,
                )
        except RepositoryError as error:
            raise ConfirmationError(str(error)) from error
