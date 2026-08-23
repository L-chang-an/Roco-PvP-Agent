"""battle environment 包（零第三方依赖，只吃 stdlib）。

里程碑线：E0a 数据与组队层 → E0b 回合内核 → E1–E7。
E0a 阶段只做包标记；E0b 再补对外 re-export（new_battle / BattleSession / run_match …）。
"""
