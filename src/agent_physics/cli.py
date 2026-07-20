"""Command-line interface for the deterministic Agent Physics slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .examples import miami_eoc_envelope, miami_eoc_graph
from .feasibility import FeasibilityAnalyzer
from .ledger import verify_conservation
from .judge_bundle import build_judge_evidence
from .scheduler import SchedulePolicy, Scheduler


def _print_summary(result: object) -> None:
    data = result.as_dict()  # type: ignore[attr-defined]
    print("Agent Physics - Miami EOC pressure test")
    print(f"policy:          {data['policy']}")
    print(f"success:         {data['success']}")
    print(f"makespan:        {data['makespan_ms']} ms")
    print(f"model bound:     {data['model_bound_ms']} ms")
    print(f"model-bound gap: {data['model_bound_gap']}x")
    print(f"tokens:          {data['total_tokens']}")
    print(f"cost:            {data['total_cost_microusd']} micro-USD")
    print(f"context moved:   {data['total_context_bytes']} bytes")
    print(f"skipped:         {', '.join(data['skipped']) or 'none'}")
    print("\nSchedule")
    print("task                         backend                start    end")
    print("-" * 72)
    for entry in data["entries"]:
        print(
            f"{entry['task_id']:<28} {entry['backend']:<20} "
            f"{entry['start_ms']:>6} {entry['end_ms']:>6}"
        )
    if data["failure_reason"]:
        print(f"\nFailure: {data['failure_reason']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-physics")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="run the deterministic Miami EOC scenario")
    demo.add_argument(
        "--policy",
        choices=[policy.value for policy in SchedulePolicy],
        default=SchedulePolicy.ADAPTIVE.value,
    )
    demo.add_argument("--json", action="store_true", help="print machine-readable output")
    preflight = subparsers.add_parser(
        "preflight", help="emit a feasibility certificate for the Miami EOC scenario"
    )
    preflight.add_argument(
        "--policy",
        choices=[policy.value for policy in SchedulePolicy],
        default=SchedulePolicy.ADAPTIVE.value,
    )
    judge = subparsers.add_parser(
        "judge-bundle",
        help="write the complete digest-bound offline evidence bundle",
    )
    judge.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/judge-evidence.json"),
    )
    judge.add_argument(
        "--raw-experiments",
        type=Path,
        default=Path("artifacts/judge-experiments.jsonl"),
    )
    judge.add_argument(
        "--revision",
        help=(
            "optional caller label (marked unverified); omit to read local Git HEAD and "
            "report dirty state"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        result = Scheduler().schedule(
            miami_eoc_graph(),
            miami_eoc_envelope(),
            SchedulePolicy(args.policy),
        )
        if args.json:
            print(json.dumps(result.as_dict(), indent=2))
        else:
            _print_summary(result)
        return 0 if result.success else 1
    if args.command == "preflight":
        graph = miami_eoc_graph()
        envelope = miami_eoc_envelope()
        certificate, result = FeasibilityAnalyzer().analyze(
            graph,
            envelope,
            SchedulePolicy(args.policy),
        )
        conservation = verify_conservation(graph, envelope, result)
        payload = certificate.as_dict()
        payload["conservation"] = {
            "passed": conservation.passed,
            "trace_digest": conservation.trace_digest,
            "checks": [
                {"name": check.name, "passed": check.passed, "evidence": check.evidence}
                for check in conservation.checks
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0 if certificate.status.value != "refused" and conservation.passed else 1
    if args.command == "judge-bundle":
        root = Path(__file__).resolve().parents[2]
        output = args.output.resolve()
        raw_experiments = args.raw_experiments.resolve()
        bundle = build_judge_evidence(
            revision=args.revision,
            project_root=root,
            provenance_excluded_paths=(output, raw_experiments),
        )
        bundle.write(output, raw_experiments_path=raw_experiments)
        print(
            json.dumps(
                {
                    "verified": bundle.verify(),
                    "content_digest": bundle.content_digest,
                    "judge_bundle": str(output),
                    "raw_experiments": str(raw_experiments),
                },
                sort_keys=True,
            )
        )
        return 0 if bundle.verify() else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
