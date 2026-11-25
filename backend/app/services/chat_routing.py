"""Conservative routing for public assistant information; business questions keep RAG."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionRoute:
    topics: tuple[str, ...]
    knowledge_question: str


SUBJECT = r"(?:你|您|本系统|这个系统|当前系统|系统)"
WHEN = r"(?:现在|目前|当前)?"
MODEL = r"(?:大语言|对话|聊天|语言|大)?模型(?:型号)?"
WHICH = r"(?:什么|哪个|哪种|哪款|哪一款)"
PATTERNS = {
    "model": [
        rf"{SUBJECT}{WHEN}(?:是|用的是|使用的是|用|使用|调用|采用|接入|基于)?{WHICH}{MODEL}",
        rf"{SUBJECT}{WHEN}的?{MODEL}(?:是|叫)?{WHICH}(?:型号)?",
        rf"{WHEN}(?:使用|调用|配置|接入)的?{MODEL}(?:是)?{WHICH}",
        rf"{SUBJECT}{WHEN}(?:用|使用)的(?:是)?{MODEL}",
        rf"{SUBJECT}{WHEN}是(?:deepseek|qwen[\w.\-]*|通义千问|chatgpt|gpt[\w.\-]*)",
        rf"(?:{SUBJECT}{WHEN}(?:使用|用|配置)的?)?向量(?:化)?模型(?:是|用)?{WHICH}",
    ],
    "identity": [rf"{SUBJECT}{WHEN}是(?:谁|什么助手|什么系统)", r"你叫什么(?:名字)?"],
    "capabilities": [
        rf"{SUBJECT}{WHEN}(?:能|可以)(?:帮我)?(?:做什么|干什么|提供什么帮助|做哪些事情)",
        rf"{SUBJECT}{WHEN}(?:有|支持|具备)(?:什么|哪些)(?:功能|能力)",
        rf"{SUBJECT}{WHEN}(?:能|可以)(?:直接|自动)?"
        r"(?:发布代码|部署服务|审批报销|审批发布|修改权限|执行生产操作|联网|上网)",
        r"(?:介绍一下|介绍)(?:你的功能|你的能力|你自己)",
    ],
    "upload_help": [
        r"(?:我|管理员|员工)?(?:该|应该|可以|能)?(?:怎么|如何)(?:才能)?"
        r"(?:上传|导入)(?:企业)?(?:知识库)?文档(?:到知识库)?",
    ],
    "confirmation_help": [
        r"(?:为什么|为何)(?:必须|需要|要)(?:先)?确认(?:出差|差旅|行程|预算)?条件",
    ],
    "greeting": [r"(?:你好|您好|嗨|hello|hi|早上好|晚上好|在吗)"],
    "thanks": [r"(?:谢谢|感谢|多谢)"],
}


def topic_for(clause):
    text = re.sub(r"\s+", "", clause).lower()
    text = re.sub(r"^(?:请问一下|请问|请告诉我|告诉我|我想问一下|想问一下|麻烦|请)", "", text)
    text = re.sub(r"(?:吗|呢|呀|啊|吧)+$|(?<!什)么$", "", text)
    for topic, patterns in PATTERNS.items():
        if any(re.fullmatch(pattern, text) for pattern in patterns):
            return topic
    return None


def route_question(question):
    # Match complete clauses, never just the words "模型" or "你好" inside a document question.
    clauses = re.split(
        r"[，,。！？!?；;\n]+|(?:另外|以及|并且|顺便|同时|还有|然后)(?:再)?|\s+and\s+",
        question,
        flags=re.IGNORECASE,
    )
    topics, business = [], []
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        topic = topic_for(clause)
        if topic:
            if topic not in topics:
                topics.append(topic)
        else:
            business.append(clause)
    return QuestionRoute(tuple(topics), "；".join(business) if topics else question.strip())


def system_answer(route, metadata):
    replies = []
    for topic in route.topics:
        if topic == "model":
            chat = metadata.get("chat_model") or "尚未配置"
            embedding = metadata.get("embedding_model") or "尚未配置"
            dimensions = metadata.get("embedding_dimensions")
            suffix = f"（{dimensions}维）" if dimensions else ""
            replies.append(
                f"我是企业知识助手。本轮配置的对话模型为 {chat}；"
                f"知识检索使用的向量模型为 {embedding}{suffix}。"
            )
        elif topic == "identity":
            replies.append("我是企业知识助手，可以帮助你查询有权访问的企业文档并核对原文依据。")
        elif topic == "capabilities":
            replies.append(
                "我可以检索有权访问的企业文档、依据原文回答并提供引用，"
                "也可以读取你本人的员工资料，提取出差条件供你确认后由程序估算预算。"
                "我不能联网查询、自动发布代码、部署服务、审批报销或修改账号和知识库权限。"
            )
        elif topic == "upload_help":
            replies.append(
                "文档需要由管理员上传：进入文档页面，选择目标知识库，点击“上传文档”，"
                "填写标题并选择MD、文字PDF或DOCX文件，点击“上传并建立索引”。"
                "等待状态变为“已就绪”后核对片段，再在相应授权知识库中提问。"
                "员工没有上传权限时，请联系管理员处理。"
            )
        elif topic == "confirmation_help":
            replies.append(
                "自然语言提取的出差条件可能有遗漏或歧义，因此需要先核对目的地、天数和晚数。"
                "你确认后，程序才会使用管理员维护的职级和有效费用规则计算预算；"
                "系统不会从天数自动推断住宿晚数。"
            )
        elif topic == "thanks":
            replies.append("不客气！还有需要核对的制度或办理事项，可以继续问我。")
        else:
            replies.append("你好！你可以向我询问企业制度、办理流程或差旅预算。")
    return "\n\n".join(replies)
