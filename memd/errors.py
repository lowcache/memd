"""Exception hierarchy for memd failures.

MemdError subclasses RuntimeError so existing `except RuntimeError` callers
(e.g. sync_project's failure guard) keep catching everything memd raises.

CLI exit-code contract: 0 = ok, 1 = known error, 2 = config error,
3 = curator/distill failure.
"""


class MemdError(RuntimeError):
    """Base for all memd-raised failures."""


class ConfigError(MemdError):
    """Bad or missing configuration (including a missing curator backend);
    retrying cannot fix it. CLI exit code 2 where surfaced directly."""


class DigestError(MemdError):
    """A transcript source could not be digested. Digesters deliberately log
    and return an unchanged cursor instead of raising (so backlog replays);
    this class is part of the public hierarchy for downstream tooling."""


class CuratorError(MemdError):
    """Curator backend invocation or output failure (transient: timeouts,
    nonzero exits, unparseable output). call_curator retries these."""
