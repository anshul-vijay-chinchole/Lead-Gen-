"""Shared fixtures.

``make_ctx(**playbook_overrides)`` builds a Context with a validated playbook,
a FakeHttp, an in-memory Store, an explicit env dict and a fixed ``today``.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

import pytest

from leadgen.context import Context
from leadgen.playbook import from_dict
from leadgen.store import Store
from tests.fakes import FakeHttp

TODAY = date(2026, 9, 24)


def build_ctx(env: Optional[Dict[str, str]] = None, dry_run: bool = False, **overrides: Any) -> Context:
    # The module tests exercise the outbound features (writer, replies, hand-over),
    # so the fixture defaults to mode: outbound. Delivery tests pass mode="delivery";
    # tests/test_modes.py pins the engine's real default (delivery).
    data: Dict[str, Any] = {"name": "test", "mode": "outbound"}
    data.update(overrides)
    pb = from_dict(data, env=env or {})
    return Context(playbook=pb, http=FakeHttp(), store=Store(":memory:"), env=dict(env or {}),
                   today=TODAY, log=logging.getLogger("leadgen.test"), dry_run=dry_run)


@pytest.fixture
def make_ctx():
    created = []

    def _make(env: Optional[Dict[str, str]] = None, dry_run: bool = False, **overrides: Any) -> Context:
        ctx = build_ctx(env=env, dry_run=dry_run, **overrides)
        created.append(ctx)
        return ctx

    yield _make
    for c in created:
        c.store.close()


@pytest.fixture
def today() -> date:
    return TODAY


# Playbooks shipped with the repo. Tests iterate this list (not a glob) so a
# playbook a user adds to playbooks/ never breaks the test suite.
from pathlib import Path as _Path  # noqa: E402

REPO_ROOT = _Path(__file__).resolve().parent.parent
SHIPPED_PLAYBOOKS = [REPO_ROOT / "playbooks" / n for n in ("demo-offline.yaml", "my-agency.yaml")] + [
    REPO_ROOT / "playbooks" / "templates" / f"{n}.yaml"
    for n in ("agency-outreach", "generic", "local-business", "recruitment", "saas-funding")]
