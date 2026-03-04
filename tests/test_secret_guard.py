"""Tests for the git secret guardrail."""

from src.secret_guard import Finding, commits_from_pre_push, scan_unified_diff


def test_scan_unified_diff_flags_real_api_key() -> None:
    sample_secret = "eda371ce16894da4b71b30f2d46" "d63b2.V4SefPocXL0xPkTg"

    diff_text = f"""\
diff --git a/test-zai.py b/test-zai.py
--- a/test-zai.py
+++ b/test-zai.py
@@ -0,0 +1,2 @@
+client = OpenAI(api_key="{sample_secret}")
+print("hello")
"""

    findings = scan_unified_diff(diff_text)

    assert findings == [
        Finding(
            path="test-zai.py",
            line_number=1,
            variable_name="api_key",
            preview=f'client = OpenAI(api_key="{sample_secret}")',
        )
    ]


def test_scan_unified_diff_ignores_placeholders() -> None:
    diff_text = """\
diff --git a/.env.example b/.env.example
--- a/.env.example
+++ b/.env.example
@@ -0,0 +1,3 @@
+OPENAI_API_KEY=sk-...
+CUSTOM_API_KEY=your-key-if-needed
+api_key=os.environ["OPENAI_API_KEY"]
"""

    assert scan_unified_diff(diff_text) == []


def test_commits_from_pre_push_uses_unique_commit_list(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run_git(args: list[str]) -> str:
        calls.append(args)
        if args == ["rev-list", "localsha", "--not", "--remotes"]:
            return "commit-a\ncommit-b\n"
        if args == ["rev-list", "remote123..secondlocal"]:
            return "commit-b\ncommit-c\n"
        raise AssertionError(f"Unexpected git args: {args}")

    monkeypatch.setattr("src.secret_guard.run_git", fake_run_git)

    commits = commits_from_pre_push(
        "refs/heads/main localsha refs/heads/main 0000000000000000000000000000000000000000\n"
        "refs/heads/feature secondlocal refs/heads/feature remote123\n"
    )

    assert commits == ["commit-a", "commit-b", "commit-c"]
    assert calls == [
        ["rev-list", "localsha", "--not", "--remotes"],
        ["rev-list", "remote123..secondlocal"],
    ]
