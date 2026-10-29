"""安全层：红旗规则、注入检测、输出校验、脱敏。

架构 §2.2 的两条硬约束在这里：
1. 本模块**不得依赖 llm_gateway**——红旗必须能在模型全挂时生效。
2. 全是纯规则代码，无网络、无模型调用。

红旗检测必须放在意图识别**之前**（架构 §5.1）：否则「我胸痛，挂什么科」
会先进科室匹配，延迟急救引导。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- 词库

# 身份词库（PRD §4.1）：作用于人设 name / 自称 / 世界观 / 开场白 / 称谓
FORBIDDEN_IDENTITY_WORDS: tuple[str, ...] = (
    "医生", "医师", "大夫", "主任", "专家", "教授", "博士", "护士", "药师",
    "门诊", "诊所", "医院", "科室", "处方", "临床", "从医", "行医", "执业",
    "病例", "患者", "病人", "医学博士", "主治",
)

# 资历表述模式（PRD §4.1：比穷举禁用词有效）
CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"我(有|当|做|干)了?[零一二三四五六七八九十百\d]+\s*年"),
    re.compile(r"我(治|看|接诊)过"),
    re.compile(r"我看过\s*[一二三四五六七八九十百千\d]+\s*(个|位|名)"),
    re.compile(r"我是.{0,8}(医生|医师|大夫|主任|专家|教授|博士|护士)"),
    re.compile(r"我在.{0,10}(医院|诊所|门诊|科室|医学院)"),
    re.compile(r"我(毕业于|就读于).{0,10}(医学院|医科大学|医学)"),
)

# 提示词注入（PRD §13.3）
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_prev", re.compile(r"忽略.{0,6}(之前|以上|上面|前述)的?(指令|提示|要求|规则|设定)", re.I)),
    ("ignore_en", re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions?", re.I)),
    ("role_reassign", re.compile(r"(你是|你现在是|从现在开始你是|假装你是|扮演|act as).{0,12}(医生|医师|大夫|主任|专家|教授|医学博士)", re.I)),
    ("dev_mode", re.compile(r"(进入|开启|切换到).{0,6}(开发者|debug|Debug|越狱|jailbreak|DAN)模式?")),
    ("sys_prompt", re.compile(r"(系统提示|system prompt|初始指令|你的指令|prompt 是|提示词是)", re.I)),
    ("disobey", re.compile(r"(不要|无需|不用)(遵守|遵循|理会).{0,8}(规则|限制|约束|设定)")),
    ("prescribe_coerce", re.compile(r"(直接|马上|立刻).{0,4}(开方|开药|处方|给我.{0,4}药|告诉我吃什么药)")),
)

# 输出禁用（PRD §1.3 / §13.1）
OUTPUT_BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("diagnosis", re.compile(r"(确诊|你患了|你这是.{0,8}(病|炎|症|癌)|诊断为|就是.{0,6}病)")),
    ("dose", re.compile(r"\d+\s*(mg|毫克|ml|毫升|片|粒|支|g|克)\b", re.I)),
    ("prescribe", re.compile(r"(建议服用|建议吃|可以服用|应该吃|开.{0,4}药|处方)")),
    ("credential", re.compile(r"|".join(p.pattern for p in CREDENTIAL_PATTERNS))),
    ("cure_promise", re.compile(r"(保证(治好|有效|没事)|一定能(好|治愈)|根治|百分百|肯定没事|绝对安全)")),
    ("emotional_hook", re.compile(r"(我会一直陪着你|只有我懂你|你只需要我)")),
)

# PII
RE_PHONE = re.compile(r"1[3-9]\d{9}")
RE_IDCARD = re.compile(r"\b\d{17}[\dXx]\b")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


# ---------------------------------------------------------------- 红旗规则

# PRD §13.2 的 11 条。`any` 表示「还需命中其中一个」，留空则只按 keywords 命中。
# 对应数据库 redflag_rule 的 keywords / logic_expr（logic_expr 形如 "any:出汗|左臂"）。
REDFLAG_SEED: tuple[dict, ...] = (
    dict(id="chest_pain", name="胸痛伴危险信号", level="urgent", action="call_120",
         # 注意：真实用户很少说"胸痛"，多说"胸口疼/心口疼/前胸疼"。
         # 关键词要按口语覆盖，不能只写书面语（实测踩过："胸口疼得厉害"漏检）
         keywords=["胸痛", "胸闷", "胸口疼", "胸口痛", "前胸疼", "前胸痛", "心口疼", "心口痛",
                   "心前区痛", "压榨样疼痛", "胸部压着"],
         any_of=["出汗", "冷汗", "左臂", "放射", "濒死", "大汗", "恶心呕吐"],
         message="胸痛伴这些表现可能是心脏急症。请立即拨打 120，不要自行驾车前往。"),
    dict(id="stroke", name="卒中预警", level="urgent", action="call_120",
         keywords=["口角歪斜", "肢体无力", "言语不清", "说话不清", "说不清", "说不清楚",
                   "半身麻木", "一侧手脚动不了", "嘴歪", "嘴巴歪", "嘴角歪", "脸歪"],
         any_of=[], message="这些是脑卒中（中风）的典型信号，每分钟都很关键。请立即拨打 120。"),
    dict(id="dyspnea", name="呼吸困难", level="urgent", action="call_120",
         keywords=["呼吸困难", "喘不过气", "喘不上气", "上不来气", "透不过气", "气促",
                   "口唇发紫", "嘴唇发紫", "不能平卧", "无法平卧", "憋气", "呼吸急促"],
         any_of=[], message="呼吸困难可能危及生命。请立即拨打 120 或前往最近急诊。"),
    dict(id="bleeding", name="大量出血", level="urgent", action="encourage_emergency",
         keywords=["大量出血", "呕血", "黑便", "血不止", "止不住血", "便血"],
         any_of=[], message="活动性出血需要立即处理。请马上前往急诊，不要等待。"),
    dict(id="unconscious", name="意识障碍", level="urgent", action="call_120",
         keywords=["意识丧失", "抽搐", "昏迷", "呼之不应", "叫不醒", "晕倒", "翻白眼"],
         any_of=[], message="出现意识改变请立即拨打 120，侧卧防误吸，不要喂水喂药。"),
    dict(id="thunderclap", name="雷击样头痛", level="urgent", action="encourage_emergency",
         keywords=["雷击样头痛", "炸裂样头痛", "剧烈头痛", "头痛得厉害", "从没这么痛"],
         any_of=[], message="突发剧烈头痛需要急诊排除蛛网膜下腔出血。请立即前往急诊。"),
    dict(id="fever_neck", name="高热伴脑膜刺激", level="urgent", action="encourage_emergency",
         keywords=["高热", "发高烧", "发烧"],
         any_of=["颈项强直", "脖子硬", "皮下出血点", "出血点", "剧烈呕吐"],
         message="高热伴这些表现提示可能为颅内感染，请立即急诊。"),
    dict(id="pregnancy", name="产科急症", level="urgent", action="encourage_emergency",
         keywords=["阴道出血", "胎动消失", "孕妇腹痛", "见红", "肚子疼"],
         any_of=["怀孕", "孕", "胎儿", "胎动"],
         message="孕期出现这些情况请立即前往产科急诊。"),
    dict(id="child_critical", name="儿童危急表现", level="urgent", action="call_120",
         keywords=["孩子", "小孩", "婴儿", "宝宝", "儿子", "女儿", "儿童", "娃"],
         any_of=["抽搐", "呼吸急促", "精神萎靡", "嗜睡", "叫不醒", "皮肤发花"],
         message="儿童出现这些表现变化很快，请立即前往儿科急诊或拨打 120。"),
    dict(id="poisoning", name="中毒/过量", level="urgent", action="call_120",
         keywords=["服毒", "过量服药", "药物过量", "吃了很多药", "喝了很多酒", "农药", "老鼠药", "割腕", "自伤"],
         any_of=[], message="疑似中毒请立即拨打 120，带上药品包装或剩余物，不要催吐。"),
    dict(id="suicide", name="自伤自杀意念", level="crisis", action="show_card",
         keywords=["自杀", "不想活", "活不下去", "结束生命", "轻生", "自残", "想死", "了结自己"],
         any_of=[], message="谢谢你说出来。请现在就联系 24 小时心理援助热线 400-161-9995，或拨打 120。你不需要一个人扛。"),
)

LEVEL_RANK = {"none": 0, "attention": 1, "urgent": 2, "crisis": 3}


@dataclass(frozen=True)
class RedFlagHit:
    rule_id: str
    level: str  # attention/urgent/crisis
    action: str  # show_card/call_120/encourage_emergency
    message: str


@dataclass(frozen=True)
class PostcheckResult:
    ok: bool
    reason: str | None = None  # 命中的规则名


def _match_keywords(text: str, keywords: list[str]) -> bool:
    return any(k and k in text for k in keywords)


def detect_redflag(text: str, rules: list[dict] | None = None) -> RedFlagHit | None:
    """返回命中的红旗。

    同级时取**更具体**的那条：命中条件越多越具体。所以「孩子抽搐」走 child_critical
    （儿童名词 + 抽搐，两个条件）而不是 unconscious（只命中抽搐），给家长的提示更准。
    PRD §18 要求 30 条测试集召回 100%。
    """
    best: RedFlagHit | None = None
    best_specificity = -1
    for rule in rules if rules is not None else REDFLAG_SEED:
        matched = [k for k in rule["keywords"] if k and k in text]
        if not matched:
            continue
        any_of = rule.get("any_of") or rule.get("logic_any") or []
        matched_any: list[str] = []
        if any_of:
            matched_any = [k for k in any_of if k and k in text]
            if not matched_any:
                continue
        hit = RedFlagHit(rule["id"], rule["level"], rule["action"], rule["message"])
        rank = (LEVEL_RANK[hit.level], len(matched) + len(matched_any))
        if rank > (LEVEL_RANK[best.level] if best else -1, best_specificity):
            best, best_specificity = hit, rank[1]
    return best


def detect_injection(text: str) -> str | None:
    """命中返回规则名，未命中返回 None。降级策略是宁可误拦（PRD §14 #3）。"""
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return name
    return None


