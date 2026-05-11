from __future__ import annotations

from agents import Agent, Runner, function_tool

from .config import AgentConfig
from .local_tools import LocalToolbox


INSTRUCTIONS = """
You are a senior coding agent working inside a local workspace.

Operating rules:
- Understand the project before proposing edits.
- Prefer small, reversible changes.
- Read relevant files before modifying them.
- Prefer apply_patch over write_file for changes to existing files.
- Use write_file only for new files or full-file generation.
- When the user explicitly asks you to use a named tool, call that tool before answering.
- Do not call tools just to satisfy process. If a task can be answered safely without workspace
  context, answer directly.
- If a tool returns enough information, stop calling tools and produce the final answer.
- Use run_command for verification when an allowed command fits.
- Never claim tests passed unless you actually ran them.
- Explain important risks, tradeoffs, and verification results.
- If dry_run blocks a write, clearly say what would have been changed.
- For tabular data analysis, use profile_table before correlations or target_analysis.
- Treat correlations as exploratory, not causal evidence.
"""


def build_agent(config: AgentConfig) -> Agent:
    toolbox = LocalToolbox(
        workspace=config.workspace,
        dry_run=config.dry_run,
        allowed_commands=config.allowed_commands,
    )

    @function_tool
    def list_files(path: str = ".", max_files: int = 200) -> str:
        """List files under a path inside the configured workspace."""
        return toolbox.list_files(path=path, max_files=max_files)

    @function_tool
    def read_file(path: str) -> str:
        """Read a UTF-8 text file inside the configured workspace."""
        return toolbox.read_file(path)

    @function_tool
    def search_files(
        query: str,
        path: str = ".",
        max_matches: int = 50,
        case_sensitive: bool = False,
    ) -> str:
        """Search for text in UTF-8 files inside the configured workspace."""
        return toolbox.search_files(
            query=query,
            path=path,
            max_matches=max_matches,
            case_sensitive=case_sensitive,
        )

    @function_tool
    def apply_patch(path: str, old_text: str, new_text: str, occurrence: int = 1) -> str:
        """Patch a file by replacing exact text and return a unified diff."""
        return toolbox.apply_patch(
            path=path,
            old_text=old_text,
            new_text=new_text,
            occurrence=occurrence,
        )

    @function_tool
    def diff_file(path: str, proposed_content: str) -> str:
        """Return a unified diff between a file and proposed replacement content."""
        return toolbox.diff_file(path=path, proposed_content=proposed_content)

    @function_tool
    def git_diff(path: str = ".") -> str:
        """Return git diff output for a path inside the workspace."""
        return toolbox.git_diff(path=path)

    @function_tool
    def project_summary(path: str = ".", max_files: int = 300) -> str:
        """Summarize the workspace files, extensions, directories, and key project files."""
        return toolbox.project_summary(path=path, max_files=max_files)

    @function_tool
    def write_file(path: str, content: str) -> str:
        """Create or replace a UTF-8 text file inside the configured workspace."""
        return toolbox.write_file(path=path, content=content)

    @function_tool
    def run_command(command: str, timeout_seconds: int = 60) -> str:
        """Run an allowlisted verification command inside the configured workspace."""
        return toolbox.run_command(command=command, timeout_seconds=timeout_seconds)

    @function_tool
    def profile_table(path: str, max_rows: int = 200_000) -> str:
        """Profile a CSV, Excel, or Parquet table: shape, dtypes, missingness, examples, stats."""
        return toolbox.profile_table(path=path, max_rows=max_rows)

    @function_tool
    def missing_report(path: str, max_rows: int = 200_000, top_n: int = 30) -> str:
        """Report missing values by column for a CSV, Excel, or Parquet table."""
        return toolbox.missing_report(path=path, max_rows=max_rows, top_n=top_n)

    @function_tool
    def correlations(
        path: str,
        method: str = "pearson",
        target: str | None = None,
        top_n: int = 20,
        max_rows: int = 200_000,
    ) -> str:
        """Find numeric Pearson or Spearman correlations in a table."""
        return toolbox.correlations(
            path=path,
            method=method,
            target=target,
            top_n=top_n,
            max_rows=max_rows,
        )

    @function_tool
    def group_summary(
        path: str,
        group_by: str,
        value_columns: list[str] | None = None,
        top_n: int = 30,
        max_rows: int = 200_000,
    ) -> str:
        """Summarize row counts and numeric metrics by a categorical column."""
        return toolbox.group_summary(
            path=path,
            group_by=group_by,
            value_columns=value_columns,
            top_n=top_n,
            max_rows=max_rows,
        )

    @function_tool
    def outlier_report(
        path: str,
        columns: list[str] | None = None,
        max_rows: int = 200_000,
    ) -> str:
        """Detect numeric outliers using the IQR rule."""
        return toolbox.outlier_report(path=path, columns=columns, max_rows=max_rows)

    @function_tool
    def target_analysis(
        path: str,
        target: str,
        max_rows: int = 200_000,
        top_n: int = 20,
    ) -> str:
        """Analyze a target column with correlations and category-level summaries."""
        return toolbox.target_analysis(path=path, target=target, max_rows=max_rows, top_n=top_n)

    @function_tool
    def forecast_baseline(
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        method: str = "moving_average",
        window: int = 3,
        max_rows: int = 200_000,
    ) -> str:
        """Create a naive or moving-average forecast for a time series table."""
        return toolbox.forecast_baseline(
            path=path,
            date_column=date_column,
            value_column=value_column,
            horizon=horizon,
            freq=freq,
            method=method,
            window=window,
            max_rows=max_rows,
        )

    @function_tool
    def forecast_arima(
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        order: list[int] | None = None,
        max_rows: int = 200_000,
    ) -> str:
        """Fit a univariate ARIMA forecast using statsmodels."""
        return toolbox.forecast_arima(
            path=path,
            date_column=date_column,
            value_column=value_column,
            horizon=horizon,
            freq=freq,
            order=order,
            max_rows=max_rows,
        )

    @function_tool
    def forecast_prophet(
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        max_rows: int = 200_000,
    ) -> str:
        """Fit a Prophet forecast when prophet is installed."""
        return toolbox.forecast_prophet(
            path=path,
            date_column=date_column,
            value_column=value_column,
            horizon=horizon,
            freq=freq,
            max_rows=max_rows,
        )

    @function_tool
    def forecast_auto_arima(
        path: str,
        date_column: str,
        value_column: str,
        horizon: int = 6,
        freq: str = "MS",
        seasonal: bool = False,
        max_rows: int = 200_000,
    ) -> str:
        """Fit an automatic ARIMA model using pmdarima."""
        return toolbox.forecast_auto_arima(
            path=path,
            date_column=date_column,
            value_column=value_column,
            horizon=horizon,
            freq=freq,
            seasonal=seasonal,
            max_rows=max_rows,
        )

    @function_tool
    def predictive_model(
        path: str,
        target: str,
        problem_type: str = "auto",
        model: str = "random_forest",
        date_column: str | None = None,
        exclude_columns: list[str] | None = None,
        test_size: float = 0.2,
        max_rows: int = 200_000,
    ) -> str:
        """Train an exploratory scikit-learn regression/classification model with metrics."""
        return toolbox.predictive_model(
            path=path,
            target=target,
            problem_type=problem_type,
            model=model,
            date_column=date_column,
            exclude_columns=exclude_columns,
            test_size=test_size,
            max_rows=max_rows,
        )

    @function_tool
    def model_availability(deep_check: bool = False) -> str:
        """Report which advanced predictive modeling libraries are installed and importable."""
        return toolbox.model_availability(deep_check=deep_check)

    @function_tool
    def plot_correlation_heatmap(
        path: str,
        output_name: str = "correlation_heatmap.png",
        method: str = "pearson",
        max_rows: int = 200_000,
    ) -> str:
        """Create a PNG correlation heatmap for numeric columns."""
        return toolbox.plot_correlation_heatmap(path, output_name, method, max_rows)

    @function_tool
    def plot_missingness(path: str, output_name: str = "missingness.png", max_rows: int = 200_000) -> str:
        """Create a PNG bar chart of missingness by column."""
        return toolbox.plot_missingness(path, output_name, max_rows)

    @function_tool
    def plot_distribution(
        path: str,
        column: str,
        output_name: str | None = None,
        max_rows: int = 200_000,
    ) -> str:
        """Create a PNG distribution plot for a numeric or categorical column."""
        return toolbox.plot_distribution(path, column, output_name, max_rows)

    @function_tool
    def plot_group_metric(
        path: str,
        group_by: str,
        value_column: str,
        metric: str = "mean",
        output_name: str | None = None,
        max_rows: int = 200_000,
        top_n: int = 30,
    ) -> str:
        """Create a PNG bar chart of a metric by group."""
        return toolbox.plot_group_metric(path, group_by, value_column, metric, output_name, max_rows, top_n)

    @function_tool
    def plot_time_series(
        path: str,
        date_column: str,
        value_column: str,
        output_name: str | None = None,
        freq: str = "MS",
        max_rows: int = 200_000,
    ) -> str:
        """Create a PNG line chart for a time series."""
        return toolbox.plot_time_series(path, date_column, value_column, output_name, freq, max_rows)

    @function_tool
    def plot_interactive_scatter(
        path: str,
        x: str,
        y: str,
        color: str | None = None,
        output_name: str = "scatter.html",
        max_rows: int = 50_000,
    ) -> str:
        """Create an interactive Plotly scatter chart as HTML."""
        return toolbox.plot_interactive_scatter(path, x, y, color, output_name, max_rows)

    @function_tool
    def discover_large_tables(pattern: str | None = None, max_files: int = 100) -> str:
        """Discover large CSV/Parquet tables in the workspace for DuckDB queries."""
        return toolbox.discover_large_tables(pattern=pattern, max_files=max_files)

    @function_tool
    def large_table_schema(path: str) -> str:
        """Inspect schema and sample rows of a large CSV/Parquet table using DuckDB."""
        return toolbox.large_table_schema(path)

    @function_tool
    def query_large_tables(sql: str, tables: dict[str, str], limit: int = 1000) -> str:
        """Run a safe read-only DuckDB SQL query over large CSV/Parquet tables."""
        return toolbox.query_large_tables(sql=sql, tables=tables, limit=limit)

    return Agent(
        name="Coding Agent",
        model=config.model,
        instructions=INSTRUCTIONS,
        tools=[
            list_files,
            read_file,
            search_files,
            apply_patch,
            diff_file,
            git_diff,
            project_summary,
            write_file,
            run_command,
            profile_table,
            missing_report,
            correlations,
            group_summary,
            outlier_report,
            target_analysis,
            forecast_baseline,
            forecast_arima,
            forecast_prophet,
            forecast_auto_arima,
            predictive_model,
            model_availability,
            plot_correlation_heatmap,
            plot_missingness,
            plot_distribution,
            plot_group_metric,
            plot_time_series,
            plot_interactive_scatter,
            discover_large_tables,
            large_table_schema,
            query_large_tables,
        ],
    )


def run_coding_agent(prompt: str, config: AgentConfig) -> str:
    if config.backend == "chat":
        from .chat_backend import run_chat_backend

        return run_chat_backend(prompt, config)

    if config.backend != "agents":
        raise ValueError(f"Unsupported AGENT_BACKEND: {config.backend}")

    agent = build_agent(config)
    result = Runner.run_sync(agent, prompt)
    return result.final_output
