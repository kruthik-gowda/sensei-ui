import inspect

import pytest


def test_signature_helpers_exist():
    from sensei.gitlab_client import (
        build_body_signature,
        build_inline_signature,
        extract_diff_lines,
    )

    assert build_body_signature("a b") == ("body", "a b")
    assert build_inline_signature("f.py", 3) == ("inline", "f.py", 3)
    assert extract_diff_lines("@@ -1,0 +5,1 @@\n+new line\n") == {5}


def test_formatter_helpers_exist():
    from sensei.formatter import format_inline_comment, format_nits_summary

    assert callable(format_inline_comment)
    assert callable(format_nits_summary)


def test_gitlab_client_exposes_expected_methods():
    from sensei.gitlab_client import GitLabClient

    for name in ("get_mr_diff", "get_existing_comments", "post_mr_comment"):
        assert hasattr(GitLabClient, name), name

    params = inspect.signature(GitLabClient.get_mr_diff).parameters
    assert "project_path" in params
    assert "mr_iid" in params


def test_config_loader_exists():
    from sensei.config import load_config

    assert callable(load_config)


def test_review_url_parser_exists():
    from sensei.review_platform import parse_review_url

    parsed = parse_review_url(
        "https://gitlab.com/group/proj/-/merge_requests/7"
    )
    assert str(parsed["project_path"]) == "group/proj"
    assert int(parsed["review_id"]) == 7
