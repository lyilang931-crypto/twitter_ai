# safety.py — 安全ガード（炎上/凍結回避）+ トピックリスク + 毒性検出
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any


@dataclass
class SafetyResult:
    ok: bool
    reasons: List[str]


@dataclass
class TopicRiskResult:
    """トピックリスク判定の結果。"""
    risky: bool
    categories: List[str] = field(default_factory=list)
    auto_post_allowed: bool = True


@dataclass
class ToxicityResult:
    """毒性検出の結果。"""
    toxic: bool
    matches: List[str] = field(default_factory=list)
    severity: str = "none"  # "none", "low", "medium", "high"


# --- 既存の安全パターン ---
BANNED: List[Tuple[str, str]] = [
    (r"(死ね|殺|暴力|殴|刺|爆破)", "暴力/過激"),
    (r"(差別|民族|人種|障害者|女は|男は)", "差別/属性一般化"),
    (r"(宗教|信者|カルト)", "宗教"),
    (r"(選挙|政党|首相|政治)", "政治"),
    (r"(住所|電話|メール|LINE|学校|会社|勤務先)", "特定情報"),
    (r"(晒|暴露|特定|通報)", "晒し誘導"),
    (r"(お前|こいつ|あいつ).*(無能|ゴミ|カス)", "個人攻撃"),
]


# --- 自動投稿を禁止するトピックカテゴリ ---
TOPIC_RISK_PATTERNS: List[Tuple[str, str]] = [
    # 政治
    (r"(選挙|政党|首相|与党|野党|衆議院|参議院|内閣|大臣|国会|自民|立憲|共産|公明|維新|総理|政権|投票|マニフェスト|公約)", "政治"),
    # 宗教
    (r"(宗教|信者|カルト|布教|教祖|信仰|創価|統一教会|エホバ|オウム)", "宗教"),
    # 戦争/紛争
    (r"(戦争|紛争|空爆|侵攻|ミサイル|核兵器|軍事|テロ|武装|ウクライナ|ガザ|パレスチナ)", "戦争/紛争"),
    # 事件/事故
    (r"(殺人|逮捕|容疑者|被害者|事故死|死亡事故|轢き逃げ|痴漢|性犯罪|詐欺)", "事件/事故"),
    # 医療/健康断定
    (r"(必ず治る|確実に効く|万能薬|ワクチン.*(危険|毒)|反ワクチン|がん.*(治る|消える)|健康.*(絶対|必ず))", "医療/健康断定"),
    # 投資の断定的助言
    (r"(必ず儲かる|絶対に上がる|確実に稼げる|元本保証|ノーリスク.*利益|年利.*%.*確実|投資.*損しない)", "投資断定助言"),
]

# --- 毒性語辞書（誹謗中傷・断定攻撃・差別語・過激表現） ---
TOXICITY_PATTERNS: List[Tuple[str, str, str]] = [
    # (パターン, カテゴリ, 重要度)
    (r"(死ね|死んで|消えろ|くたばれ|殺す)", "直接的暴言", "high"),
    (r"(ゴミ|カス|クズ|バカ|アホ|馬鹿|間抜け|能無し|無能)", "侮辱語", "medium"),
    (r"(キモい|キモ|ブス|デブ|ハゲ|チビ)", "外見侮辱", "medium"),
    (r"(障害者|ガイジ|池沼|知恵遅れ|めくら|つんぼ)", "差別語", "high"),
    (r"(在日|チョン|支那|ニガー|黒人.*劣)", "民族差別", "high"),
    (r"(女は黙|男は黙|女のくせに|男のくせに)", "性差別", "medium"),
    (r"(お前.*(ダメ|最低|終わ|無理)|こいつ.*(ヤバい|終わ|ダメ))", "断定攻撃", "medium"),
    (r"(全員.*(バカ|アホ|無能|終わ)|誰もが.*(ダメ|無理))", "集団攻撃", "medium"),
    (r"(炎上させ|拡散しろ|晒せ|特定しろ|住所.*晒)", "扇動", "high"),
]

