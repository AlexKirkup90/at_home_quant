from __future__ import annotations

from at_home_quant.config.settings import get_settings

ROLE_ORDER = {"viewer": 0, "analyst": 1, "approver": 2, "admin": 3}
ACTION_MIN_ROLE = {
    "release.propose": "analyst",
    "release.approve": "approver",
    "release.activate": "admin",
    "release.rollback": "admin",
}


def has_permission(role: str, action: str) -> bool:
    required_role = ACTION_MIN_ROLE.get(action, "admin")
    return ROLE_ORDER.get(role, -1) >= ROLE_ORDER.get(required_role, 99)


def enforce_permission(action: str, role: str | None = None) -> None:
    settings = get_settings()
    if not settings.enforce_rbac:
        return
    resolved_role = role or settings.operator_role
    if not has_permission(resolved_role, action):
        raise PermissionError(
            f"RBAC denied action '{action}' for role '{resolved_role}'."
        )


__all__ = ["has_permission", "enforce_permission"]
