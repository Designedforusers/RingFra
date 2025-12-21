"""
Base tool interface and utilities.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Abstract base class for tools."""

    name: str
    description: str

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """
        Execute the tool with the given arguments.

        Returns:
            str: Voice-friendly result message
        """
        pass

    def get_schema(self) -> dict[str, Any]:
        """Get the tool's input schema for LLM."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._get_input_schema(),
        }

    @abstractmethod
    def _get_input_schema(self) -> dict[str, Any]:
        """Get the input schema for this tool."""
        pass


def truncate_for_voice(text: str, max_length: int = 200) -> str:
    """
    Truncate text for voice output.

    Args:
        text: The text to truncate
        max_length: Maximum length

    Returns:
        str: Truncated text
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def format_list_for_voice(items: list[str], max_items: int = 5) -> str:
    """
    Format a list for voice output.

    Args:
        items: List of items
        max_items: Maximum items to include

    Returns:
        str: Voice-friendly list
    """
    if not items:
        return "none"

    if len(items) <= max_items:
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + f", and {items[-1]}"

    shown = items[:max_items]
    remaining = len(items) - max_items
    return ", ".join(shown) + f", and {remaining} more"
