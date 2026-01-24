# rating.py
def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x

def tweet_score(
    impr: int,
    likes: int,
    rts: int,
    replies: int,
    fol_before: int,
    fol_after: int,
) -> float:
    """
    0〜1付近に収まる“報酬”を作る（将棋の勝率みたいなもの）
    指標は減らさず全部使う。
    """
    impr = max(1, int(impr))
    likes = max(0, int(likes))
    rts = max(0, int(rts))
    replies = max(0, int(replies))

    fol_before = max(0, int(fol_before))
    fol_after = max(0, int(fol_after))
    dfol = max(0, fol_after - fol_before)

    # 率
    like_rate = likes / impr
    rt_rate = rts / impr
    reply_rate = replies / impr

    # フォロ増は希少なので重め（ただし暴れないように抑制）
    fol_gain = dfol
    fol_term = (fol_gain ** 0.5) * 0.01  # 緩やか

    # 合成（調整可能）
    s = 0.70 * like_rate + 0.20 * rt_rate + 0.10 * reply_rate + fol_term
    return clamp(s, 0.0, 1.0)

def update_abs_rating(r: float, score: float, baseline: float = 1000.0, k: float = 16.0) -> float:
    """
    絶対レート：baselineを“引き分け点”みたいに見立てて更新
    scoreが高いほど上がる（0〜1）
    """
    # 期待値（0.5中心に寄せる）
    expected = 0.5
    actual = clamp(score, 0.0, 1.0)
    return r + k * (actual - expected)

def update_rel_rating(r: float, score: float, self_baseline: float = 0.02, k: float = 16.0) -> float:
    """
    相対レート：自分の直近平均 self_baseline を相手の強さと見立てる
    score > self_baseline なら上がる、低ければ下がる
    """
    actual = clamp(score, 0.0, 1.0)
    expected = clamp(self_baseline / max(1e-6, (self_baseline + 0.05)), 0.05, 0.95)
    # expectedを0.5近辺に収める簡易式
    expected = 0.5 + (expected - 0.5) * 0.6
    return r + k * (actual - expected)
