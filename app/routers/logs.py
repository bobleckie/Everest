"""Live log tail viewer.

Exposes three safe endpoints:
  GET /api/logs            -> list of known logs with sizes & mtimes
  GET /api/logs/{name}     -> returns last N lines (default 200) as JSON
  GET /api/logs/{name}/stream -> Server-Sent Events stream of new lines
  GET /api/logs/viewer     -> minimal HTML page that tails any log live

Only files inside the workspace `logs/` directory are readable. The `name`
is matched against a whitelist of currently-present files (and any files
matching *.log in that directory) to avoid path traversal.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

router = APIRouter()

# Env-overridable via EVEREST_LOGS_DIR; default <workspace>/logs.
from .. import paths as _paths
_LOGS_DIR = _paths.logs_dir()


def _list_log_files() -> List[Path]:
    if not _LOGS_DIR.exists():
        return []
    return sorted(
        [p for p in _LOGS_DIR.iterdir() if p.is_file() and p.suffix == ".log"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _resolve_log(name: str) -> Path:
    """Resolve a requested log file name safely within _LOGS_DIR."""
    # Reject anything that tries to escape the logs dir
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid log name")
    candidate = (_LOGS_DIR / name).resolve()
    try:
        candidate.relative_to(_LOGS_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal rejected")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"Log not found: {name}")
    return candidate


@router.get("")
def list_logs():
    """List known log files with size and last-modified timestamp."""
    result = []
    for p in _list_log_files():
        st = p.stat()
        result.append({
            "name": p.name,
            "size_bytes": st.st_size,
            "mtime": st.st_mtime,
            "mtime_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        })
    return {"logs_dir": str(_LOGS_DIR), "files": result}


@router.get("/viewer", response_class=HTMLResponse)
def viewer():
    """Tiny standalone HTML page that tails a log live via SSE."""
    return HTMLResponse(_VIEWER_HTML)


@router.get("/{name}")
def tail_log(name: str, lines: int = Query(200, ge=1, le=10000)):
    """Return the last N lines of the named log file."""
    path = _resolve_log(name)
    # Efficient tail: read the whole file only if small; else seek from end
    size = path.stat().st_size
    approx_bytes = max(lines * 400, 16_384)  # ~400 bytes per line average
    with path.open("rb") as f:
        if size > approx_bytes:
            f.seek(-approx_bytes, os.SEEK_END)
            f.readline()  # discard likely-partial first line
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    tail = text.splitlines()[-lines:]
    return {
        "name": name,
        "size_bytes": size,
        "lines": tail,
        "returned": len(tail),
    }


@router.get("/{name}/stream")
async def stream_log(name: str, from_end_lines: int = Query(50, ge=0, le=5000)):
    """Server-Sent Events stream that pushes appended lines as they arrive."""
    path = _resolve_log(name)

    async def event_gen():
        # Seed with the current tail so the viewer shows recent context
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            approx = min(end, max(from_end_lines * 400, 8_192))
            f.seek(end - approx)
            if approx < end:
                f.readline()
            seed = f.read().decode("utf-8", errors="replace").splitlines()
            pos = f.tell()
        for line in seed[-from_end_lines:]:
            yield f"data: {line}\n\n"

        # Now follow the file
        while True:
            await asyncio.sleep(1.0)
            try:
                new_size = path.stat().st_size
            except FileNotFoundError:
                yield "event: error\ndata: file disappeared\n\n"
                return
            if new_size < pos:
                # File was truncated/rotated — reset
                pos = 0
            if new_size > pos:
                with path.open("rb") as f:
                    f.seek(pos)
                    chunk = f.read().decode("utf-8", errors="replace")
                    pos = f.tell()
                for line in chunk.splitlines():
                    # SSE payloads must not contain raw newlines inside a data field
                    yield f"data: {line}\n\n"
            else:
                # Heartbeat every ~15s so proxies don't close the connection
                yield ": keep-alive\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


_VIEWER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Everest · Log Tail Viewer</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: ui-monospace, Consolas, "Cascadia Code", monospace;
         background:#0d1117; color:#e6edf3; }
  header { padding: 10px 16px; background:#161b22; border-bottom:1px solid #30363d;
           display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  header h1 { font-size: 14px; margin:0; font-weight: 600; letter-spacing:.3px; }
  select, button { background:#21262d; color:#e6edf3; border:1px solid #30363d;
                   padding: 6px 10px; border-radius:6px; font-family:inherit; font-size:12px; }
  button.primary { background:#238636; border-color:#2ea043; }
  button.primary:hover { background:#2ea043; }
  .meta { margin-left:auto; font-size:11px; color:#8b949e; }
  .status-dot { display:inline-block; width:8px; height:8px; border-radius:50%;
                background:#8b949e; margin-right:6px; vertical-align:middle; }
  .status-dot.live { background:#3fb950; box-shadow:0 0 6px #3fb950; }
  .status-dot.err  { background:#f85149; }
  #log { padding: 8px 16px; white-space: pre-wrap; word-break: break-word;
         font-size: 12px; line-height: 1.45; height: calc(100vh - 52px); overflow:auto; }
  .line-INFO     { color:#e6edf3; }
  .line-WARN,
  .line-WARNING  { color:#e3b341; }
  .line-ERROR    { color:#f85149; }
  .line-DEBUG    { color:#8b949e; }
  .line-DONE     { color:#3fb950; font-weight:600; }
  .line-HEADER   { color:#58a6ff; }
</style>
</head>
<body>
<header>
  <h1>Everest · Log Tail</h1>
  <select id="file"></select>
  <button id="reload">Reload list</button>
  <label style="font-size:12px;"><input type="checkbox" id="autoscroll" checked/> auto-scroll</label>
  <button id="clear">Clear</button>
  <span class="meta"><span class="status-dot" id="dot"></span><span id="status">disconnected</span></span>
</header>
<div id="log"></div>
<script>
(function(){
  const fileSel = document.getElementById('file');
  const logEl   = document.getElementById('log');
  const dot     = document.getElementById('dot');
  const statusEl= document.getElementById('status');
  const autoCB  = document.getElementById('autoscroll');
  let es = null;

  function setStatus(kind, text){
    dot.className = 'status-dot ' + (kind || '');
    statusEl.textContent = text;
  }

  function classify(line){
    if (/===\s*DONE/.test(line)) return 'DONE';
    if (/^===/.test(line))       return 'HEADER';
    if (/\sERROR\b/.test(line))  return 'ERROR';
    if (/\sWARN(ING)?\b/.test(line)) return 'WARN';
    if (/\sDEBUG\b/.test(line))  return 'DEBUG';
    if (/\sINFO\b/.test(line))   return 'INFO';
    return '';
  }

  function append(line){
    const div = document.createElement('div');
    const cls = classify(line);
    if (cls) div.className = 'line-' + cls;
    div.textContent = line;
    logEl.appendChild(div);
    // Trim to last ~5000 lines to keep memory bounded
    while (logEl.childElementCount > 5000) logEl.removeChild(logEl.firstChild);
    if (autoCB.checked) logEl.scrollTop = logEl.scrollHeight;
  }

  async function loadFileList(){
    const r = await fetch('/api/logs');
    const j = await r.json();
    const selected = fileSel.value;
    fileSel.innerHTML = '';
    for (const f of j.files){
      const kb = (f.size_bytes/1024).toFixed(1);
      const opt = document.createElement('option');
      opt.value = f.name;
      opt.textContent = `${f.name}  (${kb} KB · ${f.mtime_iso})`;
      fileSel.appendChild(opt);
    }
    if (selected && [...fileSel.options].some(o=>o.value===selected)) fileSel.value = selected;
  }

  function connect(name){
    if (es) { es.close(); es = null; }
    logEl.innerHTML = '';
    if (!name){ setStatus('err', 'no file'); return; }
    setStatus('', 'connecting…');
    es = new EventSource(`/api/logs/${encodeURIComponent(name)}/stream?from_end_lines=200`);
    es.onopen    = () => setStatus('live', 'live · ' + name);
    es.onerror   = () => setStatus('err', 'disconnected (will retry)');
    es.onmessage = (ev) => append(ev.data);
  }

  fileSel.addEventListener('change', () => connect(fileSel.value));
  document.getElementById('reload').addEventListener('click', async () => {
    await loadFileList();
    if (fileSel.value) connect(fileSel.value);
  });
  document.getElementById('clear').addEventListener('click', () => { logEl.innerHTML = ''; });

  (async () => {
    await loadFileList();
    if (fileSel.options.length) connect(fileSel.value);
  })();
})();
</script>
</body>
</html>
"""
