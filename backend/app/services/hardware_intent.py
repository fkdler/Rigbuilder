"""Conservative explicit SKU constraints and bounded desktop gaming discovery.

SKU matching is lexical, not a claim about existence or performance. Facts still
come exclusively from query_database observations and the TruthVerifier.
"""
import re
from app.services.hardware_requirements import parse_requirements, allows_brand, gpu_sql_requirements


def performance_priority(message):
    from app.services.execution_intent import execution_intent
    plan = execution_intent.get()
    return plan.priority == 'performance' if plan else bool(re.search(r'特别好|高端|顶尖|顶级|旗舰|极致|发烧|ultimate|flagship', message, re.I))

_PATTERNS = {
    'cpu': re.compile(r'(?<![a-z0-9])(?:i[3579]-\d{4,5}[a-z]{0,3}|\d{4,5}(?:x3d|x|xt|g|f|k|kf|ks))(?![a-z0-9])', re.I),
    'gpu': re.compile(r'(?<![a-z0-9])(?:rtx|gtx|rx)\s*\d{3,4}(?:\s*(?:ti|super|xtx|xt|d))?(?![a-z0-9])', re.I),
}

def sku_requests(message):
    result = {}
    for role, pattern in _PATTERNS.items():
        wanted, excluded = set(), set()
        for match in pattern.finditer(message):
            prefix = re.split(r'[，,。;；!?！？\n]', message[:match.start()])[-1]
            negative = bool(re.search(r'不要|不用|排除|不选|别选|不是|而非|without|avoid|not\s', prefix, re.I))
            token = re.sub(r'\s+', ' ', match.group()).casefold()
            (excluded if negative else wanted).add(token)
        # Comparisons / alternatives are deliberately not converted to one fixed SKU.
        result[role] = {'required': next(iter(wanted)) if len(wanted) == 1 else None,
                        'excluded': sorted(excluded)}
    return result

def matches_sku(name, token):
    compact = re.sub(r'\s+', '', name).casefold()
    needle = re.sub(r'\s+', '', token).casefold()
    # Keep the space before Intel's i7/i5 suffix: compacting "Core i7" to
    # "Corei7" destroys the word boundary and rejects a real catalogue match.
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', name.casefold())) if needle.startswith('i') else bool(re.search(re.escape(needle) + r'(?![a-z0-9])', compact))


def gpu_series(message):
    """Lexical NVIDIA generation intent; makes no claim about release dates."""
    if not re.search(r'nv|英伟达|rtx|显卡|gpu', message, re.I):
        return None
    matches = []
    for match in re.finditer(r'(?<!\d)(\d{2})\s*系(?:列)?', message):
        prefix = re.split(r'[，,。;；!?！？\n]', message[:match.start()])[-1]
        if not re.search(r'不要|不用|排除|不选|别选|不是|without|avoid', prefix, re.I):
            matches.append(match.group(1))
    return matches[-1] if matches else None


def _sku_sql_filter(token, alias):
    # ILIKE is a coarse prefilter; the bounded ARE expression enforces
    # the same SKU suffix boundary as matches_sku before pairing/row limits.
    pattern = re.sub(r'\s+', '[[:space:]]*', token)
    pattern += '(?![[:alnum:]])(?![[:space:]]+(ti|super|xtx|xt|d)(?![[:alnum:]]))'
    return (alias + ".name ILIKE '%" + token.replace(' ', '%') + "%' AND "
            + alias + ".name ~* '" + pattern + "'")


def _spec_evidence_filter(role, alias):
    field = 'gpu.vram_gib' if role == 'gpu' else 'cpu.cores_total'
    return ("EXISTS (SELECT 1 FROM agent_catalog.fact_evidence e WHERE e.entity_key = "
            + alias + ".entity_key AND e.field_key = '" + field + "')")


def _gpu_sql_filter(message, alias):
    spec = sku_requests(message)['gpu']
    if spec['required']:
        return _sku_sql_filter(spec['required'], alias)
    series = gpu_series(message)
    if series:
        # Keep discovery within the SQL validator's ILIKE dialect. The exact
        # numeric-generation check is repeated by allows_candidate afterwards.
        return alias + ".name ILIKE '%rtx " + series + "__%'"
    return None

def allows_candidate(message, role, name):
    if role == 'gpu' and not allows_brand(parse_requirements(message), name):
        return False
    spec = sku_requests(message).get(role, {})
    required = spec.get('required')
    series = gpu_series(message) if role == 'gpu' else None
    if series and not re.search(r'rtx\s*' + series + r'\d{2}(?!\d)', name, re.I):
        return False
    return (not required or matches_sku(name, required)) and not any(matches_sku(name, token) for token in spec.get('excluded', []))

