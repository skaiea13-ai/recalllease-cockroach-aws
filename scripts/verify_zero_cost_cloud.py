from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from scripts.verify_runtime_requirements import ROOT, resolve_external_executable

AWS_BILLING_REGION = "us-east-1"
CLUSTER_NAME = "recalllease"
MAX_REQUEST_UNITS = 1_000_000
MAX_STORAGE_MIB = 1_024
MINIMUM_AWS_PLAN_LIFETIME = timedelta(days=7)
MINIMUM_COCKROACH_BILLING_WINDOW = timedelta(days=7)


def _run_json(command: Sequence[str]) -> Mapping[str, Any] | list[Any]:
    executable = resolve_external_executable(command[0])
    try:
        result = subprocess.run(  # noqa: S603 - resolved executable and fixed arguments
            [executable, *command[1:]],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise RuntimeError("zero-cost cloud state could not be verified") from None
    if not isinstance(payload, (dict, list)):
        raise RuntimeError("zero-cost cloud state could not be verified")
    return payload


def _require_mapping(payload: Mapping[str, Any] | list[Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise RuntimeError("zero-cost cloud state could not be verified")
    return payload


def _require_mapping_list(payload: Mapping[str, Any] | list[Any]) -> list[Mapping[str, Any]]:
    if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
        raise RuntimeError("zero-cost cloud state could not be verified")
    return payload


def _nested(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = payload
        for part in path:
            if not isinstance(current, Mapping) or part not in current:
                break
            current = current[part]
        else:
            return current
    return None


def _as_positive_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError
    return parsed


def validate_aws_free_plan(
    plan: Mapping[str, Any],
    identity: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    if plan.get("accountPlanType") != "FREE" or plan.get("accountPlanStatus") != "ACTIVE":
        raise RuntimeError("AWS account must have an active Free account plan")
    plan_account = plan.get("accountId")
    identity_account = identity.get("Account")
    if (
        not isinstance(plan_account, str)
        or not isinstance(identity_account, str)
        or plan_account != identity_account
    ):
        raise RuntimeError("AWS Free account plan is not bound to the active identity")

    credits = plan.get("accountPlanRemainingCredits")
    try:
        if not isinstance(credits, Mapping) or credits.get("unit") != "USD":
            raise ValueError
        _as_positive_float(credits.get("amount"))
    except (TypeError, ValueError):
        raise RuntimeError("AWS Free account plan has no verified remaining credits") from None

    expiration_value = plan.get("accountPlanExpirationDate")
    try:
        expiration = datetime.fromisoformat(str(expiration_value).replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError("AWS Free account plan expiration is not verifiable") from None
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if expiration <= current + MINIMUM_AWS_PLAN_LIFETIME:
        raise RuntimeError("AWS Free account plan expires too soon for a safe demo window")


def _normalize_enum(value: Any) -> str:
    return str(value or "").strip().upper()


def _usage_limit(payload: Mapping[str, Any], snake: str, camel: str) -> Any:
    return _nested(
        payload,
        ("config", "serverless", "usage_limits", snake),
        ("config", "serverless", "usageLimits", camel),
        ("serverless", "usage_limits", snake),
        ("serverless", "usageLimits", camel),
        ("usage_limits", snake),
        ("usageLimits", camel),
        (snake,),
        (camel,),
    )


def validate_cockroach_basic_cluster(
    cluster: Mapping[str, Any],
    *,
    cluster_name: str = CLUSTER_NAME,
) -> None:
    name = _nested(cluster, ("name",), ("cluster", "name"))
    plan = _normalize_enum(
        _nested(cluster, ("plan",), ("plan_type",), ("planType",), ("cluster", "plan"))
    )
    state = _normalize_enum(_nested(cluster, ("state",), ("cluster", "state")))
    cloud = _normalize_enum(
        _nested(
            cluster,
            ("cloud",),
            ("provider",),
            ("cloud_provider",),
            ("cloudProvider",),
            ("cluster", "cloud"),
        )
    )
    if name != cluster_name:
        raise RuntimeError("CockroachDB cluster identity does not match the bounded demo")
    if plan not in {"BASIC", "PLAN_BASIC", "PLAN_SERVERLESS"}:
        raise RuntimeError("CockroachDB cluster must use the Basic plan")
    if state not in {"CREATED", "READY", "CLUSTER_STATE_CREATED"}:
        raise RuntimeError("CockroachDB Basic cluster is not ready")
    if cloud not in {"AWS", "CLOUD_PROVIDER_AWS"}:
        raise RuntimeError("CockroachDB cluster must run on AWS")

    request_units = _usage_limit(cluster, "request_unit_limit", "requestUnitLimit")
    storage_mib = _usage_limit(cluster, "storage_mib_limit", "storageMibLimit")
    storage_gib = _usage_limit(cluster, "storage_gib_limit", "storageGibLimit")
    try:
        request_unit_limit = _as_positive_float(request_units)
        if storage_mib is not None:
            storage_mib_limit = _as_positive_float(storage_mib)
        elif storage_gib is not None:
            storage_mib_limit = _as_positive_float(storage_gib) * 1_024
        else:
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeError("CockroachDB resource limits are not verifiable") from None
    if request_unit_limit > MAX_REQUEST_UNITS or storage_mib_limit > MAX_STORAGE_MIB:
        raise RuntimeError("CockroachDB Basic limits exceed the zero-cost demo budget")


def validate_cockroach_zero_cost_invoice(
    invoices: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(UTC)
    drafts = [invoice for invoice in invoices if invoice.get("status") == "DRAFT"]
    if len(drafts) != 1:
        raise RuntimeError("CockroachDB current billing period is not verifiable")
    draft = drafts[0]

    try:
        period_start = datetime.fromisoformat(str(draft.get("period_start")).replace("Z", "+00:00"))
        period_end = datetime.fromisoformat(str(draft.get("period_end")).replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError("CockroachDB billing window is not verifiable") from None
    if period_start.tzinfo is None:
        period_start = period_start.replace(tzinfo=UTC)
    if period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=UTC)
    if not period_start <= current or period_end <= current + MINIMUM_COCKROACH_BILLING_WINDOW:
        raise RuntimeError("CockroachDB zero-cost billing window is too short")

    totals = draft.get("totals")
    if not isinstance(totals, list) or len(totals) != 1:
        raise RuntimeError("CockroachDB invoice total is not verifiable")
    total = totals[0]
    try:
        if not isinstance(total, Mapping) or total.get("currency") != "USD":
            raise ValueError
        amount = float(total.get("amount"))
        if not math.isfinite(amount) or amount != 0:
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeError("CockroachDB current invoice must total exactly USD 0") from None

    if draft.get("balances") != []:
        raise RuntimeError("CockroachDB current invoice has an unverified balance")

    adjustments = draft.get("adjustments")
    if not isinstance(adjustments, list):
        raise RuntimeError("CockroachDB trial credit is not verifiable")
    trial_credit_verified = False
    for adjustment in adjustments:
        if not isinstance(adjustment, Mapping) or adjustment.get("name") != "Free trial credits":
            continue
        credit_amount = adjustment.get("amount")
        try:
            if not isinstance(credit_amount, Mapping) or credit_amount.get("currency") != "USD":
                raise ValueError
            parsed = float(credit_amount.get("amount"))
            trial_credit_verified = math.isfinite(parsed) and parsed < 0
        except (TypeError, ValueError):
            trial_credit_verified = False
        if trial_credit_verified:
            break
    if not trial_credit_verified:
        raise RuntimeError("CockroachDB free trial credit is not applied to the current invoice")


def verify_aws_free_plan() -> None:
    plan = _require_mapping(
        _run_json(
            [
                "aws",
                "freetier",
                "get-account-plan-state",
                "--region",
                AWS_BILLING_REGION,
                "--output",
                "json",
                "--no-cli-pager",
            ]
        )
    )
    identity = _require_mapping(
        _run_json(
            [
                "aws",
                "sts",
                "get-caller-identity",
                "--region",
                AWS_BILLING_REGION,
                "--output",
                "json",
                "--no-cli-pager",
            ]
        )
    )
    validate_aws_free_plan(plan, identity)


def verify_cockroach_basic_cluster(*, cluster_name: str = CLUSTER_NAME) -> None:
    cluster = _require_mapping(
        _run_json(["ccloud", "-q", "cluster", "info", cluster_name, "--output", "json"])
    )
    invoices = _require_mapping_list(
        _run_json(["ccloud", "-q", "billing", "invoice", "list", "--output", "json"])
    )
    validate_cockroach_basic_cluster(cluster, cluster_name=cluster_name)
    validate_cockroach_zero_cost_invoice(invoices)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed cloud cost-boundary verifier")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--aws-only", action="store_true")
    selection.add_argument("--cockroach-only", action="store_true")
    parser.add_argument("--cluster", default=CLUSTER_NAME)
    arguments = parser.parse_args()

    try:
        if not arguments.cockroach_only:
            verify_aws_free_plan()
        if not arguments.aws_only:
            verify_cockroach_basic_cluster(cluster_name=arguments.cluster)
    except RuntimeError as error:
        print(f"Zero-cost cloud boundary rejected: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    print("Zero-cost cloud boundary verified without exposing account identifiers.")


if __name__ == "__main__":
    main()
