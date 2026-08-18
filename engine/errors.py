"""The project's custom exceptions, all in one module (rules.md §3.2).

    ModelCallError, PatchError, GradingInfraError, BudgetExceeded

Every caught exception either becomes an event + status, or is re-raised.
Silent swallowing is forbidden. Messages carry context (task_id, attempt,
agent) -- never a bare "apply failed".
"""
