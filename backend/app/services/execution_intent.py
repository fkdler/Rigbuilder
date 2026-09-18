"""One request-local semantic plan shared by retrieval, selection and presentation."""
from contextvars import ContextVar
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: Literal["recommend", "introduce", "explain", "chat"] = "recommend"
    domain: Literal["hardware", "ai_model", "general"] = "hardware"
    scope: Literal["single", "bundle", "none"] = "single"
    roles: list[Literal["cpu", "gpu", "model"]] = Field(default_factory=list, max_length=2)
    priority: Literal["balanced", "performance"] = "balanced"
    workload: Literal["gaming", "office", "local_ai", "general"] = "general"
    subject: str = Field(default="", max_length=120)

    def instruction(self) -> str:
        if self.domain == "ai_model":
            return ("Recommend AI models for local deployment, not CPUs, GPUs or a computer build. "
                    "Hardware mentioned by the user is available resources, not products to buy. "
                    "Use only returned catalogue facts; distinguish base models, quantized files, and runtimes. "
                    "Respect task, model family, quantization and memory constraints. Do not equate weight file size "
                    "with total memory, or loading estimates with tested throughput or compatibility. "
                    "If hardware or task is missing, give a provisional model option and ask for missing details.")
        target = ", ".join(self.roles) or "the requested product"
        return (f"Execution plan: task={self.task}; scope={self.scope}; target={target}; "
                f"priority={self.priority}; workload={self.workload}. "
                + ("Return only the requested component category; no whole-PC assembly or supporting parts. "
                   if self.scope == "single" else "Cover all requested core roles with a compatible desktop pair. ")
                + ("Prefer the strongest eligible catalogue performance tier; this is not a measured FPS claim. "
                   if self.priority == "performance" else "Prefer a balanced mainstream option, not an unrequested flagship. ")
                + "Respect current user constraints; do not revive previous choices or supply storage, cooler or case advice.")


execution_intent: ContextVar[ExecutionIntent | None] = ContextVar("execution_intent", default=None)


def planned_messages(messages, message):
    """The effective request already contains the selected user history, never old answers."""
    plan = execution_intent.get()
    if plan is None:
        return messages
    constraints = [m for m in messages[1:] if m["role"] == "system" and m["content"].startswith("Confirmed constraints:")]
    from app.services.model_choices import excluded_model_ids
    exclusions = ([{'role': 'system', 'content':
        'The user requested a different model. Exclude these previously shown model IDs: '
        + ', '.join(excluded_model_ids.get())
        + '. Keep all other user requirements. If no alternative exists in the evidence, report the gap; never repeat an excluded model.'}]
        if excluded_model_ids.get() else [])
    return [messages[0], *constraints, *exclusions, {"role": "system", "content": plan.instruction()},
            {"role": "user", "content": message}]
