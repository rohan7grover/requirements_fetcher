from requirements_fetcher.browser import action_safety_error
from requirements_fetcher.models import BrowserAction


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
