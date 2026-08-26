"""注入式种子 RNG：引擎的全部随机性收进这一个类。

seed 是必填位置参数——不存在"未注入时回退全局 random"的分支（见计划 §8 陷阱 2）。
只暴露 `choice()`：E0 的引擎随机点只有队列平手硬币（跨方完全平手才抽），
以及玩家的独立随机流（Player 各自持有一个 BattleRng，绝不共用引擎的流）。

`from_dict` 还原 RNG 的方式：`Random(seed)` 后**丢弃 calls 次抽取**，随机流位置
完全复原。这里 `__post_init__` 用 `choice((0, 1))` 重放，与引擎真正的抽取
（build_queue 的硬币，同样是 2 选 1）逐位一致。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class BattleRng:
    """种子必填的确定性 RNG。`calls` 是抽取计数，进 `to_dict()`/`audit()`。

    `_rng` 不进序列化：还原靠 seed + calls 重放，不靠保存对象本身。
    """

    seed: int
    calls: int = 0
    _rng: random.Random = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        for _ in range(self.calls):
            self._rng.choice((0, 1))  # 重放 calls 次抽取，复原随机流位置

    def choice(self, seq):
        """抽一个元素并计数。这是引擎唯一允许的随机入口。"""
        self.calls += 1
        return self._rng.choice(seq)

    def audit(self) -> dict:
        """只把 seed/calls 给到 to_dict——`_rng` 是运行时服务，不序列化。"""
        return {"seed": self.seed, "calls": self.calls}
