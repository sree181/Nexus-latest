from __future__ import annotations

import pytest

from app import auth
from app.auth import AuthError


def cfg():
    return auth.Config(
        analyst_groups=("analyst",), ciso_groups=("ciso",),
        platform_admin_groups=("platform-admin",),
    )


def test_platform_admin_is_a_fourth_non_composable_role():
    who = auth.principal_of(
        {"sub": "admin-1", "name": "Avery", "roles": ["platform-admin"]}, cfg()
    )
    assert who.role == "platform_admin"
    assert who.has("recorder.admin.read")
    assert who.has("recorder.trust.write")
    assert not who.has("fleet.read")
    assert not who.has("run.own")
    assert not who.has("policy.write")


@pytest.mark.parametrize("roles", [
    ["platform-admin", "analyst"],
    ["platform-admin", "ciso"],
    ["analyst", "ciso"],
    ["platform-admin", "analyst", "ciso"],
])
def test_any_privileged_role_overlap_fails_closed(roles):
    with pytest.raises(AuthError, match="conflicting"):
        auth.role_of({"roles": roles}, cfg())
