"""Small, explicit request policy; a whole build need not contain a discrete GPU."""
import re


def office_default(message: str) -> bool:
    from app.services.execution_intent import execution_intent
    plan = execution_intent.get()
    if plan:
        return plan.workload == "office" and "gpu" not in plan.roles
    text = message.casefold()
    office = bool(re.search(r"办公|文档处理|office|word|excel", text))
    positive = re.sub(r"(?:不要|不需要|无需|不装|不配)(?:独立显卡|显卡|独显)|no discrete gpu", "", text)
    positive = re.sub(r"(?:不玩|不打|无需|不需要)(?:游戏|网游)|(?:no|not)\s+gaming", "", positive)
    demanding = bool(re.search(r"游戏|3a|渲染|建模|训练|本地.*模型|gaming|render|cuda|独显|独立显卡|discrete gpu", positive))
    return office and not demanding


def is_build_correction(message: str) -> bool:
    return bool(re.search(r"(?:不是|不算|不完整|不对).*配置|(?:补齐|补全|完整|整套).*(?:配置|配件|电脑)|只有.*(?:cpu|处理器)|其他配件|complete.*build", message, re.I))


def repair_request(message: str, original: str | None) -> str:
    if original and is_build_correction(message):
        return f"原始需求：{original}\n本轮纠正：{message}\n请重新提供一套完整电脑配置草案，逐项列出配件和未核验项；旧推荐不是用户指定必须保留的部件。"
    return message


def continue_request(message: str, original: str | None, continuation: bool) -> str:
    """Carry user requirements, never turn a previous model choice into a pin."""
    if not original or not continuation:
        return repair_request(message, original)
    from app.services.hardware_intent import _PATTERNS, sku_requests, gpu_series
    previous = original
    model_name = re.search(r'(?<![a-z0-9])(?:qwen|deepseek|llama|gemma|phi)[a-z0-9.\-]*', message, re.I)
    if model_name:
        previous = re.sub(r'(?<![a-z0-9])(?:qwen|deepseek|llama|gemma|phi)[a-z0-9.\-]*|更小|小一点|更大|轻量', '', previous, flags=re.I)
    for role, spec in sku_requests(message).items():
        if spec["required"] or (role == "gpu" and gpu_series(message)):
            previous = _PATTERNS[role].sub("", previous)
            if role == "gpu":
                previous = re.sub(r"\d{2}\s*系(?:列)?", "", previous)
    # New workload starts a new scenario, rather than mixing office and gaming.
    def workloads(text):
        text = re.sub(r"(?:不玩|不打|不要|无需|不需要)(?:游戏|网游)|(?:no|not)\s+gaming", "", text, flags=re.I)
        return {name for name, pattern in (("office", r"办公|office"), ("gaming", r"游戏|网游|gaming"),
                                          ("render", r"渲染|建模|render")) if re.search(pattern, text, re.I)}
    current_workloads, previous_workloads = workloads(message), workloads(original)
    if current_workloads and previous_workloads and current_workloads != previous_workloads:
        return message
    return f"此前用户需求：{previous}\n本轮用户调整：{message}\n沿用未变更的用户条件，本轮要求优先；必须查询数据库，查无记录不代表未发布。"


def bind_referenced_components(message, effective_message, anchor):
    """Only explicit references promote an earlier verified selection to a user pin."""
    if anchor is None:
        return effective_message
    from app.services.hardware_intent import sku_requests
    explicit = sku_requests(message)
    names = []
    for role, noun in (("gpu", r"显卡|GPU"), ("cpu", r"处理器|CPU")):
        if explicit[role]["required"] or not re.search(
            rf"(?:这|那|刚才(?:的)?|上(?:一轮|次)(?:的)?)(?:张|块|个|款)?(?:{noun})", message, re.I):
            continue
        choices = [c for c in anchor.selected if any(x.claim.field_key.startswith(role + ".") for x in c.claims)]
        if len(choices) == 1:
            names.append(f"{role.upper()} 必须使用 {choices[0].canonical_name}")
    return effective_message + ("\n用户明确指代的已选部件：" + "；".join(names) if names else "")


def office_utility(facts: dict) -> float | None:
    """Policy utility, never a performance or market-value measurement."""
    def number(key):
        raw = facts.get(key)
        if isinstance(raw, dict):
            raw = raw.get("value")
        if isinstance(raw, bool):
            return None
        try:
            return float(raw) if raw is not None else None
        except (ValueError, TypeError):
            return None
    power, cores = number("cpu.base_power_w"), number("cpu.cores_total")
    if power is None or cores is None or power <= 0 or cores <= 0:
        return None
    # Version office-fit-v1: a transparent conservative preference. Core count
    # is not CPU speed; more cores beyond the ordinary-office band do not win.
    power_fit = min(1.0, 65.0 / power)
    core_fit = min(1.0, cores / 4) * min(1.0, 8.0 / cores)
    return round(100 * (0.7 * power_fit + 0.3 * core_fit), 2)
