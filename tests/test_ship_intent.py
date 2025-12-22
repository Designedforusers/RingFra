"""
Tests for natural language ship intent parsing.
"""

import pytest
from src.tools.code_tools import parse_ship_intent
from src.repos.manager import TestStrategy, ReviewStrategy


class TestShipIntentBasic:
    """Test basic ship commands."""
    
    def test_ship_it_default(self):
        """Default 'ship it' should use CI tests and Claude review."""
        result = parse_ship_intent("ship it")
        
        assert result["test_strategy"] == TestStrategy.CI
        assert result["review_strategy"] == ReviewStrategy.CLAUDE
        assert result["auto_merge"] is False
        assert "sonnet" in result["review_model"]
    
    def test_empty_string_defaults(self):
        """Empty string should use defaults."""
        result = parse_ship_intent("")
        
        assert result["test_strategy"] == TestStrategy.CI
        assert result["review_strategy"] == ReviewStrategy.CLAUDE


class TestTestStrategyParsing:
    """Test parsing of test strategy commands."""
    
    def test_no_tests(self):
        result = parse_ship_intent("ship it, no tests")
        assert result["test_strategy"] == TestStrategy.NONE
    
    def test_skip_tests(self):
        result = parse_ship_intent("ship it and skip tests")
        assert result["test_strategy"] == TestStrategy.NONE
    
    def test_without_tests(self):
        result = parse_ship_intent("deploy without tests")
        assert result["test_strategy"] == TestStrategy.NONE
    
    def test_run_tests_first(self):
        result = parse_ship_intent("run tests first then ship")
        assert result["test_strategy"] == TestStrategy.LOCAL
    
    def test_with_tests(self):
        result = parse_ship_intent("ship with tests")
        assert result["test_strategy"] == TestStrategy.LOCAL
    
    def test_local_tests(self):
        result = parse_ship_intent("run local tests before shipping")
        assert result["test_strategy"] == TestStrategy.LOCAL
    
    def test_test_first(self):
        result = parse_ship_intent("test first, then ship it")
        assert result["test_strategy"] == TestStrategy.LOCAL
    
    def test_both_tests(self):
        result = parse_ship_intent("run both tests locally and in CI")
        assert result["test_strategy"] == TestStrategy.BOTH
    
    def test_full_tests(self):
        result = parse_ship_intent("run full tests")
        assert result["test_strategy"] == TestStrategy.BOTH


class TestReviewStrategyParsing:
    """Test parsing of review strategy commands."""
    
    def test_no_review(self):
        result = parse_ship_intent("ship it, no review needed")
        assert result["review_strategy"] == ReviewStrategy.NONE
    
    def test_skip_review(self):
        result = parse_ship_intent("skip review and ship")
        assert result["review_strategy"] == ReviewStrategy.NONE
    
    def test_without_review(self):
        result = parse_ship_intent("push without review")
        assert result["review_strategy"] == ReviewStrategy.NONE
    
    def test_just_push(self):
        result = parse_ship_intent("just push it")
        assert result["review_strategy"] == ReviewStrategy.NONE
    
    def test_just_ship(self):
        result = parse_ship_intent("just ship it already")
        assert result["review_strategy"] == ReviewStrategy.NONE
    
    def test_human_review(self):
        result = parse_ship_intent("ship it but wait for human review")
        assert result["review_strategy"] == ReviewStrategy.HUMAN
    
    def test_manual_review(self):
        result = parse_ship_intent("I want manual review")
        assert result["review_strategy"] == ReviewStrategy.HUMAN
    
    def test_team_review(self):
        result = parse_ship_intent("send to team review")
        assert result["review_strategy"] == ReviewStrategy.HUMAN


class TestModelParsing:
    """Test parsing of Claude model selection."""
    
    def test_opus_review(self):
        result = parse_ship_intent("ship with opus review")
        assert "opus" in result["review_model"]
    
    def test_opus_mentioned(self):
        result = parse_ship_intent("use opus for the review")
        assert "opus" in result["review_model"]
    
    def test_default_is_sonnet(self):
        result = parse_ship_intent("ship it")
        assert "sonnet" in result["review_model"]


