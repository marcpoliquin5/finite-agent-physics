"""Command-line interface for the deterministic Agent Physics slice."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

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
    langgraph = subparsers.add_parser(
        "langgraph-baseline",
        help="run the pinned static LangGraph StormShift conformance comparator",
    )
    langgraph.add_argument(
        "--run-id",
        default="stormshift-langgraph-static-cli-v1",
        help="unique checkpoint thread/run identifier",
    )
    langgraph.add_argument(
        "--checkpoint",
        default=":memory:",
        help="SQLite checkpoint path; defaults to an in-memory database",
    )
    langgraph.add_argument(
        "--output",
        type=Path,
        help="optional JSON output path; stdout is always a compact verification receipt",
    )
    fair = subparsers.add_parser(
        "fair-benchmark",
        help="run the preregistered local FINITE/Python/LangGraph comparison",
    )
    fair.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/fair-benchmark"),
        help="directory for contract, environment, raw records, report, and manifest",
    )
    fair.add_argument(
        "--bootstrap-samples",
        type=int,
        default=2_000,
        help="deterministic paired-bootstrap sample count (minimum 200)",
    )
    survival = subparsers.add_parser(
        "production-survival",
        help="run the preregistered local crash/recovery and overhead gauntlet",
    )
    survival.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/production-survival"),
        help="directory for contract, raw records, report, and file manifest",
    )
    survival.add_argument(
        "--trials",
        type=int,
        default=10,
        help="repeated trials per scenario (minimum 3)",
    )
    survival.add_argument(
        "--seed-base",
        type=int,
        default=5_000,
        help="non-negative first trial seed",
    )
    survival.add_argument(
        "--revision",
        help=(
            "optional caller label (marked unverified); omit to read local Git HEAD and "
            "report dirty state"
        ),
    )
    survival.add_argument(
        "--verify-only",
        type=Path,
        help="independently verify an existing evidence directory without running trials",
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
    if args.command == "langgraph-baseline":
        # Keep LangGraph optional for every other CLI path. The comparator module
        # raises a focused installation error when its pinned extra is absent.
        from .langgraph_baseline import run_langgraph_stormshift_baseline

        record = asyncio.run(
            run_langgraph_stormshift_baseline(
                run_id=args.run_id,
                checkpoint_path=args.checkpoint,
            )
        )
        payload = record.as_dict()
        if args.output is not None:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "verified": record.verify_digest(),
                    "framework": record.framework,
                    "framework_version": record.framework_version,
                    "comparator_kind": record.comparator_kind,
                    "record_digest": record.record_digest,
                    "output": str(args.output.resolve()) if args.output is not None else None,
                },
                sort_keys=True,
            )
        )
        return 0 if record.verify_digest() else 1
    if args.command == "fair-benchmark":
        # Keep comparator dependencies and benchmark setup off every other CLI path.
        from .fair_benchmark import build_fair_benchmark_contract, run_fair_benchmark

        contract = build_fair_benchmark_contract(bootstrap_samples=args.bootstrap_samples)
        output = args.output.resolve()
        evidence = asyncio.run(run_fair_benchmark(contract, output_directory=output))
        evidence.verify()
        executed = [
            status.system_id
            for status in evidence.report.system_statuses
            if status.execution_status == "executed-local"
        ]
        unexecuted = [
            status.system_id
            for status in evidence.report.system_statuses
            if status.execution_status != "executed-local"
        ]
        print(
            json.dumps(
                {
                    "verified": True,
                    "evidence_digest": evidence.evidence_digest,
                    "contract_digest": evidence.contract.contract_digest,
                    "report_digest": evidence.report.report_digest,
                    "executed_systems": executed,
                    "unexecuted_systems": unexecuted,
                    "output_directory": str(output),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "production-survival":
        from .judge_bundle import resolve_source_revision
        from .production_survival import (
            build_survival_contract,
            run_production_survival,
            verify_survival_evidence_directory,
        )

        if args.verify_only is not None:
            evidence, manifest = verify_survival_evidence_directory(
                args.verify_only.resolve()
            )
            print(
                json.dumps(
                    {
                        "verified": True,
                        "all_trials_observed_passed": (
                            evidence.report.all_trials_observed_passed
                        ),
                        "total_trials": evidence.report.total_trials,
                        "total_passes": evidence.report.total_passes,
                        "external_provider_calls": evidence.report.external_provider_calls,
                        "duplicate_effect_applications": (
                            evidence.report.duplicate_effect_applications
                        ),
                        "contract_digest": evidence.contract.contract_digest,
                        "report_digest": evidence.report.report_digest,
                        "manifest_digest": manifest["manifest_digest"],
                        "source_revision": evidence.report.source_revision,
                        "source_state": evidence.report.source_state,
                        "output_directory": str(args.verify_only.resolve()),
                    },
                    sort_keys=True,
                )
            )
            return 0 if evidence.report.all_trials_observed_passed else 1
        root = Path(__file__).resolve().parents[2]
        output = args.output.resolve()
        source = resolve_source_revision(
            args.revision,
            project_root=root,
            excluded_status_paths=(output,),
        )
        revision = str(source["revision"])
        dirty = source["worktree_dirty"]
        source_state = (
            "caller-supplied-unverified"
            if dirty is None
            else ("dirty-after-output-exclusion" if dirty else "clean")
        )
        contract = build_survival_contract(
            trials_per_scenario=args.trials,
            seed_base=args.seed_base,
        )
        with TemporaryDirectory(prefix="finite-survival-") as working:
            evidence = run_production_survival(
                contract,
                working_directory=working,
                source_revision=revision,
                source_state=source_state,
            )
        manifest = evidence.write(output)
        print(
            json.dumps(
                {
                    "verified": evidence.verify(),
                    "all_trials_observed_passed": (
                        evidence.report.all_trials_observed_passed
                    ),
                    "total_trials": evidence.report.total_trials,
                    "total_passes": evidence.report.total_passes,
                    "external_provider_calls": evidence.report.external_provider_calls,
                    "duplicate_effect_applications": (
                        evidence.report.duplicate_effect_applications
                    ),
                    "contract_digest": evidence.contract.contract_digest,
                    "report_digest": evidence.report.report_digest,
                    "manifest_digest": manifest["manifest_digest"],
                    "source_revision": revision,
                    "source_state": source_state,
                    "output_directory": str(output),
                },
                sort_keys=True,
            )
        )
        return 0 if evidence.report.all_trials_observed_passed else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
