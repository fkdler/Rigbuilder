from enum import StrEnum


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    LOADING_MODEL = "loading_model"
    RUNNING = "running"
    SAVING_RESULT = "saving_result"
    UNLOADING_MODEL = "unloading_model"
    COMPLETED = "completed"
    FAILED = "failed"
