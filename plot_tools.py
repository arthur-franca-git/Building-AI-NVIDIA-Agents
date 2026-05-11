from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from .data_tools import DataTools


PLOTS_DIR = "plots"


@dataclass(frozen=True)
class PlotTools:
    workspace: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", self.workspace.resolve())

    def plot_correlation_heatmap(
        self,
        path: str,
        output_name: str = "correlation_heatmap.png",
        method: str = "pearson",
        max_rows: int = 200_000,
    ) -> str:
        frame = DataTools(self.workspace).load_table(path, max_rows=max_rows)
        numeric = frame.select_dtypes(include="number")
        if numeric.shape[1] < 2:
            raise ValueError("Need at least two numeric columns for a correlation heatmap")
        corr = numeric.corr(method=method)
        output_path = self._output_path(output_name, ".png")

        plt.figure(figsize=(max(8, len(corr.columns) * 0.7), max(6, len(corr.columns) * 0.55)))
        sns.heatmap(corr, annot=len(corr.columns) <= 12, cmap="vlag", center=0, linewidths=0.5)
        plt.title(f"{method.title()} Correlation Heatmap")
        plt.tight_layout()
        plt.savefig(output_path, dpi=160)
        plt.close()
        return str(output_path)

    def plot_missingness(
        self,
        path: str,
        output_name: str = "missingness.png",
        max_rows: int = 200_000,
    ) -> str:
        frame = DataTools(self.workspace).load_table(path, max_rows=max_rows)
        missing_pct = frame.isna().mean().sort_values(ascending=False)
        output_path = self._output_path(output_name, ".png")

        plt.figure(figsize=(10, max(5, len(missing_pct) * 0.25)))
        sns.barplot(x=missing_pct.values, y=missing_pct.index, color="#3b82f6")
        plt.xlabel("Missing %")
        plt.ylabel("Column")
        plt.title("Missingness by Column")
        plt.tight_layout()
        plt.savefig(output_path, dpi=160)
        plt.close()
        return str(output_path)

    def plot_distribution(
        self,
        path: str,
        column: str,
        output_name: str | None = None,
        max_rows: int = 200_000,
    ) -> str:
        frame = DataTools(self.workspace).load_table(path, max_rows=max_rows)
        if column not in frame.columns:
            raise ValueError(f"Column not found: {column}")
        output_path = self._output_path(output_name or f"{column}_distribution.png", ".png")

        plt.figure(figsize=(9, 5))
        if pd.api.types.is_numeric_dtype(frame[column]):
            sns.histplot(frame[column].dropna(), kde=True, color="#0f766e")
            plt.xlabel(column)
        else:
            counts = frame[column].astype(str).value_counts().head(30)
            sns.barplot(x=counts.values, y=counts.index, color="#0f766e")
            plt.xlabel("Count")
            plt.ylabel(column)
        plt.title(f"Distribution: {column}")
        plt.tight_layout()
        plt.savefig(output_path, dpi=160)
        plt.close()
        return str(output_path)

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
        frame = DataTools(self.workspace).load_table(path, max_rows=max_rows)
        if group_by not in frame.columns:
            raise ValueError(f"group_by column not found: {group_by}")
        if value_column not in frame.columns:
            raise ValueError(f"value_column not found: {value_column}")
        if metric not in {"mean", "median", "sum", "count"}:
            raise ValueError("metric must be mean, median, sum, or count")

        grouped = frame.groupby(group_by, dropna=False)[value_column]
        if metric == "count":
            values = grouped.count()
        else:
            values = getattr(grouped, metric)()
        values = values.sort_values(ascending=False).head(top_n)
        output_path = self._output_path(output_name or f"{group_by}_{value_column}_{metric}.png", ".png")

        plt.figure(figsize=(10, max(5, len(values) * 0.3)))
        sns.barplot(x=values.values, y=values.index.astype(str), color="#7c3aed")
        plt.xlabel(f"{metric}({value_column})")
        plt.ylabel(group_by)
        plt.title(f"{metric.title()} {value_column} by {group_by}")
        plt.tight_layout()
        plt.savefig(output_path, dpi=160)
        plt.close()
        return str(output_path)

    def plot_time_series(
        self,
        path: str,
        date_column: str,
        value_column: str,
        output_name: str | None = None,
        freq: str = "MS",
        max_rows: int = 200_000,
    ) -> str:
        series = DataTools(self.workspace)._time_series(path, date_column, value_column, freq, max_rows)
        output_path = self._output_path(output_name or f"{value_column}_time_series.png", ".png")

        plt.figure(figsize=(10, 5))
        sns.lineplot(x=series.index, y=series.values, marker="o", color="#2563eb")
        plt.xlabel(date_column)
        plt.ylabel(value_column)
        plt.title(f"{value_column} over time")
        plt.tight_layout()
        plt.savefig(output_path, dpi=160)
        plt.close()
        return str(output_path)

    def plot_interactive_scatter(
        self,
        path: str,
        x: str,
        y: str,
        color: str | None = None,
        output_name: str = "scatter.html",
        max_rows: int = 50_000,
    ) -> str:
        import plotly.express as px

        frame = DataTools(self.workspace).load_table(path, max_rows=max_rows)
        for column in [x, y, color]:
            if column and column not in frame.columns:
                raise ValueError(f"Column not found: {column}")
        output_path = self._output_path(output_name, ".html")
        figure = px.scatter(frame, x=x, y=y, color=color, title=f"{y} vs {x}")
        figure.write_html(output_path, include_plotlyjs="cdn")
        return str(output_path)

    def _output_path(self, output_name: str, suffix: str) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", output_name).strip("._")
        if not safe_name:
            safe_name = f"plot{suffix}"
        if not safe_name.lower().endswith(suffix):
            safe_name = f"{safe_name}{suffix}"
        output_dir = self.workspace / PLOTS_DIR
        output_dir.mkdir(exist_ok=True)
        return output_dir / safe_name
