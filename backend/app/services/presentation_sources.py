"""Resolve public citations only from the selected candidates' verified evidence."""
from urllib.parse import urlsplit
from sqlalchemy import select
from app.models.truth_v3 import EvidenceClaim, SourceDocument


def attach_sources(session, narration, result):
    ids = {item.candidate_id for item in narration.core_build.core} if narration.core_build else set()
    ids.update(item.candidate_id for item in getattr(narration, 'selections', []))
    selected = [candidate for candidate in result.top_k if candidate.candidate_id in ids] if ids else result.top_k[:1]
    evidence_ids = {evidence_id for candidate in selected for claim in candidate.claims
                    if claim.verification.status == "supported" for evidence_id in claim.verification.valid_evidence_ids}
    if not evidence_ids:
        return
    rows = session.execute(select(SourceDocument.title, SourceDocument.url, EvidenceClaim.field_key)
        .join(EvidenceClaim, EvidenceClaim.source_id == SourceDocument.id)
        .where(EvidenceClaim.id.in_(evidence_ids), EvidenceClaim.review_status == "accepted")
        .order_by(SourceDocument.url, EvidenceClaim.field_key)).all()
    seen = set()
    sources = []
    for title, url, field in rows:
        try:
            parsed = urlsplit(url)
        except (ValueError, TypeError):
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or (url, field) in seen:
            continue
        seen.add((url, field))
        sources.append({"title": title, "url": url, "field": field})
    narration.sources = sources[:8]
