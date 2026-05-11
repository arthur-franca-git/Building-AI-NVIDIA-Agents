from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import AgentConfig


WorkflowMode = str | None
TaskProfile = str | None


@dataclass(frozen=True)
class WorkflowOptions:
    mode: WorkflowMode = None
    profile: TaskProfile = None
    target_tests: tuple[str, ...] = ()
    task_log: str | None = None


def build_workflow_prompt(prompt: str, options: WorkflowOptions) -> str:
    sections = [f"User task:\n{prompt.strip()}"]

    if options.mode == "task-flow":
        sections.append(
            """
Run this as a structured coding task:
1. Use project_summary first.
2. Inspect relevant files with search_files and read_file.
3. State a short plan before changing files.
4. Prefer apply_patch for existing files and write_file only for new files.
5. After edits, run exactly: py -m pytest
6. Then run exactly: py -m ruff check src tests
7. Finish with: changed files, verification results, and remaining risks.
"""
        )
    elif options.mode == "review-only":
        sections.append(
            """
Run this as review-only:
- Do not edit files.
- Do not call apply_patch or write_file.
- Use project_summary, search_files, read_file, and git_diff when useful.
- Prioritize bugs, regressions, security issues, missing tests, and risky design.
- Lead with findings ordered by severity. If no findings, say that clearly.
"""
        )
    elif options.mode == "dry-run-plan":
        sections.append(
            """
Run this as a dry-run implementation plan:
- Do not persist edits.
- Inspect relevant files before proposing changes when the task references existing files,
  code, logs, schemas, or a workspace project.
- If the task is generative and does not require existing files, answer directly without tool calls.
- Use apply_patch to produce dry-run diffs only for concrete file changes.
- Do not claim any file was changed.
- Finish with the proposed patches, verification commands to run, and risks.
"""
        )

    if options.profile == "sql":
        sections.append(
            """
Apply the SQL profile:
- Identify the SQL dialect if possible: BigQuery, Trino, Postgres, MySQL, SQL Server, or unknown.
- Prefer readable CTEs, explicit join keys, clear date boundaries, and stable aliases.
- Watch for duplicated rows after joins, null handling, timezone issues, and unsafe filters.
- When reviewing SQL, call out grain, expected output columns, and likely performance risks.
- Do not invent table schemas; inspect available files or ask for schema if needed.
- If the user asks for a generic SQL skeleton and gives no real schema, provide a clearly labeled
  template with placeholder table and column names instead of searching the workspace.
"""
        )
    elif options.profile == "python-ml":
        sections.append(
            """
Apply the Python/ML profile:
- Inspect data assumptions, target definition, feature leakage, train/test split, and metrics.
- Prefer reproducible pipelines, fixed random seeds, small functions, and explicit validation.
- For forecasting, check time ordering, horizon, backtest design, seasonality, and leakage.
- For pandas code, watch for chained assignment, dtype surprises, nulls, and row explosion.
- Use profile_table before predictive_model, correlations, target_analysis, or forecast tools.
- Compare forecasts against forecast_baseline before trusting ARIMA or Prophet.
- Summarize model limitations and what evidence would improve confidence.
"""
        )
    elif options.profile == "java":
        sections.append(
            """
Apply the Java profile:
- Inspect class responsibilities, method contracts, null handling, exceptions, and thread safety.
- Prefer small changes, readable names, and tests around public behavior.
- Watch for resource leaks, mutable shared state, bad equals/hashCode, and collection edge cases.
- Respect existing build tools and style conventions.
"""
        )
    elif options.profile == "debug":
        sections.append(
            """
Apply the debug profile:
- Reproduce or localize the failure before proposing a fix when possible.
- Inspect stack traces, logs, failing inputs, and recent changes.
- State the likely root cause, not just the symptom.
- Prefer the smallest fix that addresses the cause, then verify with the narrowest relevant test.
"""
        )
    elif options.profile == "analysis":
        sections.append(
            """
Apply the analysis profile:
- Clarify the metric, grain, population, filters, and time window before drawing conclusions.
- Separate facts, assumptions, and hypotheses.
- Prefer auditable calculations and intermediate checks.
- Use profile_table, missing_report, group_summary, correlations, outlier_report, and target_analysis
  when the user provides a table file.
- Call out sample-size issues, missing data, confounders, and next-best validation steps.
"""
        )

    if options.target_tests:
        test_lines = "\n".join(f"- {command}" for command in options.target_tests)
        sections.append(
            f"""
Target verification commands requested by the user:
{test_lines}
Run these exact commands only if they are allowlisted. If a command is not allowlisted,
state that it was not run and explain which allowed command is closest.
"""
        )

    return "\n\n".join(section.strip() for section in sections if section.strip())


def append_task_log(
    workspace: Path,
    log_path: str,
    prompt: str,
    output: str,
    config: AgentConfig,
    options: WorkflowOptions,
) -> Path:
    target = (workspace / log_path).resolve()
    workspace = workspace.resolve()
    if target != workspace and workspace not in target.parents:
        raise ValueError(f"Task log path is outside workspace: {log_path}")

    target.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    mode = options.mode or "default"
    profile = options.profile or "default"
    target_tests = ", ".join(options.target_tests) or "(none)"
    entry = f"""
## {timestamp}

- mode: {mode}
- profile: {profile}
- workspace: {config.workspace}
- model: {config.model}
- backend: {config.backend}
- dry_run: {config.dry_run}
- target_tests: {target_tests}

### Prompt

```text
{prompt.strip()}
```

### Output

```text
{output.strip()}
```

"""
    with target.open("a", encoding="utf-8") as file:
        file.write(entry.lstrip())
    return target


def resolve_mode(task_flow: bool, review_only: bool, dry_run_plan: bool) -> WorkflowMode:
    enabled = [
        ("task-flow", task_flow),
        ("review-only", review_only),
        ("dry-run-plan", dry_run_plan),
    ]
    selected = [mode for mode, is_enabled in enabled if is_enabled]
    if len(selected) > 1:
        raise ValueError("Choose only one workflow mode: --task-flow, --review-only, or --dry-run-plan")
    return selected[0] if selected else None


def resolve_profile(
    sql_mode: bool,
    python_ml: bool,
    java_mode: bool,
    debug: bool,
    analysis: bool,
) -> TaskProfile:
    enabled = [
        ("sql", sql_mode),
        ("python-ml", python_ml),
        ("java", java_mode),
        ("debug", debug),
        ("analysis", analysis),
    ]
    selected = [profile for profile, is_enabled in enabled if is_enabled]
    if len(selected) > 1:
        raise ValueError(
            "Choose only one task profile: --sql-mode, --python-ml, --java-mode, --debug, or --analysis"
        )
    return selected[0] if selected else None
