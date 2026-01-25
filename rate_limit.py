# rate_limit.py
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import List, Tuple

@dataclass
class Limits:
    rpm: int = 5
    tpm: int = 250   # 1分あたりの入力トークン上限（目安）
    rpd: int = 20

def rough_token_count(text: str) -> int:
    # 目安：日本語混在でも「長文を抑える」用途。過小評価しないよう少し厳しめ。
    # ざっくり「文字数/1.8」くらいで見積もる（=多めにカウント）
    return max(1, int(len(text) / 1.8))

class RateLimiter:
    """
    - RPM: 直近60秒の呼び出し回数
    - TPM: 直近60秒の入力トークン合計（rough）
    を両方満たすまで待つ
    """
    def __init__(self, limits: Limits):
        self.limits = limits
        self.calls: List[float] = []                # timestamps
        self.tokens: List[Tuple[float, int]] = []   # (timestamp, tokens)

    def _prune(self, now: float):
        self.calls = [t for t in self.calls if now - t < 60]
        self.tokens = [(t, k) for (t, k) in self.tokens if now - t < 60]

    def wait(self, input_tokens: int = 1):
        while True:
            now = time.time()
            self._prune(now)

            rpm_ok = (len(self.calls) < self.limits.rpm)
            tpm_used = sum(k for _, k in self.tokens)
            tpm_ok = (tpm_used + input_tokens <= self.limits.tpm)

            if rpm_ok and tpm_ok:
                self.calls.append(time.time())
                self.tokens.append((time.time(), int(input_tokens)))
                return

            # 次に空く最短時間を計算して sleep
            sleep_rpm = 0.0
            if not rpm_ok and self.calls:
                sleep_rpm = 60 - (now - self.calls[0]) + 0.2

            sleep_tpm = 0.0
            if not tpm_ok and self.tokens:
                # 先頭が窓から落ちるまで待つ
                sleep_tpm = 60 - (now - self.tokens[0][0]) + 0.2

            time.sleep(max(0.2, min(sleep_rpm or 999, sleep_tpm or 999)))