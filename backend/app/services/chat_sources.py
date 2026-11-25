"""Map short run-local source references to authoritative original document fragments."""

from app.chat_protocol import Quote
from app.core.errors import ApiError


def evidence_window(execution):
    window, size = [], 0
    for key in execution.evidence_order:
        item = execution.seen.get(key)
        if item is None or size + len(item["content"]) > 4000 or len(window) >= 8:
            continue
        reference = execution.source_refs.setdefault(key, f"E{len(execution.source_refs) + 1}")
        execution.exposed_sources.add(key)
        window.append(
            {
                "chunk_id": reference,
                "title": item["title"],
                "version_no": item["version_no"],
                "anchor": item["anchor"],
                "content": item["content"],
            }
        )
        size += len(item["content"])
    return window


def canonicalize(execution, answer):
    references = {value: key for key, value in execution.source_refs.items()}
    quotes, used = [], set()
    for cite in answer.citations:
        value = str(cite.chunk_id).strip()
        key = references.get(value.upper(), value.lower())
        if key not in execution.exposed_sources or key not in execution.seen:
            execution.citation_error = "unknown_source"
            raise ApiError(422, "INVALID_CITATION", "引用编号不属于本轮可用证据")
        if key in used:
            execution.citation_error = "duplicate_source"
            raise ApiError(422, "INVALID_CITATION", "同一来源不能重复提交")
        item = execution.seen[key]
        # Legacy clients may still supply an exact quote. Never accept a rewritten quote.
        quote = cite.quote or item["content"]
        if quote not in item["content"]:
            execution.citation_error = "quote_mismatch"
            raise ApiError(422, "INVALID_CITATION", "摘录与原文不一致")
        used.add(key)
        quotes.append({"chunk_id": key, "quote": quote})
    if answer.status == "answered" and answer.basis == "documents" and not quotes:
        raise ApiError(422, "MISSING_CITATION", "文档回答需要选择来源编号")
    execution.citation_error = None
    return answer.model_copy(update={"citations": [Quote(**q) for q in quotes]})
