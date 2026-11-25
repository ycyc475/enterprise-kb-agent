"""Safe public error explanations. No exception bodies or document content."""

MESSAGES = {
    "INVALID_CITATION": (
        "引用校验未通过，已阻止发布无依据答案。请重新提问或核对原文。",
        "citation",
    ),
    "MISSING_CITATION": ("回答没有提供可核对的来源，已阻止发布。请重新提问。", "citation"),
    "EVIDENCE_REVIEW_FAILED": ("证据核验未完成，未发布候选答案。请稍后重试。", "review"),
    "INVALID_MODEL_OUTPUT": (
        "模型返回格式不符合要求，本轮未能形成有效回答。请重新提问。",
        "generation",
    ),
    "CONTEXT_TOO_LONG": ("本轮上下文过长，请缩短问题或新建会话。", "generation"),
    "PROVIDER_UNAVAILABLE": ("模型服务暂不可用，请稍后重试。", "generation"),
    "SEARCH_LIMIT": ("本轮检索额度已用完。已找到的原文仍可核对。", "search"),
    "MODEL_LIMIT": (
        "本轮剩余模型额度不足以完成回答及核验，未发布未经核验的候选答案。",
        "generation",
    ),
    "TOOL_LIMIT": ("本轮工具调用额度已用完，请缩小问题范围。", "tool"),
    "DEADLINE": ("本轮达到时间上限，请缩小问题范围或稍后重试。", "runtime"),
    "BUDGET_PAUSED": ("应用预算暂不允许新增模型调用，请联系管理员查看用量。", "budget"),
    "INTERRUPTED": ("服务重启中断了本轮，请重新提问。", "runtime"),
}


def public_error(code, detail=None):
    message, stage = MESSAGES.get(code, ("本轮状态已变化，请刷新后重新提问。", "runtime"))
    result = {"code": code, "message": message, "stage": stage}
    if detail in {"unknown_source", "quote_mismatch", "duplicate_source"}:
        result["detail"] = detail
    return result