class TestEffortParsing:
    """Test parsing of review effort level."""
    
    def test_thorough_review(self):
        result = parse_ship_intent("do a thorough review")
        assert result["review_effort"] == "high"
    
    def test_careful_review(self):
        result = parse_ship_intent("be careful with the review")
        assert result["review_effort"] == "high"
    
    def test_deep_review(self):
        result = parse_ship_intent("deep review please")
        assert result["review_effort"] == "high"
    
    def test_quick_review(self):
        result = parse_ship_intent("quick review is fine")
        assert result["review_effort"] == "low"
    
    def test_fast_review(self):
        result = parse_ship_intent("fast review")
        assert result["review_effort"] == "low"
    
    def test_default_is_medium(self):
        result = parse_ship_intent("ship it")
        assert result["review_effort"] == "medium"


class TestAutoMergeParsing:
    """Test parsing of auto-merge commands."""
    
    def test_auto_merge(self):
        result = parse_ship_intent("ship with auto merge")
        assert result["auto_merge"] is True
    
    def test_auto_merge_hyphen(self):
        result = parse_ship_intent("enable auto-merge")
        assert result["auto_merge"] is True
    
    def test_merge_when_ready(self):
        result = parse_ship_intent("merge when ready")
        assert result["auto_merge"] is True
    
    def test_merge_if_pass(self):
        result = parse_ship_intent("merge if pass")
        assert result["auto_merge"] is True
    
    def test_default_no_auto_merge(self):
        result = parse_ship_intent("ship it")
        assert result["auto_merge"] is False


class TestQuickShipModes:
    """Test quick/yolo shipping modes."""
    
    def test_quick_ship(self):
        result = parse_ship_intent("quick ship")
        
        assert result["test_strategy"] == TestStrategy.NONE
        assert result["review_strategy"] == ReviewStrategy.NONE
        assert result["auto_merge"] is True
    
    def test_yolo(self):
        result = parse_ship_intent("yolo")
        
        assert result["test_strategy"] == TestStrategy.NONE
        assert result["review_strategy"] == ReviewStrategy.NONE
        assert result["auto_merge"] is True
    
    def test_yolo_case_insensitive(self):
        result = parse_ship_intent("YOLO ship it")
        
        assert result["test_strategy"] == TestStrategy.NONE
        assert result["review_strategy"] == ReviewStrategy.NONE
        assert result["auto_merge"] is True


class TestComplexCommands:
    """Test complex multi-part commands."""
    
    def test_tests_and_opus(self):
        result = parse_ship_intent("ship with tests and opus review")
        
        assert result["test_strategy"] == TestStrategy.LOCAL
        assert result["review_strategy"] == ReviewStrategy.CLAUDE
        assert "opus" in result["review_model"]
    
    def test_no_tests_auto_merge(self):
        result = parse_ship_intent("ship without tests and auto-merge")
        
        assert result["test_strategy"] == TestStrategy.NONE
        assert result["auto_merge"] is True
    
    def test_thorough_opus_review(self):
        result = parse_ship_intent("thorough opus review please")
        
        assert result["review_effort"] == "high"
        assert "opus" in result["review_model"]
    
    def test_full_pipeline(self):
        result = parse_ship_intent(
            "run tests first, get opus review, then auto-merge when ready"
        )
        
        assert result["test_strategy"] == TestStrategy.LOCAL
        assert "opus" in result["review_model"]
        assert result["auto_merge"] is True


class TestRealWorldPhrases:
    """Test phrases users might actually say."""
    
    def test_casual_ship(self):
        result = parse_ship_intent("okay ship it")
        assert result["test_strategy"] == TestStrategy.CI
        assert result["review_strategy"] == ReviewStrategy.CLAUDE
    
    def test_lets_ship(self):
        result = parse_ship_intent("let's ship this")
        # "just ship" not present, so defaults
        assert result["review_strategy"] == ReviewStrategy.CLAUDE
    
    def test_send_it(self):
        # "send it" doesn't trigger anything special
        result = parse_ship_intent("send it")
        assert result["test_strategy"] == TestStrategy.CI
    
    def test_push_to_prod(self):
        result = parse_ship_intent("just push to prod")
        assert result["review_strategy"] == ReviewStrategy.NONE
    
    def test_its_ready(self):
        result = parse_ship_intent("it's ready, ship it")
        assert result["test_strategy"] == TestStrategy.CI
    
    def test_make_a_pr(self):
        result = parse_ship_intent("make a PR")
        assert result["test_strategy"] == TestStrategy.CI
    
    def test_looks_good_ship_it(self):
        result = parse_ship_intent("looks good, ship it with tests")
        assert result["test_strategy"] == TestStrategy.LOCAL
