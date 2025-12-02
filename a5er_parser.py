#!/usr/bin/env python3
"""A5:ER (.a5er) file parser - converts ER diagrams to Markdown format."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


# Pre-compiled regex patterns
_FIELD_PATTERN = re.compile(
    r'Field="([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*([^,]*)'
)
_INDEX_PATTERN = re.compile(r"Index=([^=]+)=(\d+),(.+)")
_SECTION_SPLIT_PATTERN = re.compile(r"\n(?=\[)")


@dataclass(slots=True, frozen=True)
class Column:
    """Represents a table column."""

    logical_name: str
    physical_name: str
    data_type: str
    not_null: bool
    is_pk: bool


@dataclass(slots=True, frozen=True)
class TableIndex:
    """Represents a table index."""

    name: str
    is_unique: bool
    columns: tuple[str, ...]


@dataclass(slots=True)
class Table:
    """Represents a database table."""

    physical_name: str
    logical_name: str
    page: str
    columns: list[Column] = field(default_factory=list)
    indexes: list[TableIndex] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class ForeignKey:
    """Represents a foreign key relationship."""

    parent_table: str
    child_table: str
    parent_column: str
    child_column: str


class A5erParser:
    """Parser for A5:ER (.a5er) files."""

    __slots__ = ("_content",)

    def __init__(self, content: str) -> None:
        if content.startswith("\ufeff"):
            content = content[1:]
        self._content = content

    @classmethod
    def from_file(cls, filepath: Path) -> A5erParser:
        """Create parser from file path."""
        content = filepath.read_text(encoding="utf-8")
        return cls(content)

    def parse(self) -> tuple[list[Table], list[ForeignKey]]:
        """Parse content and extract tables and foreign keys."""
        tables: list[Table] = []
        foreign_keys: list[ForeignKey] = []

        for section in self._iter_sections():
            lines = section.strip().split("\n")
            if not lines:
                continue

            header = lines[0].strip()
            body = lines[1:]

            if header == "[Entity]":
                table = self._parse_table(body)
                if table:
                    tables.append(table)
            elif header == "[Relation]":
                fk = self._parse_foreign_key(body)
                if fk:
                    foreign_keys.append(fk)

        return tables, foreign_keys

    def _iter_sections(self) -> Iterator[str]:
        """Iterate over sections in the content."""
        return iter(_SECTION_SPLIT_PATTERN.split(self._content))

    def _parse_table(self, lines: list[str]) -> Table | None:
        """Parse an [Entity] section into a Table."""
        physical_name = ""
        logical_name = ""
        page = ""
        columns: list[Column] = []
        indexes: list[TableIndex] = []

        for line in lines:
            line = line.strip()

            if line.startswith("PName="):
                physical_name = line[6:]
            elif line.startswith("LName="):
                logical_name = line[6:]
            elif line.startswith("Page="):
                page = line[5:]
            elif line.startswith("Field="):
                col = self._parse_column(line)
                if col:
                    columns.append(col)
            elif line.startswith("Index="):
                idx = self._parse_index(line)
                if idx:
                    indexes.append(idx)

        if not physical_name:
            return None

        return Table(
            physical_name=physical_name,
            logical_name=logical_name,
            page=page,
            columns=columns,
            indexes=indexes,
        )

    @staticmethod
    def _parse_column(line: str) -> Column | None:
        """Parse a Field line into a Column."""
        match = _FIELD_PATTERN.match(line)
        if not match:
            return None

        constraints = match.group(4)
        pk_flag = match.group(5).strip()

        return Column(
            logical_name=match.group(1),
            physical_name=match.group(2),
            data_type=match.group(3),
            not_null="NOT NULL" in constraints.upper(),
            is_pk=pk_flag == "0",
        )

    @staticmethod
    def _parse_index(line: str) -> TableIndex | None:
        """Parse an Index line into a TableIndex."""
        match = _INDEX_PATTERN.match(line)
        if not match:
            return None

        return TableIndex(
            name=match.group(1),
            is_unique=match.group(2) == "1",
            columns=tuple(col.strip() for col in match.group(3).split(",")),
        )

    @staticmethod
    def _parse_foreign_key(lines: list[str]) -> ForeignKey | None:
        """Parse a [Relation] section into a ForeignKey."""
        parent_table = ""
        child_table = ""
        parent_column = ""
        child_column = ""

        for line in lines:
            line = line.strip()

            if line.startswith("Entity1="):
                parent_table = line[8:]
            elif line.startswith("Entity2="):
                child_table = line[8:]
            elif line.startswith("Fields1="):
                parent_column = line[8:]
            elif line.startswith("Fields2="):
                child_column = line[8:]

        if not all((parent_table, child_table, parent_column, child_column)):
            return None

        return ForeignKey(
            parent_table=parent_table,
            child_table=child_table,
            parent_column=parent_column,
            child_column=child_column,
        )


class RelationIndex:
    """Index for fast relation lookups."""

    __slots__ = ("_outgoing", "_incoming")

    def __init__(self, foreign_keys: list[ForeignKey]) -> None:
        self._outgoing: dict[str, list[ForeignKey]] = {}
        self._incoming: dict[str, list[ForeignKey]] = {}

        for fk in foreign_keys:
            self._outgoing.setdefault(fk.child_table, []).append(fk)
            self._incoming.setdefault(fk.parent_table, []).append(fk)

    def get_outgoing(self, table_name: str) -> list[ForeignKey]:
        """Get foreign keys where table is the child (references others)."""
        return self._outgoing.get(table_name, [])

    def get_incoming(self, table_name: str) -> list[ForeignKey]:
        """Get foreign keys where table is the parent (is referenced)."""
        return self._incoming.get(table_name, [])


class MarkdownGenerator:
    """Generates Markdown output from parsed ER data."""

    __slots__ = ("_table_map", "_relation_index")

    def __init__(
        self,
        tables: list[Table],
        relation_index: RelationIndex,
    ) -> None:
        self._table_map = {t.physical_name: t for t in tables}
        self._relation_index = relation_index

    def generate_page(self, page_name: str, tables: list[Table]) -> str:
        """Generate Markdown for a single page."""
        buf = StringIO()
        write = buf.write

        write(f"# {page_name}\n\n")
        write(f"- テーブル数: {len(tables)}\n\n")

        # Table of contents
        write("## テーブル一覧\n\n")
        for table in tables:
            write(f"- [{table.physical_name}](#{table.physical_name.lower()}) ({table.logical_name})\n")
        write("\n")

        # Table details
        for table in tables:
            self._write_table(buf, table)

        return buf.getvalue()

    def _write_table(self, buf: StringIO, table: Table) -> None:
        """Write a single table's Markdown to buffer."""
        write = buf.write

        write(f"### {table.physical_name} ({table.logical_name})\n\n")

        # Columns
        write("#### カラム\n\n")
        write("| 物理名 | 論理名 | 型 | NOT NULL | PK |\n")
        write("|--------|--------|-----|:--------:|:--:|\n")

        for col in table.columns:
            not_null = "✓" if col.not_null else ""
            pk = "✓" if col.is_pk else ""
            write(f"| {col.physical_name} | {col.logical_name} | {col.data_type} | {not_null} | {pk} |\n")

        write("\n")

        # Indexes
        if table.indexes:
            write("#### インデックス\n\n")
            for idx in table.indexes:
                unique_mark = "[UNIQUE] " if idx.is_unique else ""
                cols = ", ".join(idx.columns)
                write(f"- {unique_mark}`{idx.name}`: {cols}\n")
            write("\n")

        # Outgoing relations
        outgoing = self._relation_index.get_outgoing(table.physical_name)
        if outgoing:
            write("#### 参照するテーブル (外部キー)\n\n")
            for fk in outgoing:
                parent = self._table_map.get(fk.parent_table)
                label = (
                    f"{fk.parent_table} ({parent.logical_name})"
                    if parent
                    else fk.parent_table
                )
                write(f"- `{fk.child_column}` → `{label}.{fk.parent_column}`\n")
            write("\n")

        # Incoming relations
        incoming = self._relation_index.get_incoming(table.physical_name)
        if incoming:
            write("#### 参照されるテーブル\n\n")
            for fk in incoming:
                child = self._table_map.get(fk.child_table)
                label = (
                    f"{fk.child_table} ({child.logical_name})"
                    if child
                    else fk.child_table
                )
                write(f"- `{label}.{fk.child_column}` → `{fk.parent_column}`\n")
            write("\n")

        write("---\n\n")


def main() -> None:
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: uv run a5er_parser.py <path-to-a5er-file>")
        sys.exit(1)

    input_path = Path(sys.argv[1])

    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    print(f"Parsing: {input_path}")

    # Parse
    parser = A5erParser.from_file(input_path)
    tables, foreign_keys = parser.parse()

    print(f"Found {len(tables)} tables and {len(foreign_keys)} relations")

    # Build indexes
    relation_index = RelationIndex(foreign_keys)

    # Group by page
    pages: dict[str, list[Table]] = {}
    for table in tables:
        pages.setdefault(table.page, []).append(table)

    # Sort tables within each page
    for page_tables in pages.values():
        page_tables.sort(key=lambda t: t.physical_name)

    # Generate output
    generator = MarkdownGenerator(tables, relation_index)
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for page_name, page_tables in pages.items():
        safe_filename = page_name.replace(" ", "_").replace("/", "_")
        markdown = generator.generate_page(page_name, page_tables)

        output_path = output_dir / f"{safe_filename}_{timestamp}.md"
        output_path.write_text(markdown, encoding="utf-8")
        print(f"Output written to: {output_path}")


if __name__ == "__main__":
    main()
