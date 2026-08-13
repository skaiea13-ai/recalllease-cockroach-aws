from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import scripts.verify_zero_cost_cloud as zero_cost

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _aws_plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "accountId": "123456789012",
        "accountPlanType": "FREE",
        "accountPlanStatus": "ACTIVE",
        "accountPlanRemainingCredits": {"amount": 100.0, "unit": "USD"},
        "accountPlanExpirationDate": (NOW + timedelta(days=30)).isoformat(),
    }
    plan.update(overrides)
    return plan


def _identity(account: str = "123456789012") -> dict[str, str]:
    return {"Account": account}


def _cluster() -> dict[str, Any]:
    return {
        "name": "recalllease",
        "plan": "BASIC",
        "state": "CREATED",
        "cloud_provider": "AWS",
        "regions": [{"name": "us-east-1", "primary": True}],
        "config": {
            "serverless": {
                "usage_limits": {
                    "request_unit_limit": "1000000",
                    "storage_mib_limit": "1024",
                }
            }
        },
    }


def _invoices(**overrides: Any) -> list[dict[str, Any]]:
    invoice: dict[str, Any] = {
        "status": "DRAFT",
        "period_start": (NOW - timedelta(days=12)).isoformat(),
        "period_end": (NOW + timedelta(days=19)).isoformat(),
        "adjustments": [
            {
                "amount": {"amount": -0.01, "currency": "USD"},
                "name": "Free trial credits",
            }
        ],
        "balances": [],
        "totals": [{"amount": 0, "currency": "USD"}],
    }
    invoice.update(overrides)
    return [invoice]


def test_active_aws_free_plan_is_accepted() -> None:
    zero_cost.validate_aws_free_plan(_aws_plan(), _identity(), now=NOW)


@pytest.mark.parametrize(
    ("plan", "identity"),
    [
        (_aws_plan(accountPlanType="PAID"), _identity()),
        (_aws_plan(accountPlanStatus="EXPIRED"), _identity()),
        (_aws_plan(), _identity("999999999999")),
        (_aws_plan(accountPlanRemainingCredits={"amount": 0, "unit": "USD"}), _identity()),
        (
            _aws_plan(accountPlanRemainingCredits={"amount": float("nan"), "unit": "USD"}),
            _identity(),
        ),
        (
            _aws_plan(accountPlanExpirationDate=(NOW + timedelta(days=7)).isoformat()),
            _identity(),
        ),
    ],
)
def test_unsafe_aws_plan_states_are_rejected(
    plan: dict[str, Any],
    identity: dict[str, str],
) -> None:
    with pytest.raises(RuntimeError):
        zero_cost.validate_aws_free_plan(plan, identity, now=NOW)


def test_live_ccloud_basic_shape_is_accepted() -> None:
    zero_cost.validate_cockroach_basic_cluster(_cluster())


def test_zero_cost_cockroach_invoice_is_accepted() -> None:
    zero_cost.validate_cockroach_zero_cost_invoice(_invoices(), now=NOW)


@pytest.mark.parametrize(
    "invoices",
    [
        [],
        _invoices(status="FINALIZED"),
        _invoices(period_end=(NOW + timedelta(days=7)).isoformat()),
        _invoices(totals=[{"amount": 0.01, "currency": "USD"}]),
        _invoices(totals=[{"amount": float("nan"), "currency": "USD"}]),
        _invoices(balances=[{"amount": 0.01, "currency": "USD"}]),
        _invoices(adjustments=[]),
        _invoices(
            adjustments=[
                {
                    "amount": {"amount": 0.01, "currency": "USD"},
                    "name": "Free trial credits",
                }
            ]
        ),
    ],
)
def test_unsafe_cockroach_invoice_states_are_rejected(
    invoices: list[dict[str, Any]],
) -> None:
    with pytest.raises(RuntimeError):
        zero_cost.validate_cockroach_zero_cost_invoice(invoices, now=NOW)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("plan",), "STANDARD"),
        (("state",), "CREATING"),
        (("cloud_provider",), "GCP"),
        (("name",), "another-cluster"),
        (("config", "serverless", "usage_limits", "request_unit_limit"), "1000001"),
        (("config", "serverless", "usage_limits", "storage_mib_limit"), "1025"),
        (("config", "serverless", "usage_limits", "storage_mib_limit"), "nan"),
    ],
)
def test_unsafe_cockroach_states_are_rejected(
    path: tuple[str, ...],
    value: str,
) -> None:
    cluster = deepcopy(_cluster())
    target = cluster
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(RuntimeError):
        zero_cost.validate_cockroach_basic_cluster(cluster)


def test_cloud_verifiers_use_only_bounded_cli_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run_json(command: list[str]) -> dict[str, Any] | list[dict[str, Any]]:
        commands.append(command)
        if command[:2] == ["aws", "freetier"]:
            return _aws_plan()
        if command[:2] == ["aws", "sts"]:
            return _identity()
        if command[1:3] == ["-q", "billing"]:
            return _invoices()
        return _cluster()

    monkeypatch.setattr(zero_cost, "_run_json", fake_run_json)
    monkeypatch.setattr(zero_cost, "MINIMUM_AWS_PLAN_LIFETIME", timedelta(days=-1))
    monkeypatch.setattr(zero_cost, "MINIMUM_COCKROACH_BILLING_WINDOW", timedelta(days=-1))

    zero_cost.verify_aws_free_plan()
    zero_cost.verify_cockroach_basic_cluster()

    assert commands == [
        [
            "aws",
            "freetier",
            "get-account-plan-state",
            "--region",
            "us-east-1",
            "--output",
            "json",
            "--no-cli-pager",
        ],
        [
            "aws",
            "sts",
            "get-caller-identity",
            "--region",
            "us-east-1",
            "--output",
            "json",
            "--no-cli-pager",
        ],
        [
            "ccloud",
            "-q",
            "cluster",
            "info",
            "recalllease",
            "--output",
            "json",
        ],
        [
            "ccloud",
            "-q",
            "billing",
            "invoice",
            "list",
            "--output",
            "json",
        ],
    ]
