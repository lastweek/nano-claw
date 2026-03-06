"""Unit tests for admin redaction helpers."""

from src.server.admin_redaction import REDACTED_TOKEN, preview_text, redact_config_object, redact_text


def test_redact_text_masks_secret_like_values():
    raw = "Authorization: Bearer token_ABCDEF1234567890 and sk-1234567890ABCDEF"
    redacted = redact_text(raw)
    assert REDACTED_TOKEN in redacted
    assert "Bearer" not in redacted
    assert "sk-1234567890ABCDEF" not in redacted


def test_preview_text_truncates_and_handles_none():
    assert preview_text(None) is None
    preview = preview_text("x" * 400, redacted=False, limit=12)
    assert preview == ("x" * 12 + "...")


def test_redact_config_object_masks_sensitive_keys():
    payload = {
        "llm": {"api_key": "abc123", "model": "fake"},
        "nested": {"secretToken": "hello", "safe": 1},
        "items": [{"password": "pw"}, {"name": "ok"}],
    }
    sanitized = redact_config_object(payload)
    assert sanitized["llm"]["api_key"] == REDACTED_TOKEN
    assert sanitized["nested"]["secretToken"] == REDACTED_TOKEN
    assert sanitized["items"][0]["password"] == REDACTED_TOKEN
    assert sanitized["llm"]["model"] == "fake"
