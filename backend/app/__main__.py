import sys

from .core import Settings, migrate

if __name__ == "__main__":
    if sys.argv[1:] == ["migrate"]:
        migrate(Settings())
        print("Database migrations applied.")
    elif sys.argv[1:] == ["worker"]:
        from .jobs import run_worker

        run_worker(Settings())
    elif len(sys.argv) == 4 and sys.argv[1] == "desktop":
        from .desktop import serve_desktop

        serve_desktop(Settings(), int(sys.argv[2]), sys.argv[3])
    else:
        raise SystemExit("Usage: python -m backend.app migrate|worker|desktop PORT INSTANCE")
