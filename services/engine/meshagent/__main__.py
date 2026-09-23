"""
Project:     MeshAgent
File:        meshagent/__main__.py
Description: Runnable demos for a live audience. `python -m meshagent
             poison` runs the 30-second security demo; `python -m
             meshagent unlearn` runs the 60-second provable-unlearning
             demo. Each prints the beats a CISO or a technical buyer
             needs to see: behaviour shifting, the trace to the source,
             the certificate, and behaviour restored.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import sys
import tempfile

import hypermeshdb

from .demos import PoisonDemo, UnlearnDemo


def _memory():
    from hypermeshdb.agentmem import MemoryStore

    d = tempfile.mkdtemp(prefix="meshagent-demo-")
    db = hypermeshdb.connect(d)
    return MemoryStore(db, d), db


def _rule(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def run_poison() -> None:
    mem, db = _memory()
    _rule("MeshAgent — live memory poisoning and recovery")
    t = PoisonDemo(mem).run()
    print(f"1. baseline behaviour : {t.baseline_answer}")
    print(f"2. after poisoning    : {t.poisoned_answer}")
    print("   (a fetched page planted a config claim; the agent believed it)")
    print(f"3. why did it change? : traced to {t.root_cause_source}")
    print("   evidence chain:")
    for n in t.why_chain.flatten():
        print(f"     - [{n['status']}] {n['source']}: {n['statement']}")
    print(f"4. revert             : purged {t.certificate.count} edges "
          f"(source + the agent's conclusion), hashes retained")
    print(f"5. restored behaviour : {t.restored_answer}")
    print(f"\n   restored == baseline: {t.restored_answer == t.baseline_answer}")
    mem.close()
    db.close()


def run_unlearn() -> None:
    mem, db = _memory()
    _rule("MeshAgent — provable unlearning and relearning")
    t = UnlearnDemo(mem).run()
    print(f"1. behaviour from a wrong fact : {t.behaviour_before}")
    print(f"2. forget the wrong fact       : certificate purged "
          f"{t.certificate.count} edges (fact + dependent skill)")
    print(f"3. behaviour after forget      : {t.behaviour_after_forget}")
    print(f"4. relearn from a correction   : {t.behaviour_after_relearn}")
    mem.close()
    db.close()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    which = args[0] if args else "poison"
    if which == "poison":
        run_poison()
    elif which == "unlearn":
        run_unlearn()
    elif which == "both":
        run_poison()
        run_unlearn()
    else:
        print("usage: python -m meshagent [poison|unlearn|both]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
