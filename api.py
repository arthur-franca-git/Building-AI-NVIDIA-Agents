from __future__ import annotations

import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import run_coding_agent
from .config import load_config
from .workflow import WorkflowOptions, append_task_log, build_workflow_prompt


PROFILE_ALIASES = {
    "default": None,
    "sql": "sql",
    "python-ml": "python-ml",
    "java": "java",
    "debug": "debug",
    "analysis": "analysis",
}
MODE_ALIASES = {
    "default": None,
    "task-flow": "task-flow",
    "review-only": "review-only",
    "dry-run-plan": "dry-run-plan",
}


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1)
    profile: str = "default"
    mode: str = "default"
    workspace: str | None = None
    write: bool = False
    target_tests: list[str] = Field(default_factory=list)
    task_log: str | None = None


class ContinueRequest(BaseModel):
    instruction: str | None = None


class ConfigSummary(BaseModel):
    model: str
    backend: str
    max_tokens: int
    request_timeout_seconds: int
    base_url: str | None
    workspace: str
    dry_run: bool
    profiles: list[str]
    modes: list[str]


class WorkspaceLoadRequest(BaseModel):
    workspace: str | None = None
    patterns: list[str] = Field(default_factory=list)
    max_files: int = 300
    profile_tables: bool = True


class LargeTableQueryRequest(BaseModel):
    workspace: str | None = None
    sql: str
    tables: dict[str, str]
    limit: int = 1000


class RunSummary(BaseModel):
    id: str
    status: str
    prompt: str
    profile: str
    mode: str
    created_at: str
    parent_id: str | None = None
    completed_at: str | None = None
    output: str | None = None
    error: str | None = None


@dataclass
class RunState:
    id: str
    request: ChatRequest
    parent_id: str | None = None
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    completed_at: str | None = None
    output: str | None = None
    error: str | None = None
    events: "queue.Queue[dict[str, Any] | None]" = field(default_factory=queue.Queue)


app = FastAPI(title="Coding Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RUNS: dict[str, RunState] = {}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config", response_model=ConfigSummary)
def get_config(workspace: str | None = None) -> ConfigSummary:
    config = load_config(workspace=workspace)
    return ConfigSummary(
        model=config.model,
        backend=config.backend,
        max_tokens=config.max_tokens,
        request_timeout_seconds=config.request_timeout_seconds,
        base_url=config.base_url,
        workspace=str(config.workspace),
        dry_run=config.dry_run,
        profiles=list(PROFILE_ALIASES),
        modes=list(MODE_ALIASES),
    )


@app.post("/api/runs", response_model=RunSummary)
def create_run(request: ChatRequest) -> RunSummary:
    return _start_run(request)


@app.post("/api/runs/{run_id}/continue", response_model=RunSummary)
def continue_run(run_id: str, request: ContinueRequest | None = None) -> RunSummary:
    parent = RUNS.get(run_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Run not found")
    if parent.status not in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="Run is still running")
    if not parent.output and not parent.error:
        raise HTTPException(status_code=400, detail="Run has no output to continue")

    previous_text = parent.output or f"Erro anterior: {parent.error}"
    extra = (request.instruction if request else None) or "Continue de onde parou, sem repetir o que ja foi respondido."
    prompt = (
        "Continue a execucao do run anterior.\n\n"
        "Regras:\n"
        "- Nao repita a resposta anterior.\n"
        "- Use o mesmo contexto, perfil e modo do run original.\n"
        "- Se precisar concluir passos pendentes, seja direto e indique verificacoes.\n\n"
        f"Tarefa original:\n{parent.request.prompt}\n\n"
        f"Instrucao adicional:\n{extra}\n\n"
        f"Ultima resposta/estado do agente:\n{_tail_text(previous_text)}"
    )
    continued = parent.request.model_copy(update={"prompt": prompt})
    return _start_run(continued, parent_id=parent.id)


def _start_run(request: ChatRequest, parent_id: str | None = None) -> RunSummary:
    if request.profile not in PROFILE_ALIASES:
        raise HTTPException(status_code=400, detail=f"Unsupported profile: {request.profile}")
    if request.mode not in MODE_ALIASES:
        raise HTTPException(status_code=400, detail=f"Unsupported mode: {request.mode}")

    run_id = uuid.uuid4().hex
    state = RunState(id=run_id, request=request, parent_id=parent_id)
    RUNS[run_id] = state
    threading.Thread(target=_execute_run, args=(state,), daemon=True).start()
    return _summary(state)


