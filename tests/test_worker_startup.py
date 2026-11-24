"""The real worker must start with every workflow registered.

Regression: with SQLAlchemy 2.1, Temporal's workflow sandbox failed to
re-import the drift models and the worker crashed at startup
("TypeError: __annotations__ must be set to a dict object").
"""

from __future__ import annotations

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from vireonet.worker import WORKFLOWS, workflow_runner


async def test_worker_validates_every_workflow_in_the_sandbox() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        worker = Worker(
            env.client,
            task_queue="startup-q",
            workflows=WORKFLOWS,
            workflow_runner=workflow_runner(),
            activities=[],
        )
        assert len(WORKFLOWS) == 6
        async with worker:
            pass
