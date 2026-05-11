from __future__ import annotations

import difflib
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


MAX_READ_BYTES = 120_000
MAX_SEARCH_FILE_BYTES = 500_000
MAX_PATCH_BYTES = 250_000
SKIPPED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".test-tmp",
    ".venv",
    "__pycache__",
    "node_modules",
}
KEY_PROJECT_FILES = (
    "README.md",
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "setup.py",
    "uv.lock",
    "poetry.lock",
)


class ToolError(ValueError):
    """Raised when a local tool refuses an unsafe or invalid operation."""


@dataclass(frozen=True)
class LocalToolbox:
    workspace: Path
    dry_run: bool = True
    allowed_commands: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", self.workspace.resolve())

    def resolve_path(self, requested_path: str) -> Path:
        candidate = (self.workspace / requested_path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ToolError(f"Path is outside workspace: {requested_path}")
        return candidate

    def list_files(self, path: str = ".", max_files: int = 200) -> str:
        root = self.resolve_path(path)
        if not root.exists():
            raise ToolError(f"Path does not exist: {path}")

        files: list[str] = []
        if root.is_file():
            return str(root.relative_to(self.workspace))

        for child in root.rglob("*"):
            if len(files) >= max_files:
                files.append(f"... truncated at {max_files} files")
                break
            if any(part in SKIPPED_DIRS for part in child.relative_to(root).parts):
                continue
            if child.is_file():
                files.append(str(child.relative_to(self.workspace)))

        return "\n".join(files) or "(no files found)"

    def read_file(self, path: str) -> str:
        file_path = self.resolve_path(path)
        if not file_path.is_file():
            raise ToolError(f"Not a file: {path}")

        size = file_path.stat().st_size
        if size > MAX_READ_BYTES:
            raise ToolError(f"File is too large to read safely: {path} ({size} bytes)")

        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"File is not valid UTF-8 text: {path}") from exc

    def search_files(
        self,
        query: str,
        path: str = ".",
        max_matches: int = 50,
        case_sensitive: bool = False,
    ) -> str:
        if not query:
            raise ToolError("Search query cannot be empty")

        root = self.resolve_path(path)
        if not root.exists():
            raise ToolError(f"Path does not exist: {path}")

        needle = query if case_sensitive else query.lower()
        files = [root] if root.is_file() else root.rglob("*")
        matches: list[str] = []

        for file_path in files:
            if len(matches) >= max_matches:
                matches.append(f"... truncated at {max_matches} matches")
                break
            if not file_path.is_file():
                continue
            relative_path = file_path.relative_to(self.workspace)
            if any(part in SKIPPED_DIRS for part in relative_path.parts):
                continue
            if file_path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue

            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue

            for line_number, line in enumerate(lines, start=1):
                haystack = line if case_sensitive else line.lower()
                if needle in haystack:
                    matches.append(f"{relative_path}:{line_number}: {line.strip()}")
                    if len(matches) >= max_matches:
                        break

        return "\n".join(matches) or "(no matches found)"

    def apply_patch(
        self,
        path: str,
        old_text: str,
        new_text: str,
        occurrence: int = 1,
    ) -> str:
        if not old_text:
            raise ToolError("old_text cannot be empty")
        if occurrence < 0:
            raise ToolError("occurrence must be 0 for all replacements or a positive index")

        file_path = self.resolve_path(path)
        if not file_path.is_file():
            raise ToolError(f"Not a file: {path}")
        if file_path.stat().st_size > MAX_PATCH_BYTES:
            raise ToolError(f"File is too large to patch safely: {path}")

        original = self.read_file(path)
        match_count = original.count(old_text)
        if match_count == 0:
            raise ToolError("old_text was not found in the target file")
        if occurrence > match_count:
            raise ToolError(f"Requested occurrence {occurrence}, but only {match_count} found")

        if occurrence == 0:
            updated = original.replace(old_text, new_text)
        else:
            updated = self._replace_occurrence(original, old_text, new_text, occurrence)

        diff = self._unified_diff(path, original, updated)
        if self.dry_run:
            return f"DRY RUN: would patch {path}\n\n{diff}"

        file_path.write_text(updated, encoding="utf-8")
        return f"Patched {path}\n\n{diff}"

    def diff_file(self, path: str, proposed_content: str) -> str:
        original = self.read_file(path)
        return self._unified_diff(path, original, proposed_content)

    def git_diff(self, path: str = ".") -> str:
        target = self.resolve_path(path)
        try:
            completed = subprocess.run(
                ["git", "diff", "--", str(target.relative_to(self.workspace))],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ToolError("git is not available on PATH") from exc

        output = completed.stdout.strip()
        error = completed.stderr.strip()
        if completed.returncode != 0:
            raise ToolError(error or f"git diff failed with exit code {completed.returncode}")
        return output or "(no git diff)"

    def project_summary(self, path: str = ".", max_files: int = 300) -> str:
        root = self.resolve_path(path)
        if not root.exists():
            raise ToolError(f"Path does not exist: {path}")

        files: list[Path] = []
        if root.is_file():
            files = [root]
        else:
            for child in root.rglob("*"):
                if len(files) >= max_files:
                    break
                if not child.is_file():
                    continue
                relative_path = child.relative_to(self.workspace)
                if any(part in SKIPPED_DIRS for part in relative_path.parts):
                    continue
                files.append(child)

        extension_counts = Counter(path.suffix.lower() or "(none)" for path in files)
        key_files = [
            str(file_path.relative_to(self.workspace))
            for file_path in files
            if file_path.name in KEY_PROJECT_FILES
        ]
        top_dirs = Counter(
            file_path.relative_to(self.workspace).parts[0]
            for file_path in files
            if file_path.relative_to(self.workspace).parts
        )

        lines = [
            f"workspace: {self.workspace}",
            f"scanned_files: {len(files)}" + (" (truncated)" if len(files) >= max_files else ""),
            "top_extensions: "
            + ", ".join(f"{extension}:{count}" for extension, count in extension_counts.most_common(8)),
            "top_dirs: " + ", ".join(f"{directory}:{count}" for directory, count in top_dirs.most_common(8)),
            "key_files: " + (", ".join(key_files[:20]) if key_files else "(none found)"),
        ]
        return "\n".join(lines)

    def write_file(self, path: str, content: str) -> str:
        file_path = self.resolve_path(path)
        if self.dry_run:
            return f"DRY RUN: would write {len(content)} characters to {path}"

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {path}"

    def run_command(self, command: str, timeout_seconds: int = 60) -> str:
        normalized = " ".join(command.split())
        if normalized not in self.allowed_commands:
            allowed = ", ".join(self.allowed_commands) or "(none)"
            raise ToolError(f"Command is not allowed: {command}. Allowed: {allowed}")

        try:
            completed = subprocess.run(
                normalized.split(),
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            executable = normalized.split()[0]
            raise ToolError(f"Executable is not available on PATH: {executable}") from exc

        output = completed.stdout.strip()
        error = completed.stderr.strip()
        parts = [f"exit_code={completed.returncode}"]
        if output:
            parts.append(f"stdout:\n{output}")
        if error:
            parts.append(f"stderr:\n{error}")
        return "\n\n".join(parts)

    def profile_table(self, path: str, max_rows: int = 200_000) -> str:
        return self._call_data_tool("profile_table", path=path, max_rows=max_rows)

    def missing_report(self, path: str, max_rows: int = 200_000, top_n: int = 30) -> str:
        return self._call_data_tool("missing_report", path=path, max_rows=max_rows, top_n=top_n)

    def correlations(
        self,
        path: str,
        method: str = "pearson",
        target: str | None = None,
        top_n: int = 20,
        max_rows: int = 200_000,
    ) -> str:
        return self._call_data_tool(
            "correlations",
            path=path,
            method=method,
            target=target,
            top_n=top_n,
            max_rows=max_rows,
        )

    def group_summary(
        self,
        path: str,
        group_by: str,
        value_columns: list[str] | None = None,
        top_n: int = 30,
        max_rows: int = 200_000,
    ) -> str:
        return self._call_data_tool(
            "group_summary",
            path=path,
            group_by=group_by,
            value_columns=value_columns,
            top_n=top_n,
            max_rows=max_rows,
        )

    def outlier_report(
        self,
        path: str,
        columns: list[str] | None = None,
        max_rows: int = 200_000,
    ) -> str:
        return self._call_data_tool(
            "outlier_report", path=path, columns=columns, max_rows=max_rows
        )

    def target_analysis(
        self,
        path: str,
        target: str,
        max_rows: int = 200_000,
        top_n: int = 20,
    ) -> str:
        return self._call_data_tool(
            "target_analysis",
            path=path,
            target=target,
            max_rows=max_rows,
            top_n=top_n,
        )

    def forecast_baseline(
        self,
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        method: str = "moving_average",
        window: int = 3,
        max_rows: int = 200_000,
    ) -> str:
        return self._call_data_tool(
            "forecast_baseline",
            path=path,
            date_column=date_column,
            value_column=value_column,
            horizon=horizon,
            freq=freq,
            method=method,
            window=window,
            max_rows=max_rows,
        )

    def forecast_arima(
        self,
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        order: list[int] | None = None,
        max_rows: int = 200_000,
    ) -> str:
        return self._call_data_tool(
            "forecast_arima",
            path=path,
            date_column=date_column,
            value_column=value_column,
            horizon=horizon,
            freq=freq,
            order=order,
            max_rows=max_rows,
        )

    def forecast_prophet(
        self,
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        max_rows: int = 200_000,
    ) -> str:
        return self._call_data_tool(
            "forecast_prophet",
            path=path,
            date_column=date_column,
            value_column=value_column,
            horizon=horizon,
            freq=freq,
            max_rows=max_rows,
        )

    def forecast_auto_arima(
        self,
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        seasonal: bool = False,
        max_rows: int = 200_000,
    ) -> str:
        return self._call_data_tool(
            "forecast_auto_arima",
            path=path,
            date_column=date_column,
            value_column=value_column,
            horizon=horizon,
            freq=freq,
            seasonal=seasonal,
            max_rows=max_rows,
        )

    def predictive_model(
        self,
        path: str,
        target: str,
        problem_type: str = "auto",
        model: str = "random_forest",
        date_column: str | None = None,
        exclude_columns: list[str] | None = None,
        test_size: float = 0.2,
        max_rows: int = 200_000,
    ) -> str:
        return self._call_data_tool(
            "predictive_model",
            path=path,
            target=target,
            problem_type=problem_type,
            model=model,
            date_column=date_column,
            exclude_columns=exclude_columns,
            test_size=test_size,
            max_rows=max_rows,
        )

    def model_availability(self, deep_check: bool = False) -> str:
        return self._call_data_tool("model_availability", deep_check=deep_check)

    def discover_large_tables(self, pattern: str | None = None, max_files: int = 100) -> str:
        return self._call_large_data_tool("discover_tables", pattern=pattern, max_files=max_files)

    def large_table_schema(self, path: str) -> str:
        return self._call_large_data_tool("table_schema", path=path)

    def query_large_tables(self, sql: str, tables: dict[str, str], limit: int = 1000) -> str:
        return self._call_large_data_tool("query", sql=sql, tables=tables, limit=limit)

    def plot_correlation_heatmap(
        self,
        path: str,
        output_name: str = "correlation_heatmap.png",
        method: str = "pearson",
        max_rows: int = 200_000,
    ) -> str:
        return self._call_plot_tool(
            "plot_correlation_heatmap",
            path=path,
            output_name=output_name,
            method=method,
            max_rows=max_rows,
        )

    def plot_missingness(
        self,
        path: str,
        output_name: str = "missingness.png",
        max_rows: int = 200_000,
    ) -> str:
        return self._call_plot_tool(
            "plot_missingness",
            path=path,
            output_name=output_name,
            max_rows=max_rows,
        )

    def plot_distribution(
        self,
        path: str,
        column: str,
        output_name: str | None = None,
        max_rows: int = 200_000,
    ) -> str:
        return self._call_plot_tool(
            "plot_distribution",
            path=path,
            column=column,
            output_name=output_name,
            max_rows=max_rows,
        )

    def plot_group_metric(
        self,
        path: str,
        group_by: str,
        value_column: str,
        metric: str = "mean",
        output_name: str | None = None,
        max_rows: int = 200_000,
        top_n: int = 30,
    ) -> str:
        return self._call_plot_tool(
            "plot_group_metric",
            path=path,
            group_by=group_by,
            value_column=value_column,
            metric=metric,
            output_name=output_name,
            max_rows=max_rows,
            top_n=top_n,
        )

    def plot_time_series(
        self,
        path: str,
        date_column: str,
        value_column: str,
        output_name: str | None = None,
        freq: str = "MS",
        max_rows: int = 200_000,
    ) -> str:
        return self._call_plot_tool(
            "plot_time_series",
            path=path,
            date_column=date_column,
            value_column=value_column,
            output_name=output_name,
            freq=freq,
            max_rows=max_rows,
        )

    def plot_interactive_scatter(
        self,
        path: str,
        x: str,
        y: str,
        color: str | None = None,
        output_name: str = "scatter.html",
        max_rows: int = 50_000,
    ) -> str:
        return self._call_plot_tool(
            "plot_interactive_scatter",
            path=path,
            x=x,
            y=y,
            color=color,
            output_name=output_name,
            max_rows=max_rows,
        )

    def _call_data_tool(self, method_name: str, **kwargs) -> str:
        from .data_tools import DataTools

        try:
            method = getattr(DataTools(self.workspace), method_name)
            return method(**kwargs)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    def _call_plot_tool(self, method_name: str, **kwargs) -> str:
        from .plot_tools import PlotTools

        try:
            method = getattr(PlotTools(self.workspace), method_name)
            return method(**kwargs)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    def _call_large_data_tool(self, method_name: str, **kwargs) -> str:
        from .large_data_tools import LargeDataTools

        try:
            method = getattr(LargeDataTools(self.workspace), method_name)
            return method(**kwargs)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @staticmethod
    def _replace_occurrence(text: str, old_text: str, new_text: str, occurrence: int) -> str:
        start = -1
        cursor = 0
        for _ in range(occurrence):
            start = text.find(old_text, cursor)
            if start == -1:
                return text
            cursor = start + len(old_text)
        return text[:start] + new_text + text[start + len(old_text) :]

    @staticmethod
    def _unified_diff(path: str, original: str, updated: str) -> str:
        diff = difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
            lineterm="",
        )
        return "\n".join(diff) or "(no changes)"
