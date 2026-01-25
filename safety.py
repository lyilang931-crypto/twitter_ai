# safety.py
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List, Tuple

@dataclass
class SafetyResult:
    ok: bool
    reasons: List[str]

BANNED_PATTERNS: List[Tuple[str, str]] = [
    (r"(死ね|殺|刺|殴|暴力|爆破|テロ)", "暴力・過激表現"),
    (r"(差別|人種|民族|障害者|女は|男は|在日)", "差別・属性一般化リスク"),
    (r"(宗教|信者|カルト)", "宗教話題リスク"),
    (r"(政党|首相|選挙|右翼|左翼|政治)", "政治話題リスク"),
    (r"(住所|電話|メール|LINE|学校|勤務先|会社名)", "個人特定リスク"),
    (r"(晒|さらす|暴露|特定|通報)", "晒し・攻撃誘導リスク"),
]

ATTACK_HINTS: List[Tuple[str, str]] = [
    (r"(お前|こいつ|あいつ).*(無能|ゴミ|カス|終わってる)", "個人攻撃リスク"),
]

def safety_check(text: str) -> SafetyResult:
    t = (text or "").strip()
    reasons: List[str] = []
    if not t:
        return SafetyResult(ok=False, reasons=["空文"])

    if t.count("http") >= 1 or t.count("@") >= 2:
        reasons.append("URL/メンション多め（晒し誘導リスク）")

    for pat, msg in BANNED_PATTERNS:
        if re.search(pat, t):
            reasons.append(msg)

    for pat, msg in ATTACK_HINTS:
        if re.search(pat, t):
            reasons.append(msg)

    ok = len(reasons) == 0
    return SafetyResult(ok=ok, reasons=reasons)

def safety_score_01(text: str) -> float:
    return 1.0 if safety_check(text).ok else 0.0