def check_persona_text(*fields: str | None) -> list[str]:
    """人设字段的禁用词与资历表述检查。返回违规原因列表，空列表 = 通过。"""
    problems: list[str] = []
    for field in fields:
        if not field:
            continue
        for word in FORBIDDEN_IDENTITY_WORDS:
            if word in field:
                problems.append(f"「{field}」包含身份词「{word}」，换个说法再试试")
                break
        else:
            for pattern in CREDENTIAL_PATTERNS:
                if pattern.search(field):
                    problems.append(f"「{field}」疑似在描述医疗资历，人设可以有性格，但不能有执业背景")
                    break
    return problems


def postcheck(text: str) -> PostcheckResult:
    """生成侧后置校验（L0）。不诊断、不开方、不宣称资历、不承诺疗效。"""
    for name, pattern in OUTPUT_BANNED_PATTERNS:
        if pattern.search(text):
            return PostcheckResult(False, name)
    return PostcheckResult(True)


def mask_pii(text: str | None, known_names: list[str] | None = None) -> str | None:
    """落 ai_log 前脱敏。姓名只能从档案里已知的姓名列表匹配，无 NER。"""
    if not text:
        return text
    masked = RE_PHONE.sub(lambda m: f"{m.group()[:3]}****{m.group()[-4:]}", text)
    masked = RE_IDCARD.sub(lambda m: m.group()[:6] + "********" + m.group()[-4:], masked)
    masked = RE_EMAIL.sub("[邮箱]", masked)
    for name in known_names or []:
        if len(name) >= 2:
            masked = masked.replace(name, name[0] + "*" * (len(name) - 1))
    return masked


# ---------------------------------------------------------------- 按句缓冲

_SENTENCE_END = "。！？；!?;\n"


def iter_sentences(text: str) -> tuple[list[str], str]:
    """把一段文本拆成完整句子 + 剩余半句。

    SSE 契约要求一个 delta 至少是一个完整句子——这是后置校验能生效的前提，
    用户永远看不到未校验的半句。
    """
    sentences: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_END:
            chunk = text[start : index + 1]
            if chunk.strip():
                sentences.append(chunk)
            start = index + 1
    return sentences, text[start:]


INJECTION_REFUSAL = (
    "我是 AI 健康助手，不是医生，也不能替代医生面诊。"
    "如果你身体不舒服，可以说说症状，我帮你理清楚、找到合适的科室；"
    "情况紧急的话请直接拨打 120。"
)

REDFLAG_OFFER = "可以先去急诊，同时把情况整理清楚给医生看。"