# --- 他者を断定評価する文脈パターン ---
JUDGMENTAL_PATTERNS: List[str] = [
    r"(あの人|この人|あいつ|こいつ|彼|彼女).*(無能|ダメ|最低|終わってる|バカ|失格|クズ)",
    r"(は|って).*(詐欺師|嘘つき|犯罪者|テロリスト|売国奴)",
]


def safety_check(text: str) -> SafetyResult:
    """既存の安全チェック（後方互換を維持）。"""
    t = (text or "").strip()
    reasons: List[str] = []
    if not t:
        return SafetyResult(False, ["空文"])
    if t.count("http") >= 1 or t.count("@") >= 2:
        reasons.append("晒し誘導リスク")
    for pat, msg in BANNED:
        if re.search(pat, t):
            reasons.append(msg)
    return SafetyResult(ok=(len(reasons) == 0), reasons=reasons)


def safety_score_01(text: str) -> float:
    """既存の 0/1 安全スコア（後方互換）。"""
    return 1.0 if safety_check(text).ok else 0.0


def detect_topic_risk(text: str, topic: str = "") -> TopicRiskResult:
    """テーマ/本文がリスクトピックに該当するか判定する。
    該当した場合、auto_post_allowed=False（自動投稿禁止→手動承認へ）。
    """
    combined = f"{topic} {text}".strip()
    if not combined:
        return TopicRiskResult(risky=False, categories=[], auto_post_allowed=True)

    categories: List[str] = []
    for pat, cat in TOPIC_RISK_PATTERNS:
        if re.search(pat, combined):
            if cat not in categories:
                categories.append(cat)

    risky = len(categories) > 0
    return TopicRiskResult(
        risky=risky,
        categories=categories,
        auto_post_allowed=not risky,
    )


def detect_toxicity(text: str) -> ToxicityResult:
    """本文中の誹謗中傷・差別語・過激表現を検出する。"""
    t = (text or "").strip()
    if not t:
        return ToxicityResult(toxic=False, matches=[], severity="none")

    matches: List[str] = []
    max_severity = "none"
    severity_order = {"none": 0, "low": 1, "medium": 2, "high": 3}

    for pat, category, severity in TOXICITY_PATTERNS:
        if re.search(pat, t):
            matches.append(category)
            if severity_order.get(severity, 0) > severity_order.get(max_severity, 0):
                max_severity = severity

    # 他者を断定評価する文脈
    for pat in JUDGMENTAL_PATTERNS:
        if re.search(pat, t):
            if "断定評価" not in matches:
                matches.append("断定評価")
            if severity_order.get("medium", 0) > severity_order.get(max_severity, 0):
                max_severity = "medium"

    return ToxicityResult(
        toxic=len(matches) > 0,
        matches=matches,
        severity=max_severity,
    )


def compute_safety_score(text: str, topic: str = "") -> Dict[str, Any]:
    """統合安全スコアを算出する（自動化タブ用）。

    Returns:
        dict with keys:
        - safety_score: float 0.0..1.0
        - auto_post_ok: bool
        - flags: dict of risk details
    """
    base = safety_check(text)
    topic_risk = detect_topic_risk(text, topic)
    toxicity = detect_toxicity(text)

    flags: Dict[str, Any] = {}
    score = 1.0

    # 基本安全チェック
    if not base.ok:
        score -= 0.3
        flags["safety_reasons"] = base.reasons

    # トピックリスク
    if topic_risk.risky:
        score -= 0.2 * len(topic_risk.categories)
        flags["topic_risk"] = topic_risk.categories

    # 毒性
    if toxicity.toxic:
        severity_penalty = {"none": 0.0, "low": 0.1, "medium": 0.3, "high": 0.5}
        score -= severity_penalty.get(toxicity.severity, 0.3)
        flags["toxicity"] = toxicity.matches
        flags["toxicity_severity"] = toxicity.severity

    score = max(0.0, min(1.0, score))

    # 自動投稿の可否
    auto_post_ok = (
        base.ok
        and topic_risk.auto_post_allowed
        and not toxicity.toxic
        and score >= 0.7
    )

    return {
        "safety_score": round(score, 3),
        "auto_post_ok": auto_post_ok,
        "flags": flags,
    }
