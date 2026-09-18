"""Keyword-based query routing, request scope and Agent-count policy.

Routing decides whether a message is worth the full verified pipeline
(multi-model database Agents -> truth verification -> fusion) or can be answered
by the fast single-model path.  The two mistakes are not equally costly:

* Sending a hardware decision to the fast path yields an unverified, memorised
  answer.  Measured: ``给我推荐一套能打大部分网游的电脑配置`` was routed to ``chat``
  because the domain vocabulary knew component nouns but not the phrasing people
  use when buying a machine, so the answer was a hallucinated parts list with
  prices -- and the interface still labelled it "Verified fusion complete".
* Sending a conversational message to the full pipeline only costs latency.

The vocabulary is therefore deliberately broad on the domain side while keeping
the conjunction with a decision word, so ``你好`` and ``你能做什么`` stay on the
fast path.
"""

from __future__ import annotations

from typing import Any, Literal
import re

from app.schemas.query import QueryMode, ResolvedMode
from app.services.requirements import office_default, is_build_correction

# Nouns that put a message in the hardware / local-AI domain.  Grouped by what
# the user is actually talking about: the phrasings used when buying a machine
# are different from the names of its parts, and only the latter were covered
# before.
DOMAIN_WORDS = {
    # components
    "gpu", "cpu", "显卡", "显存", "内存", "主板", "电源", "机箱", "硬盘", "固态",
    "ssd", "hdd", "散热", "风扇", "水冷", "显示器", "笔记本",
    # whole-machine phrasings -- the gap that sent a build question to the fast path
    "电脑", "配置", "整机", "主机", "台式", "台式机", "装机", "攒机", "组装", "外设",
    # brands and series
    "nvidia", "amd", "intel", "英伟达", "英特尔", "锐龙", "酷睿", "rtx", "gtx",
    # local AI deployment
    "硬件", "模型", "llm", "本地模型", "推理", "部署",
    # workloads people buy hardware for
    "游戏", "网游", "电竞", "跑分",
}

DECISION_WORDS = {
    "想要", "需要", "必须", "指定", "置顶", "围绕", "换成", "改成",
    "推荐", "选择", "选哪个", "预算", "对比", "比较", "购买", "买", "搭配", "兼容",
    "部署", "能跑", "运行能力", "适合", "哪个好", "怎么选",
    # added: phrasings that ask for a build or a verdict rather than a lookup
    "值得", "值不值", "性价比", "够用", "够不够", "能带", "带的动", "带得动",
    "帮我配", "帮我选", "配一台", "配一套", "配个",
}

RequestScope = Literal["single", "bundle"]


def opts_out_of_database(message: str) -> bool:
    return bool(re.search(r"(?:不要|不用|无需|无须|别|不|禁止)\s*(?:再)?\s*(?:搜索|检索|查询|访问|调用|使用|查|搜)\s*(?:本地)?\s*(?:数据库|库)|(?:do not|don't|never)\s+(?:query|search|access|use)\s+(?:(?:the|a)\s+)?(?:database|db)\b|do not query|without database", message, re.I))


def is_concept_question(message: str) -> bool:
    text = message.casefold()
    return (bool(re.search(r"什么是|是什么|工作原理|what is|what are", text))
            and not any(word in text for word in DECISION_WORDS)
            and not re.search(r"这套|这个|这款|上述|刚才|推荐|价格|新品|上市|发布|rtx|gtx|\brx\s*\d", text))

# Phrases that ask for a whole machine rather than one component.
BUNDLE_WORDS = {
    "配置",
    "一套", "一台", "整机", "整台", "全套", "配置单", "装机", "攒机", "组装",
    "配置方案", "主机",
}

# Component nouns; two or more in one message also means "a whole build".
COMPONENT_WORDS = {
    "cpu", "gpu", "显卡", "主板", "内存", "电源", "机箱", "硬盘", "固态", "ssd",
    "散热", "显示器",
}


def is_explanation_followup(message: str) -> bool:
    text = message.casefold()
    if re.search(r"换|重新推荐|改成|不要这个|预算|推荐(?:一个|一款|适合).*模型|instead|replace|budget", text):
        return False
    return bool(re.search(r"^(?:为什么[呢？?]*|解释一下[吧呢？?]*)$|(?:进一步|详细|再|具体).*(?:解释|说明|证据|理由)|为什么.*(?:推荐|选|这样|这么)|推荐.*(?:依据|理由)|有什么依据|依据呢|证据呢|why.*(?:recommend|choose|this)|explain.*(?:more|recommend|choice)", text))


def requested_model_capabilities(message: str) -> set[str]:
    text = message.casefold()
    from app.services.execution_intent import execution_intent
    plan = execution_intent.get()
    model_target = plan is not None and plan.domain == 'ai_model'
    text = re.sub(r'(?:不要|不用|不需要|无需)(?:看图|视觉|多模态|绘图|文生图|语音识别|图像理解|图像生成)', '', text)
    if not model_target and (re.search(r"显卡|(?<![a-z])gpu(?![a-z])|(?<![a-z])cpu(?![a-z])|处理器|电脑|主机|整机|装机|电源|主板", text) or not re.search(r"模型|model|vlm", text)):
        return set()
    if re.search(r"文生图|图像生成|图片生成|绘图|text.to.image", text):
        return {"image_generation"}
    if re.search(r"视觉|看图|图像理解|图片理解|多模态|vision|\bvlm\b|ocr", text):
        return {"vision_input"}
    if re.search(r"语音识别|转录|speech recognition|transcription", text):
        return {"audio_input"}
    return set()


