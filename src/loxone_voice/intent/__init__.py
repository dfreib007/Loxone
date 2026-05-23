"""Intent engine: turns natural-language requests into Loxone tool calls."""

from .system_prompt import (
    SystemPrompt,
    build_system_prompt,
    list_known_categories,
)
from .tool import Tool, ToolError, ToolHandler, render_tool_result
from .tools import (
    GetStateArgs,
    ListControlsArgs,
    SetControlArgs,
    build_default_toolset,
    make_get_state_tool,
    make_list_controls_tool,
    make_list_rooms_tool,
    make_set_control_tool,
)

__all__ = [
    "GetStateArgs",
    "ListControlsArgs",
    "SetControlArgs",
    "SystemPrompt",
    "Tool",
    "ToolError",
    "ToolHandler",
    "build_default_toolset",
    "build_system_prompt",
    "list_known_categories",
    "make_get_state_tool",
    "make_list_controls_tool",
    "make_list_rooms_tool",
    "make_set_control_tool",
    "render_tool_result",
]
