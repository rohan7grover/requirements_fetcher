from google.genai import errors

from feature_blueprint.browser import _element_priority, action_safety_error
from feature_blueprint.llm import _is_transient_api_error
from feature_blueprint.models import BrowserAction


def test_rejects_submit_and_destructive_controls() -> None:
    submit = BrowserAction(action="click", element_id="el-1")

    assert action_safety_error(submit, {"type": "submit", "text": "Create issue"})
    assert action_safety_error(submit, {"type": "button", "text": "Delete issue"})


def test_allows_navigation_to_new_issue_form() -> None:
    action = BrowserAction(action="click", element_id="el-1")

    assert action_safety_error(action, {"tag": "a", "text": "New issue", "href": "/issues/new"}) is None
    assert action_safety_error(action, {"tag": "button", "text": "Create issue"})


def test_typing_is_limited_to_search_controls() -> None:
    action = BrowserAction(action="type", element_id="el-1", value="bug")

    assert action_safety_error(action, {"tag": "input", "name": "title", "type": "text"})
    assert action_safety_error(
        action, {"tag": "input", "placeholder": "Search all issues", "type": "search"}
    ) is None


def test_select_is_limited_to_filter_controls() -> None:
    action = BrowserAction(action="select", element_id="el-1", value="open")

    assert action_safety_error(action, {"tag": "select", "name": "timezone"})
    assert action_safety_error(action, {"tag": "select", "name": "status filter"}) is None


def test_complete_workflow_needs_no_element() -> None:
    action = BrowserAction(action="complete_workflow", reason="Issue list is visible")

    assert action.element_id is None


def test_relevant_navigation_is_prioritized_over_current_page_link() -> None:
    current = "https://example.com/org/repo/issues"
    keywords = ["issues", "detail", "comments"]

    issue_score = _element_priority(
        {"tag": "a", "text": "A real bug", "href": "/org/repo/issues/123"},
        current,
        keywords,
        150,
    )
    current_score = _element_priority(
        {"tag": "a", "text": "Issues", "href": "/org/repo/issues"},
        current,
        keywords,
        1,
    )

    assert issue_score > current_score


def test_only_rate_limits_and_server_errors_are_transient() -> None:
    assert _is_transient_api_error(errors.ServerError(503, {"message": "busy"}))
    assert _is_transient_api_error(errors.ClientError(429, {"message": "slow down"}))
    assert not _is_transient_api_error(errors.ClientError(401, {"message": "bad key"}))