def resolve_query_mode(message: str, requested: QueryMode, has_constraints: bool = False, *, has_recommendation: bool = False) -> ResolvedMode:
    normalized = message.casefold()
    # The user can explicitly opt out of catalogue access.  This takes
    # precedence even when the UI was left on VERIFIED: a fast answer is more
    # useful than spending the fusion budget and then reporting an empty run.
    no_database = opts_out_of_database(message)
    if no_database:
        return "chat"
    if requested == "auto" and not has_constraints and is_concept_question(message):
        return "chat"
    if requested_model_capabilities(message) and requested != "chat":
        return "fusion"
    if has_recommendation and is_explanation_followup(message) and requested != "chat":
        return "fusion"
    if has_recommendation and is_build_correction(message) and requested != "chat":
        return "fusion"
    # Compatibility advice for a named GPU is naturally answered from model
    # knowledge; forcing the full hardware truth pipeline makes this basic
    # follow-up brittle when the model catalogue has no matching entry.
    if "\u672c\u5730\u6a21\u578b" in normalized and any(word in normalized for word in ("\u8fd0\u884c", "\u9002\u5408", "\u63a8\u8350")):
        return "chat"
    if requested in {"chat", "fusion"}:
        return requested
    if has_constraints:
        return "fusion"
    if has_recommendation and is_hardware_followup(message):
        return "fusion"
    has_domain = any(word in normalized for word in DOMAIN_WORDS)
    has_decision = any(word in normalized for word in DECISION_WORDS)
    return "fusion" if has_domain and (has_decision or any(word in normalized for word in BUNDLE_WORDS)) else "chat"


def is_hardware_followup(message: str) -> bool:
    """Elliptical changes still require catalogue evidence after a build."""
    if is_concept_question(message):
        return False
    return bool(re.search(r"显卡|cpu|gpu|rtx|gtx|内存|主板|电源|配置|预算|换成|改成|\d{2}\s*系|便宜一点|贵一点", message, re.I))


def _upgrade_role(normalized: str) -> str | None:
    if re.search(r"(?:cpu|处理器).{0,12}(?:配|选|推荐).{0,8}(?:显卡|gpu)", normalized):
        return "gpu"
    if re.search(r"(?:显卡|gpu).{0,12}(?:配|选|推荐).{0,8}(?:cpu|处理器)", normalized):
        return "cpu"
    return None


def classify_request(message: str) -> RequestScope:
    """Whether a message asks for one component or for a whole machine.

    This is **not** a rejection path. A bundle question still runs the full
    verified pipeline; the classification only tells the Agent prompt and the
    presentation that the honest answer is "here is the key component, plus what
    this Catalogue cannot decide yet" rather than an invented parts list.
    """
    from app.services.execution_intent import execution_intent
    plan = execution_intent.get()
    if plan and plan.scope in {"single", "bundle"}:
        return plan.scope
    if plan and plan.domain == "ai_model":
        return "single"
    normalized = re.sub(r"(?:不要|不需要|无需|不用)(?:推荐)?(?:整机|整套配置|电脑配置)", "", message.casefold())
    if _upgrade_role(normalized):
        return "single"
    if any(word in normalized for word in BUNDLE_WORDS):
        return "bundle"
    # Aliases for one category (GPU/显卡) are not two different components.
    categories = (r"cpu|处理器", r"gpu|显卡", r"主板", r"内存", r"电源", r"机箱",
                  r"硬盘|固态|ssd", r"散热", r"显示器")
    if sum(bool(re.search(pattern, normalized)) for pattern in categories) >= 2:
        return "bundle"
    return "single"


def select_agent_models(
    message: str,
    constraints: list[Any] | None,
    settings: Any,
) -> list[str]:
    """Return the fixed pair, regardless of request complexity or old tuning flags.

    FusionService validates the configured list before executing any work. The
    cap here also prevents callers from re-enabling a third worker via old settings.
    """
    from app.inference.profiles import AGENT_COUNT

    available = list(dict.fromkeys(
        model_id for model_id in getattr(settings, "llm_test_model_id_list", []) if model_id
    ))
    return available[:AGENT_COUNT]


def requested_component_roles(message: str) -> set[str]:
    """The current request's hardware target, independent of historical candidates."""
    from app.services.execution_intent import execution_intent
    plan = execution_intent.get()
    if plan is not None:
        return set(plan.roles)
    normalized = message.casefold()
    # Office and other display-light workloads generally need a CPU with an
    # integrated GPU; asking the database for a discrete GPU creates an
    # unreasonable gaming-oriented bundle.
    device_request = re.sub(r"(?:不要|不用|不是|排除|不选)\s*(?:笔记本|laptop|notebook)(?:显卡|电脑)?", "", normalized)
    if re.search(r"笔记本|laptop|notebook|键鼠|外设", device_request):
        return set()
    if office_default(message) and (classify_request(message) == "bundle" or not re.search(r"显卡|(?<![a-z])gpu(?![a-z])", normalized)):
        return {"cpu"}
    # Matching parts to an existing component is a single target, not a new build.
    upgrade_role = _upgrade_role(normalized)
    if upgrade_role:
        return {upgrade_role}
    if classify_request(message) == "bundle":
        return {"cpu", "gpu"}
    roles = set()
    if re.search(r"显卡|(?<![a-z])gpu(?![a-z])|graphics card", normalized):
        roles.add("gpu")
    if re.search(r"处理器|(?<![a-z])cpu(?![a-z])|processor", normalized):
        roles.add("cpu")
    return roles


__all__ = [
    "DOMAIN_WORDS",
    "DECISION_WORDS",
    "BUNDLE_WORDS",
    "COMPONENT_WORDS",
    "RequestScope",
    "classify_request",
    "resolve_query_mode",
    "select_agent_models",
]
