# safety.py
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List, Tuple

@dataclass
class SafetyResult:
    ok: bool
    reasons: List[str]

BANNED: List[Tuple[str, str]] = [
    (r"(死ね|殺|暴力|殴|刺|爆破)", "暴力/過激"),
    (r"(差別|民族|人種|障害者|女は|男は)", "差別/属性一般化"),
    (r"(宗教|信者|カルト)", "宗教"),
    (r"(選挙|政党|首相|政治)", "政治"),
    (r"(住所|電話|メール|LINE|学校|会社|勤務先)", "特定情報"),
    (r"(晒|暴露|特定|通報)", "晒し誘導"),
    (r"(お前|こいつ|あいつ).*(無能|ゴミ|カス)", "個人攻撃"),
]

def safety_check(text: str) -> SafetyResult:
    t = (text or "").strip()
    reasons = []
    if not t:
        return SafetyResult(False, ["空文"])
    if t.count("http") >= 1 or t.count("@") >= 2:
        reasons.append("晒し誘導リスク")
    for pat, msg in BANNED:
        if re.search(pat, t):
            reasons.append(msg)
    return SafetyResult(ok=(len(reasons) == 0), reasons=reasons)

def safety_score_01(text: str) -> float:
    return 1.0 if safety_check(text).ok else 0.0
