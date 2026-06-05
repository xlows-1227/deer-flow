"""ORM model registration entry point.

Importing this module ensures all ORM models are registered with
``Base.metadata`` so Alembic autogenerate detects every table.

The actual ORM classes have moved to entity-specific subpackages:
- ``deerflow.persistence.thread_meta``
- ``deerflow.persistence.run``
- ``deerflow.persistence.scheduled_task``
- ``deerflow.persistence.feedback``
- ``deerflow.persistence.user``

``RunEventRow`` remains in ``deerflow.persistence.models.run_event`` because
its storage implementation lives in ``deerflow.runtime.events.store.db`` and
there is no matching entity directory.
"""

from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.connector.model import ConnectorAuditLogRow, ConnectorGrantRow, ConnectorInstanceRow, ConnectorMetadataCacheRow
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.scheduled_task.model import ScheduledTaskRow
from deerflow.persistence.scheduled_task_run.model import ScheduledTaskRunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.thread_share.model import ThreadShareRow
from deerflow.persistence.user.model import UserRow

__all__ = [
    "FeedbackRow",
    "ConnectorAuditLogRow",
    "ConnectorGrantRow",
    "ConnectorInstanceRow",
    "ConnectorMetadataCacheRow",
    "RunEventRow",
    "RunRow",
    "ScheduledTaskRow",
    "ScheduledTaskRunRow",
    "ThreadMetaRow",
    "ThreadShareRow",
    "UserRow",
]
