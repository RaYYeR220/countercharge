"""Audit rule modules.

Each ``rN_*`` module exposes a module-level ``RULE_ID: str`` and a
``check(ctx: AuditContext) -> list[Finding]`` function. See
``rules.common`` for the shared ``AuditContext`` / ``RuleFn`` types.
"""
