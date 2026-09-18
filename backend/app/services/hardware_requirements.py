"""Explicit request requirements, shared by discovery and the final truth gate.

These rules extract user constraints, not product facts. Unknown product facts
never satisfy a hard requirement. GB in consumer VRAM requests denotes the
catalogue's GiB capacity convention; MiB is converted explicitly.
"""
from dataclasses import dataclass
import math
import re


@dataclass(frozen=True)
class HardwareRequirements:
    count: int = 1
    split_priorities: bool = False
    gpu_brands: tuple[str, ...] = ()
    excluded_gpu_brands: tuple[str, ...] = ()
    min_vram: float | None = None


def parse_requirements(message: str) -> HardwareRequirements:
    count_match = re.search(r'(?:推荐|挑选|选择|给我|recommend)\s*(?:我|给我)?\s*([一二两三四五六七八九十]|\d{1,2})\s*(?:张|款|个|种|GPUs?\b|graphics cards?\b)', message, re.I)
    numerals = dict(zip('一二两三四五六七八九十', (1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10)))
    word = count_match.group(1) if count_match else '1'
    count = numerals[word] if word in numerals else max(1, int(word))
    split = bool(re.search(r'性价比|实惠|经济|value', message, re.I) and re.search(r'高性能|性能优先|旗舰|high.performance', message, re.I)
                 and (count > 1 or len(re.findall(r'一[张款个]|one', message, re.I)) >= 2))
    if split:
        count = max(count, 2)
    brands, excluded = set(), set()
    for match in re.finditer(r'NVIDIA|英伟达|(?<![a-z])NV(?![a-z])|AMD|英特尔|Intel', message, re.I):
        # CPU brands in a build must not constrain its GPU.
        clause = re.split(r'[，,。;；!?！？\n]', message[:match.start()])[-1]
        suffix = re.split(r'[，,。;；!?！？\n]', message[match.end():])[0]
        if re.search(r'CPU|处理器', clause + suffix, re.I) and not re.search(r'显卡|GPU', clause + suffix, re.I):
            continue
        brand = 'nvidia' if re.fullmatch(r'nvidia|英伟达|nv', match.group(), re.I) else 'amd' if match.group().lower() == 'amd' else 'intel'
        (excluded if re.search(r'不要|不用|排除|不选|别选|不是|而非|without|avoid|not\s', clause, re.I) else brands).add(brand)
    minimums = []
    for pattern in (
        r'(?:显存|VRAM)\s*(?:容量)?\s*(?:至少|不低于|不少于|大于等于|>=|≥|at least)\s*(\d+(?:\.\d+)?)\s*(GiB|GB|MiB|MB|G)',
        r'(?:至少|不低于|不少于|>=|≥|at least)\s*(\d+(?:\.\d+)?)\s*(GiB|GB|MiB|MB|G)\s*(?:的)?\s*(?:显存|VRAM)',
        r'(\d+(?:\.\d+)?)\s*(GiB|GB|MiB|MB|G)\s*(?:及以上|以上)\s*(?:的)?\s*(?:显存|VRAM)',
    ):
        for match in re.finditer(pattern, message, re.I):
            value = float(match.group(1)) / (1024 if match.group(2).lower() in {'mib', 'mb'} else 1)
            minimums.append(value)
    return HardwareRequirements(count, split, tuple(sorted(brands)), tuple(sorted(excluded)), max(minimums) if minimums else None)


BRAND_PATTERNS = {
    'nvidia': r'nvidia|geforce|quadro|\b(?:rtx|gtx)\s*\d',
    'amd': r'\bamd\b|radeon',
    'intel': r'\bintel\b|\barc\b',
}


def allows_brand(requirements, name):
    brands = {brand for brand, pattern in BRAND_PATTERNS.items() if re.search(pattern, name, re.I)}
    return (not requirements.gpu_brands or bool(brands & set(requirements.gpu_brands))) and not bool(brands & set(requirements.excluded_gpu_brands))


def meets_vram(requirements, value, unit):
    if requirements.min_vram is None:
        return True
    if value is None or isinstance(value, bool) or (unit or '').lower() not in {'gib', 'gb', 'mib', 'mb'}:
        return False
    try:
        capacity = float(value) / (1024 if unit.lower() in {'mib', 'mb'} else 1)
        return math.isfinite(capacity) and capacity >= requirements.min_vram
    except (TypeError, ValueError):
        return False


def verified_requirement_issue(message, role, candidate):
    requirements = parse_requirements(message)
    if role != 'gpu':
        return None
    if not allows_brand(requirements, candidate.canonical_name or ''):
        return 'requested_brand_mismatch'
    if requirements.min_vram is not None:
        proofs = [getattr(c, 'verification', c) for c in candidate.claims if c.claim.field_key == 'gpu.vram_gib']
        supported = [p for p in proofs if p.status == 'supported' and p.valid_evidence_ids]
        if not supported:
            return 'required_vram_unverified'
        if not all(meets_vram(requirements, p.canonical_value, p.canonical_unit) for p in supported):
            return 'required_vram_mismatch'
    return None


def gpu_sql_requirements(message, alias='c'):
    requirements = parse_requirements(message)
    # Closed vocabulary only; never interpolate user-authored strings into SQL.
    clauses = {
        'nvidia': f"({alias}.name ILIKE '%NVIDIA%' OR {alias}.name ILIKE '%GeForce%' OR {alias}.name ILIKE '%RTX%' OR {alias}.name ILIKE '%GTX%' OR {alias}.name ILIKE '%Quadro%')",
        'amd': f"({alias}.name ILIKE '%AMD%' OR {alias}.name ILIKE '%Radeon%')",
        'intel': f"({alias}.name ILIKE '%Intel%' OR {alias}.name ILIKE '%Arc%')",
    }
    filters = []
    if requirements.gpu_brands:
        filters.append('(' + ' OR '.join(clauses[b] for b in requirements.gpu_brands) + ')')
    filters.extend('NOT ' + clauses[b] for b in requirements.excluded_gpu_brands)
    if requirements.min_vram is not None:
        filters.append(f'{alias}.vram_gib >= {requirements.min_vram}')
    return filters
