def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))

def tweet_score(impr: int, likes: int, rts: int, replies: int, fol_before: int, fol_after: int) -> float:
    if impr <= 0:
        return 0.0
    follow_gain_rate = max(0, fol_after - fol_before) / impr
    engagement_rate = (likes + rts + replies) / impr
    rt_rate = rts / impr
    like_rate = likes / impr

    score = (
        0.45 * clamp(follow_gain_rate) +
        0.30 * clamp(engagement_rate) +
        0.15 * clamp(rt_rate) +
        0.10 * clamp(like_rate)
    )
    return clamp(score)

def expected_score(rating: float, baseline: float = 1000.0) -> float:
    return 1 / (1 + 10 ** ((baseline - rating) / 400))

def update_abs_rating(rating: float, score_0_1: float, baseline: float = 1000.0, k: float = 16.0) -> float:
    exp = expected_score(rating, baseline)
    return rating + k * (score_0_1 - exp)

def update_rel_rating(rel_rating: float, score_0_1: float, self_baseline: float, k: float = 16.0) -> float:
    # 自己平均との差分（分かりやすさ担当）
    delta = (score_0_1 - self_baseline)
    return rel_rating + (k * 2.0) * delta
