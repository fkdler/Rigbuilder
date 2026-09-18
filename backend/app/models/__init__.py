# ``truth_v3`` runs ``Base.metadata.sorted_tables`` at import time, which resolves
# every foreign key in the metadata. ``User`` therefore has to be registered before
# anything that references ``app_user`` (auth_session, conversation, query_job) or
# importing this package raises NoReferencedTableError. Keep it first.
from app.models.user import User
from app.models.admin_records import AdminChange, TokenUsage
from app.models.hardware_daily_cache import HardwareDailyCache

from app.models.agent_message import AgentMessage
from app.models.agent_run import AgentRun
from app.models.auth_session import AuthSession
from app.models.conversation import Conversation
from app.models.conversation_context_snapshot import ConversationContextSnapshot
from app.models.conversation_message import ConversationMessage
from app.models.tool_call import ToolCall
from app.models.query_event import QueryEvent
from app.models.query_job import QueryJob
from app.models.truth_v3 import *  # noqa: F403 - register/export V3 Truth ORM
from app.models.user_constraint import UserConstraint

__all__ = [
    "AgentMessage",
    "AgentRun",
    "AIModel",
    "AuthSession",
    "Conversation",
    "ConversationContextSnapshot",
    "ConversationMessage",
    "Hardware",
    "HardwareDailyCache",
    "ModelVariant",
    "QueryEvent",
    "QueryJob",
    "ToolCall",
    "User",
    "UserConstraint",
]
