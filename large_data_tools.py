from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


MAX_QUERY_ROWS = 1000
SUPPORTED_SUFFIXES = {".csv", ".parquet"}


@dataclass(frozen=True)
class LargeDataTools:
    workspace: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", self.workspace.resolve())

    def resolve_path(self, requested_path: str) -> Path:
        candidate = (self.workspace / requested_path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ValueError(f"Path is outside workspace: {requested_path}")
        return candidate

    def discover_tables(self, pattern: str | None = None, max_files: int = 100) -> str:
        needle = (pattern or "").lower().strip()
        tables: list[dict[str, Any]] = []
        for path in self.workspace.iterdir():
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            if needle and needle not in path.name.lower():
                continue
            tables.append(_table_info(path, self.workspace))
            if len(tables) >= max_files:
                break
        return _to_json({"workspace": str(self.workspace), "tables": tables})

    def table_schema(self, path: str) -> str:
        file_path = self.resolve_path(path)
        relation_sql = _relation_sql(file_path)
        with duckdb.connect(database=":memory:") as conn:
            schema = conn.execute(f"DESCRIBE SELECT * FROM {relation_sql}").fetchdf()
            sample = conn.execute(f"SELECT * FROM {relation_sql} LIMIT 5").fetchdf()
        return _to_json(
            {
                "path": path,
                "schema": schema.to_dict(orient="records"),
                "sample": sample.astype(object).where(sample.notna(), None).to_dict(orient="records"),
            }
        )

    def query(self, sql: str, tables: dict[str, str], limit: int = MAX_QUERY_ROWS) -> str:
        if _is_unsafe_sql(sql):
            raise ValueError("Only read-only SELECT/WITH queries are allowed")
        limit = max(1, min(limit, MAX_QUERY_ROWS))
        with duckdb.connect(database=":memory:") as conn:
            for name, relative_path in tables.items():
                if not _SAFE_IDENTIFIER.fullmatch(name):
                    raise ValueError(f"Invalid table alias: {name}")
                file_path = self.resolve_path(relative_path)
                conn.execute(f"CREATE VIEW {name} AS SELECT * FROM {_relation_sql(file_path)}")
            query = f"SELECT * FROM ({sql.rstrip(';')}) AS agent_query LIMIT {limit}"
            frame = conn.execute(query).fetchdf()
        return _to_json(
            {
                "rows": int(len(frame)),
                "columns": [str(column) for column in frame.columns],
                "data": frame.astype(object).where(frame.notna(), None).to_dict(orient="records"),
            }
        )


def _table_info(path: Path, workspace: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(workspace)),
        "name": path.stem,
        "size": path.stat().st_size,
        "suffix": path.suffix.lower(),
    }


def _relation_sql(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return f"read_csv_auto('{escaped}', sample_size=-1, union_by_name=true)"
    if suffix == ".parquet":
        return f"read_parquet('{escaped}')"
    raise ValueError("Supported large-data formats: .csv, .parquet")


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_UNSAFE_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|copy|attach|detach|install|load|pragma|call)\b",
    re.IGNORECASE,
)


def _is_unsafe_sql(sql: str) -> bool:
    stripped = sql.strip().lower()
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return True
    return bool(_UNSAFE_SQL.search(sql))


def _to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
