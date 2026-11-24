from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ConfigFormat = Literal["cli", "set"]


class UnsupportedChange(ValueError):
    """This change type cannot be rendered for this platform (fail closed)."""


@dataclass
class RenderedConfig:
    device: str
    platform: str
    lines: list[str] = field(default_factory=list)
    # cli: hierarchical CLI (Cisco/Arista style). set: Junos set/delete lines.
    format: ConfigFormat = "cli"
    # builtin renderer, or the user template that produced it.
    source: str = "builtin"

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.lines else "")

    def to_dict(self) -> dict[str, object]:
        return {
            "device": self.device,
            "platform": self.platform,
            "format": self.format,
            "source": self.source,
            "lines": list(self.lines),
        }
