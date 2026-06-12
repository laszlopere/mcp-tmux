"""Shared parameter annotations that carry JSON-schema descriptions.

The MCP SDK builds each tool's ``inputSchema`` from its function signature; it
does NOT read docstring prose for per-parameter docs. So a parameter description
must ride on the type via ``Annotated[<type>, Field(description=...)]``.

Parameters that recur across many tools with identical meaning are defined here
once so the wording stays consistent and DRY; tool-specific parameters are
annotated inline at their definition. Apply an alias like a normal type, keeping
the runtime default in the signature, e.g. ``target: Target = None`` or
``target_pane: TargetPane``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

# The optional remote selector present on (almost) every tool.
Target = Annotated[
    str | None,
    Field(
        description=(
            "Host or named SSH profile from the config to run against; omit "
            '(or pass "local") to act on the local machine.'
        )
    ),
]

# A required pane target.
TargetPane = Annotated[
    str,
    Field(description='The pane to act on, as a tmux target (e.g. "%3" or "sess:1.2").'),
]

# An optional pane target — defaults to the active pane when omitted.
TargetPaneOpt = Annotated[
    str | None,
    Field(
        description=(
            'The pane to act on (e.g. "%3" or "sess:1.2"); defaults to the active '
            "pane when omitted."
        )
    ),
]