@app.get("/api/runs", response_model=list[RunSummary])
def list_runs() -> list[RunSummary]:
    return [_summary(state) for state in reversed(list(RUNS.values()))]


@app.get("/api/runs/{run_id}", response_model=RunSummary)
def get_run(run_id: str) -> RunSummary:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")
    return _summary(state)


@app.get("/api/runs/{run_id}/events")
def stream_run_events(run_id: str) -> StreamingResponse:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    def generate():
        yield _sse({"type": "snapshot", "run": _summary(state).model_dump()})
        while True:
            event = state.events.get()
            if event is None:
                yield _sse({"type": "done", "run": _summary(state).model_dump()})
                break
            yield _sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/files")
def list_files(workspace: str | None = None) -> dict[str, Any]:
    config = load_config(workspace=workspace, dry_run=True)
    root = config.workspace
    files: list[dict[str, Any]] = []
    ignored_dirs = {"node_modules", "dist", "build", "__pycache__", "logs"}
    for child in root.rglob("*"):
        if not child.is_file():
            continue
        relative = child.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if any(part in ignored_dirs or part.startswith("pytest-cache-files-") for part in relative.parts):
            continue
        files.append(
            {
                "path": str(relative),
                "size": child.stat().st_size,
                "modified": datetime.fromtimestamp(child.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
        if len(files) >= 300:
            break
    return {"workspace": str(root), "files": files}


@app.post("/api/workspace/load")
def load_workspace(request: WorkspaceLoadRequest) -> dict[str, Any]:
    config = load_config(workspace=request.workspace, dry_run=True)
    root = config.workspace
    patterns = [pattern.lower().strip() for pattern in request.patterns if pattern.strip()]
    files = _workspace_files(root, max_files=request.max_files, patterns=patterns)

    table_profiles: list[dict[str, Any]] = []
    if request.profile_tables:
        for item in files:
            if Path(item["path"]).suffix.lower() not in {".csv", ".xlsx", ".xls", ".parquet"}:
                continue
            table_profiles.append(_profile_table_light(root / item["path"], root))
            if len(table_profiles) >= 8:
                break

    return {
        "workspace": str(root),
        "files_count": len(files),
        "files": files[: request.max_files],
        "table_profiles": table_profiles,
    }


@app.get("/api/large-data/tables")
def discover_large_tables(workspace: str | None = None, pattern: str | None = None) -> dict[str, Any]:
    from .large_data_tools import LargeDataTools

    config = load_config(workspace=workspace, dry_run=True)
    return json.loads(LargeDataTools(config.workspace).discover_tables(pattern=pattern))


@app.get("/api/large-data/schema")
def large_table_schema(path: str, workspace: str | None = None) -> dict[str, Any]:
    from .large_data_tools import LargeDataTools

    config = load_config(workspace=workspace, dry_run=True)
    return json.loads(LargeDataTools(config.workspace).table_schema(path))


@app.post("/api/large-data/query")
def query_large_tables(request: LargeTableQueryRequest) -> dict[str, Any]:
    from .large_data_tools import LargeDataTools

    config = load_config(workspace=request.workspace, dry_run=True)
    return json.loads(
        LargeDataTools(config.workspace).query(
            sql=request.sql,
            tables=request.tables,
            limit=request.limit,
        )
    )


@app.get("/api/plots")
def list_plots(workspace: str | None = None) -> dict[str, Any]:
    config = load_config(workspace=workspace, dry_run=True)
    plots_dir = config.workspace / "plots"
    plots: list[dict[str, Any]] = []
    if plots_dir.exists():
        for child in plots_dir.iterdir():
            if child.is_file():
                plots.append(
                    {
                        "path": str(child.relative_to(config.workspace)),
                        "size": child.stat().st_size,
                        "modified": datetime.fromtimestamp(child.stat().st_mtime).isoformat(timespec="seconds"),
                    }
                )
    return {"workspace": str(config.workspace), "plots": plots}


@app.post("/api/upload")
async def upload_file(file: UploadFile, workspace: str | None = None) -> dict[str, str]:
    config = load_config(workspace=workspace, dry_run=True)
    uploads_dir = config.workspace / "uploads"
    uploads_dir.mkdir(exist_ok=True)
    target = uploads_dir / Path(file.filename or "upload.bin").name
    target.write_bytes(await file.read())
    return {"path": str(target.relative_to(config.workspace))}


def _execute_run(state: RunState) -> None:
    request = state.request
    try:
        _emit(state, "status", message="Configuring workspace and workflow")
        mode = MODE_ALIASES[request.mode]
        profile = PROFILE_ALIASES[request.profile]
        forced_dry_run = mode in {"review-only", "dry-run-plan"}
        config = load_config(workspace=request.workspace, dry_run=True if forced_dry_run else not request.write)
        options = WorkflowOptions(
            mode=mode,
            profile=profile,
            target_tests=tuple(request.target_tests),
            task_log=request.task_log,
        )
        final_prompt = build_workflow_prompt(request.prompt, options)
        state.status = "running"
        _emit(state, "status", message="Prompt prepared")
        _emit(state, "status", message="Running agent")
        output = run_coding_agent(final_prompt, config)
        state.output = output
        if request.task_log:
            _emit(state, "status", message="Writing task log")
            append_task_log(config.workspace, request.task_log, final_prompt, output, config, options)
        state.status = "completed"
        state.completed_at = datetime.now().isoformat(timespec="seconds")
        _emit(state, "output", output=output)
    except Exception as exc:  # pragma: no cover - surfaced through API
        state.status = "failed"
        state.error = str(exc)
        state.completed_at = datetime.now().isoformat(timespec="seconds")
        _emit(state, "error", error=str(exc))
    finally:
        state.events.put(None)


def _emit(state: RunState, event_type: str, **payload: Any) -> None:
    state.events.put(
        {
            "type": event_type,
            "run_id": state.id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **payload,
        }
    )


def _summary(state: RunState) -> RunSummary:
    return RunSummary(
        id=state.id,
        status=state.status,
        prompt=state.request.prompt,
        profile=state.request.profile,
        mode=state.request.mode,
        created_at=state.created_at,
        parent_id=state.parent_id,
        completed_at=state.completed_at,
        output=state.output,
        error=state.error,
    )


def _tail_text(text: str, limit: int = 24000) -> str:
    if len(text) <= limit:
        return text
    return "[trecho inicial omitido para preservar contexto]\n" + text[-limit:]


def _workspace_files(root: Path, max_files: int = 300, patterns: list[str] | None = None) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    ignored_dirs = {"node_modules", "dist", "build", "__pycache__", "logs"}
    normalized_patterns = patterns or []
    iterator = root.iterdir() if normalized_patterns else root.rglob("*")
    for child in iterator:
        if not child.is_file():
            continue
        relative = child.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if any(part in ignored_dirs or part.startswith("pytest-cache-files-") for part in relative.parts):
            continue
        relative_text = str(relative)
        if normalized_patterns and not any(pattern in relative_text.lower() for pattern in normalized_patterns):
            continue
        files.append(
            {
                "path": relative_text,
                "size": child.stat().st_size,
                "modified": datetime.fromtimestamp(child.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
        if len(files) >= max_files:
            break
    return files


def _profile_table_light(path: Path, root: Path) -> dict[str, Any]:
    import pandas as pd

    relative = str(path.relative_to(root))
    try:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            sample = pd.read_csv(path, nrows=5000, low_memory=False)
            rows = None
        elif suffix in {".xlsx", ".xls"}:
            sample = pd.read_excel(path, nrows=5000)
            rows = None
        elif suffix == ".parquet":
            sample = pd.read_parquet(path)
            rows = len(sample)
            sample = sample.head(5000)
        else:
            raise ValueError("Unsupported table")
        missing = sample.isna().sum().sort_values(ascending=False).head(8)
        return {
            "path": relative,
            "size": path.stat().st_size,
            "rows_estimated": rows,
            "sample_shape": [int(sample.shape[0]), int(sample.shape[1])],
            "columns": [str(column) for column in sample.columns[:20]],
            "dtype_counts": sample.dtypes.astype(str).value_counts().to_dict(),
            "missing_top": {str(key): int(value) for key, value in missing.items()},
        }
    except Exception as exc:  # pragma: no cover - surfaced through API
        return {"path": relative, "error": str(exc)}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
