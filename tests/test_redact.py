"""REDACT_PATTERNS coverage: a positive and a near-miss negative per named
pattern, REDACT_EXTRA_PATTERNS config wiring, and pattern-name match logging.

All samples are fabricated, real-looking tokens (AWS's own documented example
key for aws_access) — never real credentials. Broad redact() smoke tests also
live in test_core.py; this file is the per-pattern contract.
"""

import pytest

# name -> text that MUST be redacted
POSITIVE = {
    "google_oauth": "got token ya29." + "Ab1_" * 8,
    "github_pat_classic": "push with ghp_" + "A1b2C3d4" * 4,
    "github_pat_finegrained": "github_pat_" + "11ABCDE" * 4,
    "anthropic_key": "ANTHROPIC key sk-ant-api03-" + "x" * 24,
    "openai_key": "sk-proj-" + "Yz" * 15,
    "aws_access": "creds AKIAIOSFODNN7EXAMPLE in env",
    "slack_token": "xoxb-1234567890-abcdefghijkl",
    "gitlab_token": "glpat-" + "zZ" * 12,
    "npm_token": "npm_" + "a1B2" * 9,
    "jwt": "eyJ" + "h" * 24 + "." + "p" * 16 + "." + "s" * 16,
    "json_token_field": '{"access_token": "1//secret-value"}',
    "bearer_header": "Authorization: Bearer " + "t0ken" * 8,
    "env_credential": "OPENAI_API_KEY=abcd1234efgh5678ijkl",
}

# near-misses that MUST come through unchanged
NEGATIVE = {
    "google_oauth_short": "ya29.tooshort",
    "github_prefix_word": "ghp_short and ghx_" + "A1" * 15,
    "openai_short": "sk-short-key",
    "aws_lowercase": "akiaiosfodnn7example",
    "aws_short": "AKIA1234",
    "slack_bad_letter": "xoxq-1234567890-abcdefghijkl",
    "gitlab_short": "glpat-abc",
    "npm_short": "npm_onlytwentychars12",
    "jwt_one_segment": "eyJ" + "h" * 30,
    "json_plain_field": '"token_kind": "session"',
    "basic_auth_header": "Authorization: Basic dXNlcjpwYXNzd29yZA==",
    "env_short_value": "SOME_API_KEY=short",
    "env_wrong_suffix": "XDG_CONFIG_HOME=/home/user/.config/somewhere",
    "prose": "the skeleton key token of doom, monkey=business as usual",
}


def test_redact_patterns_has_ten_plus_named_entries(memd):
    assert len(memd.REDACT_PATTERNS) >= 10
    for required in ("google_oauth", "github_pat", "anthropic_key",
                     "openai_key", "aws_access", "slack_token", "jwt",
                     "json_token_field", "bearer_header", "ssh_private_key",
                     "npm_token"):
        assert required in memd.REDACT_PATTERNS


def test_redact_patterns_compile_individually(memd):
    import re
    for name, pat in memd.REDACT_PATTERNS.items():
        re.compile(pat)  # raises re.error on a broken pattern


@pytest.mark.parametrize("name", sorted(POSITIVE))
def test_redact_positive(memd, name):
    sample = POSITIVE[name]
    out = memd.redact(sample)
    assert "[REDACTED]" in out, f"{name}: not redacted: {out}"
    # no long token fragment survives
    assert not any(tok in out for tok in sample.split() if len(tok) > 12), out


@pytest.mark.parametrize("name", sorted(NEGATIVE))
def test_redact_negative(memd, name):
    sample = NEGATIVE[name]
    assert memd.redact(sample) == sample


def test_anthropic_key_wins_over_openai_prefix(memd):
    # sk-ant- must be attributed to anthropic_key, not swallowed by openai_key
    m = memd.REDACT_RE.search("sk-ant-api03-" + "x" * 24)
    assert m is not None
    assert m.lastgroup == "anthropic_key"


def test_pem_block_uses_key_placeholder(memd):
    pem = ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\nmore\n"
           "-----END OPENSSH PRIVATE KEY-----")
    out = memd.redact(pem)
    assert out == "[REDACTED KEY]"


def test_redact_logs_pattern_name(memd, env):
    memd.redact("token ghp_" + "A1b2C3d4" * 4)
    with open(memd.LOG_PATH) as f:
        assert "redacted credential: github_pat" in f.read()


def test_redact_extra_patterns_from_config(memd, memd_config):
    memd_config(REDACT_EXTRA_PATTERNS=[r"CORP-SECRET-\d{6}"])
    out = memd.redact("deploy used CORP-SECRET-123456 today")
    assert "CORP-SECRET-123456" not in out
    assert "[REDACTED]" in out
    with open(memd.LOG_PATH) as f:
        assert "redacted credential: extra_0" in f.read()


def test_redact_extra_patterns_broken_regex_skipped(memd, memd_config):
    memd_config(REDACT_EXTRA_PATTERNS=["(unclosed", r"GOOD-\d{4}"])
    out = memd.redact("value GOOD-1234 stays safe")
    assert "GOOD-1234" not in out
    with open(memd.LOG_PATH) as f:
        assert "bad regex in REDACT_EXTRA_PATTERNS[0]" in f.read()


def test_redact_extra_patterns_default_off(memd):
    # no config -> only built-ins active, plain text untouched
    assert memd.redact("nothing secret here") == "nothing secret here"
