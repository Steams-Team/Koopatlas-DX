"""
kdx/names.py - world and level names from the game's text archive (romfs/Message/CommonMsg_<lang>.szs).

Read-only: edit the text with an MSBT editor. Koopatlas DX only shows the names next to the points.
"""
from __future__ import annotations

from pathlib import Path

from . import archive, msbt
from .world import course_label


class Names:
    def __init__(self):
        self.worlds = {}       # WorldName_1 -> "Acorn Plains"
        self.courses = {}      # CourseName_1-1 -> "Acorn Plains Way"
        self.source = None

    @classmethod
    def from_szs(cls, path):
        n = cls()
        entries, _info, _ = archive.read_szs(path)
        for name, data in entries:
            base = Path(name).name.lower()
            if base == "world_name.msbt":
                n.worlds = msbt.parse(data)
            elif base == "course_name.msbt":
                n.courses = msbt.parse(data)
        n.source = Path(path)
        return n

    @classmethod
    def from_msbt_files(cls, world_name=None, course_name=None):
        n = cls()
        if world_name:
            n.worlds = msbt.parse(Path(world_name).read_bytes())
        if course_name:
            n.courses = msbt.parse(Path(course_name).read_bytes())
        return n

    def world(self, number):
        return self.worlds.get("WorldName_%d" % number)

    def point(self, pid):
        """(name, label) for a point, or (None, label/None). The label is inferred from the point ID."""
        label = course_label(pid)
        return (self.courses.get(label) if label else None), label
