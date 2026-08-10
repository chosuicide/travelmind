import os
import signal
import subprocess
import sys
import time

from app.auth.demo import ensure_demo_user
from app.core.config import LANGGRAPH_CHECKPOINT_PATH
from bootstrap_db import main as migrate_database


def stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()

    deadline = time.monotonic() + 10
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()


# === 生产启动器：一个容器同时托管网页、API 和后台生成任务 ===
# 流程：迁移数据库 → 准备演示账号 → 启动 API/Worker → 统一处理退出信号
def main() -> int:
    migrate_database()
    LANGGRAPH_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    created = ensure_demo_user()
    if created:
        print("[TravelMind] Demo account created")

    port = int(os.getenv("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")

    commands = {
        "API": [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--proxy-headers",
            "--forwarded-allow-ips=*",
        ],
        "Worker": [sys.executable, "-m", "app.generation.worker"],
    }
    processes: list[subprocess.Popen] = []

    def request_shutdown(signum, frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    try:
        for name, command in commands.items():
            process = subprocess.Popen(command)
            processes.append(process)
            print(f"[TravelMind] {name} started, PID={process.pid}")

        while True:
            for name, process in zip(commands, processes):
                return_code = process.poll()
                if return_code is not None:
                    raise RuntimeError(
                        f"{name} exited unexpectedly with code {return_code}"
                    )
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"[TravelMind] Startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