def discovery_queries(message, roles):
    if roles == {'model'}:
        from app.services.local_models import discovery_queries as model_queries
        return model_queries(message)
    from app.services.request_scope import _gaming_rule_key
    from app.services.requirements import office_default
    if roles == {'cpu'} and office_default(message):
        spec = sku_requests(message)['cpu']
        filters = ["c.recommendable = true", "p.form_factor = 'desktop'", "c.cores_total >= 4"]
        if spec['required']:
            filters.append(_sku_sql_filter(spec['required'], 'c'))
        else:
            filters.append(_spec_evidence_filter('cpu', 'c'))
        for token in spec['excluded']:
            filters.append("c.name NOT ILIKE '%" + token.replace(' ', '%') + "%'")
        return ["SELECT c.entity_id, c.entity_key, c.name, c.cores_total, c.base_power_w, c.socket, c.integrated_gpu "
                "FROM agent_catalog.cpu_catalog c JOIN agent_catalog.component_profile_catalog p ON p.entity_key = c.entity_key "
                "WHERE " + ' AND '.join(filters) + " ORDER BY c.base_power_w ASC NULLS LAST, c.cores_total ASC, c.name LIMIT 3"]
    rule = _gaming_rule_key(message)
    if rule is None or roles != {'cpu', 'gpu'}:
        queries = []
        for role in sorted(roles & {'cpu', 'gpu'}):
            required = sku_requests(message)[role]['required']
            clause = _gpu_sql_filter(message, 'c') if role == 'gpu' else (
                _sku_sql_filter(required, 'c') if required else None)
            from app.services.execution_intent import execution_intent
            requirements = parse_requirements(message)
            explicit_filters = gpu_sql_requirements(message) if role == 'gpu' else []
            if not clause and not explicit_filters and requirements.count == 1 and execution_intent.get() is None:
                continue  # Legacy/direct Agent callers still own their discovery loop.
            columns = 'c.vram_gib, c.board_power_w, c.architecture, c.memory_type' if role == 'gpu' else 'c.cores_total, c.threads, c.socket, c.base_power_w'
            filters = ["c.recommendable = true", "p.form_factor = 'desktop'"]
            filters.extend(explicit_filters)
            if clause:
                filters.append(clause)
            if not required:
                filters.append(_spec_evidence_filter(role, 'c'))
            for token in sku_requests(message)[role]['excluded']:
                filters.append("c.name NOT ILIKE '%" + token.replace(' ', '%') + "%'")
            # A requested single component gets bounded catalogue discovery too.
            # Do not ask both agents to independently rediscover names via SQL.
            if not clause and requirements.min_vram is None and not requirements.split_priorities and not performance_priority(message):
                filters.append("p.tier_label IN ('mid', 'high')")
            order = 'DESC' if performance_priority(message) else 'ASC'
            query = (f"SELECT c.entity_id, c.entity_key, c.name, {columns} FROM agent_catalog.{role}_catalog c "
                "JOIN agent_catalog.performance_ranking p ON p.entity_key = c.entity_key WHERE "
                + ' AND '.join(filters))
            limit = min(10, max(3, requirements.count))
            if requirements.split_priorities:
                queries.append(query + " AND p.tier_label IN ('mid', 'high') ORDER BY p.index_100 ASC, c.name LIMIT 3")
                queries.append(query + " ORDER BY p.index_100 DESC, c.name LIMIT 3")
            else:
                queries.append(query + f" ORDER BY p.index_100 {order}, c.name LIMIT {limit}")
        return queries
    specs = sku_requests(message)
    queries = []
    for role in ('cpu', 'gpu'):
        cols = 'c.cores_total, c.socket, c.base_power_w' if role == 'cpu' else 'c.vram_gib, c.board_power_w'
        filters = ["c.recommendable = true", "p.form_factor = 'desktop'", f"p.index_100 >= b.{role}_index_min"]
        if role == 'gpu':
            filters.extend(gpu_sql_requirements(message))
        required = specs[role]['required']
        if required:
            filters.remove(f"p.index_100 >= b.{role}_index_min")
            # Tokens are regex-limited ASCII SKUs; no user SQL is interpolated.
            filters.append(_sku_sql_filter(required, 'c'))
        elif role == 'gpu' and gpu_series(message):
            filters.append(_gpu_sql_filter(message, 'c'))
        if not required:
            filters.append(_spec_evidence_filter(role, 'c'))
        for token in specs[role]['excluded']:
            filters.append("c.name NOT ILIKE '%" + token.replace(' ', '%') + "%'")
        if role == 'gpu' and specs['cpu']['required'] and not required:
            filters.append("EXISTS (SELECT 1 FROM agent_catalog.performance_ranking cp "
                "WHERE cp.category = 'cpu' AND cp.form_factor = 'desktop' "
                "AND " + _sku_sql_filter(specs['cpu']['required'], 'cp') + " AND cp.index_100 > 0 "
                "AND (b.ratio_min IS NULL OR p.index_100 >= cp.index_100 * b.ratio_min) "
                "AND (b.ratio_max IS NULL OR p.index_100 <= cp.index_100 * b.ratio_max))")
        if role == 'cpu' and not required and _gpu_sql_filter(message, 'gp'):
            filters.append("EXISTS (SELECT 1 FROM agent_catalog.performance_ranking gp "
                "WHERE gp.category = 'gpu' AND gp.form_factor = 'desktop' AND "
                + _gpu_sql_filter(message, 'gp') + " AND gp.index_100 >= b.gpu_index_min "
                + ("AND " + _spec_evidence_filter('gpu', 'gp') + " " if not specs['gpu']['required'] else "")
                + "AND (b.ratio_min IS NULL OR gp.index_100 >= p.index_100 * b.ratio_min) "
                "AND (b.ratio_max IS NULL OR gp.index_100 <= p.index_100 * b.ratio_max))")
        premium = performance_priority(message)
        order = 'DESC' if premium else 'ASC'
        queries.append(f"SELECT c.entity_id, c.entity_key, c.name, {cols} FROM agent_catalog.{role}_catalog c "
            "JOIN agent_catalog.performance_ranking p ON p.entity_key = c.entity_key "
            f"JOIN agent_catalog.balance_rule_catalog b ON b.rule_key = '{rule}' "
            "WHERE b.active AND " + ' AND '.join(filters) + f" ORDER BY p.index_100 {order}, c.name LIMIT 3")
    return queries
