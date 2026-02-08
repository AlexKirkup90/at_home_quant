from __future__ import annotations

import argparse

from at_home_quant.ops.release import (
    activate_model_release,
    approve_model_release,
    get_active_model_release,
    propose_model_release,
    rollback_model_release,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Model release workflow operations.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    propose_cmd = sub.add_parser("propose")
    propose_cmd.add_argument("--model", required=True)
    propose_cmd.add_argument("--env", required=True, choices=["dev", "stage", "prod"])
    propose_cmd.add_argument("--experiment-id", required=True, type=int)
    propose_cmd.add_argument("--notes", default=None)

    approve_cmd = sub.add_parser("approve")
    approve_cmd.add_argument("--release-id", required=True, type=int)

    activate_cmd = sub.add_parser("activate")
    activate_cmd.add_argument("--release-id", required=True, type=int)

    rollback_cmd = sub.add_parser("rollback")
    rollback_cmd.add_argument("--model", required=True)
    rollback_cmd.add_argument("--env", required=True, choices=["dev", "stage", "prod"])
    rollback_cmd.add_argument("--target-release-id", required=True, type=int)

    active_cmd = sub.add_parser("active")
    active_cmd.add_argument("--model", required=True)
    active_cmd.add_argument("--env", required=True, choices=["dev", "stage", "prod"])

    args = parser.parse_args()

    if args.cmd == "propose":
        row = propose_model_release(
            model_name=args.model,
            environment=args.env,
            experiment_id=args.experiment_id,
            notes=args.notes,
        )
        print(f"proposed release_id={row.id} model={row.model_name} env={row.environment} status={row.status}")
        return
    if args.cmd == "approve":
        row = approve_model_release(args.release_id)
        print(f"approved release_id={row.id} status={row.status}")
        return
    if args.cmd == "activate":
        row = activate_model_release(args.release_id)
        print(f"active release_id={row.id} model={row.model_name} env={row.environment} status={row.status}")
        return
    if args.cmd == "rollback":
        row = rollback_model_release(
            model_name=args.model,
            environment=args.env,
            target_release_id=args.target_release_id,
        )
        print(f"rollback active release_id={row.id} model={row.model_name} env={row.environment}")
        return
    if args.cmd == "active":
        row = get_active_model_release(args.model, args.env)
        if row is None:
            print("no active release")
            return
        print(
            f"active release_id={row.id} model={row.model_name} env={row.environment} "
            f"experiment_id={row.experiment_id}"
        )


if __name__ == "__main__":
    main()
