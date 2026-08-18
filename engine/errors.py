"""The project's custom exceptions, all in one module (rules.md §3.2).

Every caught exception either becomes an event + status, or is re-raised.
Silent swallowing is forbidden. Messages must carry context (task_id, attempt,
agent) -- never a bare "apply failed".
"""


class GarageError(Exception):
    """Base for every error this engine raises deliberately."""


class ModelCallError(GarageError):
    """A model call failed after its retry budget was exhausted.

    `retryable` says whether a fresh ATTEMPT could plausibly succeed. A model
    that emitted a spontaneous tool call is a bad generation -- the next one may
    be fine, so the task keeps its remaining attempts. A missing key or a
    rejected credential will fail identically every time, so the task stops.
    """

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class PatchError(GarageError):
    """A model diff could not be extracted, validated, or applied."""


class GradingInfraError(GarageError):
    """The Docker harness itself failed -- image pull, container, timeout.

    Never conflate this with a failing test. The task's status becomes
    `crashed`, not `failed_tests`: infrastructure trouble is not the model's
    fault (rules.md §3.1).
    """


class WorkspaceError(GarageError):
    """A clone, fetch, or worktree operation failed.

    Infrastructure, like GradingInfraError: the model never sees a checkout,
    so it cannot be at fault for one that failed to materialize.
    """


class BudgetExceeded(GarageError):
    """A per-task or per-run cap was hit; the task ends gracefully."""
