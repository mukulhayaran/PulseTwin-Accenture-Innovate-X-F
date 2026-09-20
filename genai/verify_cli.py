"""Re-run the verification gate over a stored run.

    python -m genai.verify_cli --run-id <id> [--json]

The point is not convenience. A stored run carries the finding the model emitted AND the
exact tool results it was shown, so the gate can be re-executed later, by someone else,
without a provider credential -- and must reach a byte-identical verdict.

That is what makes the anti-hallucination claim auditable rather than asserted: the model
is not reproducible, but the thing that judges it is, and anyone can re-run the judgment.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from genai import verifier as V
from genai.finding import Citation, Claim, Finding


def _rehydrate(blob: dict[str, Any], question: str) -> Finding:
    claims = [
        Claim(
            id=str(c["id"]), text=str(c["text"]), value=float(c["value"]),
            unit=str(c["unit"]), provenance=str(c["provenance"]),
            citation=Citation(call_id=str(c["citation"]["call_id"]),
                              pointer=str(c["citation"]["pointer"])),
            ci_low=c.get("ci_low"), ci_high=c.get("ci_high"), source=c.get("source"),
        )
        for c in blob.get("claims", [])
    ]
    return Finding(
        question=question,
        summary=str(blob.get("summary", "")),
        claims=claims,
        recommendation=str(blob.get("recommendation", "")),
        alternatives_considered=list(blob.get("alternatives_considered", [])),
        confidence=str(blob.get("confidence", "low")),
        unanswered=list(blob.get("unanswered", [])),
    )


def _loads(v: Any) -> Any:
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Re-verify a stored triage run.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--json", action="store_true", help="emit the verification as JSON")
    args = p.parse_args(argv)

    from serve import db

    con, _ = db.open_ops_only()
    row = db.query_one(
        con,
        "SELECT question, plant_id, finding_raw, verification FROM triage_runs "
        "WHERE run_id = ?",
        (args.run_id,),
    )
    if row is None:
        print(f"no such run: {args.run_id}", file=sys.stderr)
        return 2

    raw = _loads(row["finding_raw"])
    if raw is None:
        print(f"run {args.run_id} stored no finding (rejected before parse)",
              file=sys.stderr)
        return 3

    calls = db.query_all(
        con,
        "SELECT call_id, result FROM triage_tool_calls WHERE run_id = ? ORDER BY seq",
        (args.run_id,),
    )
    transcript = {c["call_id"]: _loads(c["result"]) for c in calls}

    stations = {
        s["station_id"]
        for s in db.query_all(
            con, "SELECT DISTINCT station_id FROM sensor_health WHERE plant_id = ?",
            (row["plant_id"],),
        )
    }

    ver, served = V.verify(_rehydrate(raw, str(row["question"])), transcript,
                           station_ids=stations)

    if args.json:
        print(json.dumps(ver.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"run        {args.run_id}")
        print(f"status     {ver.status}"
              + (f"  ({ver.reject_reason})" if ver.reject_reason else ""))
        print(f"claims     {ver.claims_verified}/{ver.claims_total} verified")
        print(f"tool calls {len(transcript)}")
        for v in ver.claim_verdicts:
            if not v.ok:
                print(f"  FAIL {v.claim_id:<8} {v.code:<22} {v.detail}")
        for issue in ver.prose_issues:
            print(f"  PROSE {issue}")

    # Re-verification must agree with what was stored, or the gate is not deterministic.
    stored = _loads(row["verification"]) or {}
    if stored.get("status") and stored["status"] != ver.status:
        print(f"\nWARNING: stored status {stored['status']!r} != recomputed "
              f"{ver.status!r} -- the verifier is not deterministic, which breaks the "
              f"entire audit claim.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
