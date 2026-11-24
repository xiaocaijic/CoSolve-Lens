import os
import secrets
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout, suppress

import uvicorn
from fastapi import Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .core import ROOT, Problem, migrate
from .main import create_app


def serve_desktop(settings, port, instance):
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise RuntimeError("Desktop frontend must be built before starting.")

    runtime = ROOT / "var" / "desktop"
    runtime.mkdir(parents=True, exist_ok=True)
    stop_key_path = runtime / "stop.key"
    stop_key = secrets.token_urlsafe(32)
    os.environ["AI_COLLAB_WEB_ORIGIN"] = f"http://127.0.0.1:{port}"
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False))

    @app.get("/api/desktop-instance", include_in_schema=False)
    def desktop_instance():
        return PlainTextResponse(instance)

    @app.post("/api/desktop-shutdown", include_in_schema=False)
    def desktop_shutdown(request: Request):
        provided = request.headers.get("x-desktop-token", "")
        if not secrets.compare_digest(provided.encode(), stop_key.encode()):
            raise Problem(403, "DESKTOP_SHUTDOWN_DENIED", "本地启动器校验失败。")
        server.should_exit = True
        return Response(status_code=204)

    app.mount("/", StaticFiles(directory=dist, html=True), name="desktop-frontend")
    with (
        (runtime / "service.log").open("a", encoding="utf-8", buffering=1) as output,
        redirect_stdout(output),
        redirect_stderr(output),
    ):
        migrate(settings)
        stop_key_path.write_text(stop_key, encoding="ascii")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        worker = subprocess.Popen(
            [sys.executable, "-m", "backend.app", "worker"],
            cwd=ROOT, stdout=output, stderr=output, creationflags=creation_flags,
        )
        app.state.desktop_worker = worker
        try:
            server.run()
        finally:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait()
            with suppress(FileNotFoundError):
                stop_key_path.unlink()
