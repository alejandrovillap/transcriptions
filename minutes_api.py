"""Web API for minutes (minutes.py): start / cancel / list, with live progress over the hub.

Kept out of server.py so upstream merges stay easy; server.py only does
`app.include_router(minutes_api.router)` and sets `minutes_api.hub`.

WebSocket messages: {"type": "minutes", project, sid, v, status, steps, source, started, error, record}
"""

import asyncio
import time

from fastapi import APIRouter, HTTPException

import claude_cli
import minutes
import sessions as S

router = APIRouter()
hub = None                       # server.Hub, set by server.py
STREAM_LIMIT = 64 * 1024 * 1024
jobs: dict[tuple[str, str], dict] = {}
procs: dict[tuple[str, str], asyncio.subprocess.Process] = {}


def public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "spec"}


def broadcast(job: dict):
    if hub is not None:
        hub.emit({"type": "minutes", **public(job)})


def step(job: dict, text: str):
    job["steps"].append({"t": round(time.time() - job["started"], 1), "text": text})
    job["steps"] = job["steps"][-12:]
    broadcast(job)


async def _run(job: dict):
    spec = job["spec"]
    key = (job["project"], job["sid"])
    result: dict = {}
    err_tail = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *minutes.claude_command(spec), cwd=str(spec["cwd"]), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT)
        procs[key] = proc
        proc.stdin.write(spec["prompt"].encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        async for raw in proc.stdout:
            ev = claude_cli.parse_line(raw)
            if not ev:
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                step(job, f"Claude ({ev.get('model', '')}) started")
            for name, inp in claude_cli.tool_uses(ev):
                step(job, claude_cli.describe_tool(name, inp, spec["labels"]))
            if ev.get("type") == "result":
                result = ev
        err_tail = (await proc.stderr.read()).decode("utf-8", errors="replace")[-600:]
        await proc.wait()
        ok = proc.returncode == 0 and not result.get("is_error")
    except Exception as e:
        ok, err_tail = False, str(e)
    procs.pop(key, None)
    if job.get("cancelled"):
        status, error = "cancelled", "Cancelled"
    elif ok:
        status, error = "done", ""
    else:
        status, error = "failed", (result.get("result") or err_tail or "Minutes failed").strip()[-600:]
    rec = await asyncio.to_thread(
        minutes.finish, job["project"], job["sid"], job["v"], status,
        duration=round(time.time() - job["started"]), summary=(result.get("result") or "")[:500],
        cost=result.get("total_cost_usd"), **({"error": error} if error else {}))
    job.update(status=rec["status"], error=rec.get("error", ""), finished=time.time(), record=rec)
    step(job, {"done": "Finished", "failed": "Failed", "cancelled": "Cancelled"}[rec["status"]])


@router.get("/api/projects/{project}/sessions/{sid}/minutes")
async def minutes_list(project: str, sid: str):
    try:
        data = await asyncio.to_thread(minutes.public_manifest, project, sid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    job = jobs.get((project, sid))
    return {**data, "job": public(job) if job else None}


@router.post("/api/projects/{project}/sessions/{sid}/minutes")
async def minutes_start(project: str, sid: str):
    key = (project, sid)
    if jobs.get(key, {}).get("status") == "running":
        raise HTTPException(409, "Minutes are already being written for this session")
    if not claude_cli.claude_binary():
        raise HTTPException(400, "Claude Code CLI ('claude') not found")
    try:
        spec = await asyncio.to_thread(minutes.prepare, project, sid)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))
    job = {"project": project, "sid": sid, "v": spec["v"], "status": "running", "started": time.time(),
           "steps": [], "source": spec["source"], "lines": spec["lines"], "error": "", "spec": spec}
    jobs[key] = job
    step(job, f"Writing minutes m{spec['v']} from the {spec['source']}")
    asyncio.get_running_loop().create_task(_run(job))
    return public(job)


@router.delete("/api/projects/{project}/sessions/{sid}/minutes")
async def minutes_cancel(project: str, sid: str):
    job = jobs.get((project, sid))
    if job and job["status"] == "running":
        job["cancelled"] = True
    proc = procs.get((project, sid))
    if proc and proc.returncode is None:
        proc.terminate()
    return {"ok": True}


def stop_all():
    for p in list(procs.values()):
        if p.returncode is None:
            p.terminate()
