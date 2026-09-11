from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiscoveredField:
    label: str
    name: str | None = None
    field_type: str = "text"
    required: bool = False
    options: list[str] = field(default_factory=list)
    current_value: str | None = None
    autocomplete: str | None = None
    element_id: str | None = None
    context: str = ""
