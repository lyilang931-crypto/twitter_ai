# rate_limit.py
from __future__ import annotations
import time
from dataclasses import dataclass

@dataclass
class Limits:
    rpm: int = 5
    tpm: int = 250   # 入力トークンの制限（目安）
    rpd: int = 20

class RateLimiter:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.calls = []  # timestamps (last 60s)

    def wait_for_rpm(self):
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) >= self.limits.rpm:
            sleep = 60 - (now - self.calls[0]) + 0.2
            time.sleep(max(0.0, sleep))
        self.calls.append(time.time())

def rough_token_count(text: str) -> int:
    # 超雑な推定：日本語は1文字=1トークンではないが、TPM250の“入力短縮”の目安として使う
    return max(1, int(len(text) / 2.2))