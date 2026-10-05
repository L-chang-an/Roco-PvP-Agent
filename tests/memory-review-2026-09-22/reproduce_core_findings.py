"""Read-only offline audit repros. Writes evidence only beside this script.

Run from repository root:
  .venv/bin/python tmp/memory-review-2026-09-22/reproduce_core_findings.py
No real model is constructed; run_battles uses llm=False and fake_analyst=False.
All temporary battle and memory data is automatically removed.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from roco_pvp_agent.battle.evolution.bench import build_instances
from roco_pvp_agent.battle.evolution.globalmem import (
    GlobalMemStore, MatchupQuery, make_global_entry_id, search_global_mem,
)
from roco_pvp_agent.battle.evolution.globalmem_run import run_battles
from roco_pvp_agent.battle.evolution.memory import (
    KeywordEmbedder, MemoryQuery, MemoryStore, make_entry_id,
)
from roco_pvp_agent.battle.evolution.memory_inject import apply_adoption
from roco_pvp_agent.config import Settings


def global_entry(key: str, text: str, *, digest: str = "d") -> dict:
    return {
        "entry_id": make_global_entry_id(key, text, digest),
        "matchup_key": key, "my_roster": [], "foe_roster": [],
        "strategy_text": text, "Q": 0.8, "n_used": 20, "n_wins": 16,
        "provenance": {"data_digest": digest},
    }


def main() -> None:
    evidence = {"mode": "offline; no real LLM/API calls", "findings": {}}
    findings = evidence["findings"]
    with tempfile.TemporaryDirectory(prefix="roco-memory-audit-") as td:
        root = Path(td)
        gkey = "3v2|my:types:火3/spd:mid/k:a6|foe:types:水3"
        entry = global_entry(gkey, "同一策略")
        store = GlobalMemStore(root / "self-supersede")
        store.add(entry)
        result = store.supersede(entry["entry_id"], entry)
        findings["self_supersede_deactivates_memory"] = {
            "operation_result": result,
            "active_count": store.count(),
            "superseded_by": store.get(entry["entry_id"]).get("superseded_by"),
            "expected_active_count": 1,
        }
        assert store.count() == 0

        # Initially empty memory. Every training-time lookup must therefore miss.
        # Current orchestration extracts this game's memories before re-retrieving
        # for credit, so the following records adoption that could not have occurred.
        out = run_battles(
            n=1, seed=7, out_dir=str(root / "out"), settings=Settings(),
            instances=build_instances("d_sel")[:1],
            memory_dir=str(root / "local"), llm=False, fake_analyst=False,
        )
        mstore = MemoryStore(root / "local")
        adoption = out["battles"][0]["memory"]["adoption"]
        findings["empty_store_first_game_has_impossible_adoptions"] = {
            "adoption": adoption,
            "entry_count": mstore.count(),
            "nonzero_q_entries": sum(e["Q"] > 0 for e in mstore.all()),
            "n_used_total": sum(e["n_used"] for e in mstore.all()),
            "n_adopted_total": sum(e["n_adopted"] for e in mstore.all()),
            "expected_adopted": 0,
        }
        assert adoption["adopted"] > 0

        key = "my2/foe2/甲/乙/high/high/0/early/2/2"
        other_key = "my2/foe2/丙/丁/high/high/0/early/2/2"
        embedder = KeywordEmbedder()
        findings["different_active_pets_have_identical_similarity"] = {
            "query": key, "entry_key": other_key,
            "similarity": embedder.similarity(key, other_key),
        }
        findings["missing_digest_passes_local_version_filter"] = {
            "hard_match": embedder.hard_match(
                MemoryQuery(key, side="a", data_digest="expected"),
                {"situation_key": key, "side": "a", "provenance": {}},
            ),
            "expected_hard_match": False,
        }

        cross_store = GlobalMemStore(root / "cross-matchup")
        other_gkey = "3v2|my:types:草3/spd:mid/k:a6|foe:types:水3"
        cross_store.add(global_entry(other_gkey, "来自另一阵容桶的策略"))
        hits = search_global_mem(cross_store, MatchupQuery(gkey, data_digest="d"))
        findings["global_q_candidates_span_matchup_keys"] = {
            "query_matchup": gkey,
            "returned_matchup_keys": [e["matchup_key"] for e in hits],
            "note": "Current hard filter checks scale, not exact matchup bucket.",
        }
        assert hits and hits[0]["matchup_key"] != gkey

        # Reprocessing an identical completed decision currently rewards it twice.
        local = MemoryStore(root / "duplicate-credit")
        action = {"type": "skill", "value": 0}
        eid = make_entry_id("a", key, action, scope="d")
        local.add({
            "entry_id": eid, "side": "a", "lineage_family": "audit",
            "situation_key": key, "situation_text": "t", "experience_text": "e",
            "action": action, "Q": 0.0, "n_used": 0, "n_adopted": 0,
            "provenance": {"data_digest": "d"},
        })
        record = {"battle_id": "same-battle", "analysis_a": [
            {"turn": 1, "situation_key": key, "action": action},
        ]}
        retriever = lambda side, situation: [local.get(eid)]
        apply_adoption(local, record, retriever, winner="a")
        first_q = local.get(eid)["Q"]
        apply_adoption(local, record, retriever, winner="a")
        findings["duplicate_credit_is_not_idempotent"] = {
            "q_after_first": first_q,
            "q_after_same_record_again": local.get(eid)["Q"],
            "expected_second_q": first_q,
            "n_used": local.get(eid)["n_used"],
            "n_adopted": local.get(eid)["n_adopted"],
        }
    path = Path(__file__).with_name("core-findings-evidence.json")
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(f"Evidence saved to {path}")


if __name__ == "__main__":
    main()
