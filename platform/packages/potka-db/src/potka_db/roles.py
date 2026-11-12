"""The names of the platform's database roles, as pokus-infrastructure renders them.

A service's login role is ``<platform>_<service>``; the group role of a
profile is ``<platform>_<profile>`` with the profile's hyphens as
underscores (``potka_restricted_operator``); the untrusted agent's profile
has no role at all. The migrations grant to the group roles by these names.
"""

from __future__ import annotations

AGENT_PROFILE = "untrusted-agent"


def role(platform: str, member: str) -> str:
    """The role of MEMBER (a service, ``migrator``, ``reader``, ``grafana``).

    Args:
        platform: The platform's name.
        member: The member's name.

    Returns:
        ``<platform>_<member>``, hyphens as underscores.
    """
    return f"{platform}_{member.replace('-', '_')}"


def group_role(platform: str, profile: str) -> str:
    """The group role of PROFILE.

    Args:
        platform: The platform's name.
        profile: A privilege profile with a database role.

    Returns:
        ``<platform>_<profile>``, hyphens as underscores.

    Raises:
        ValueError: The untrusted agent's profile, which has no role.
    """
    if profile == AGENT_PROFILE:
        raise ValueError("the untrusted agent's profile has no database role")
    return role(platform, profile)
