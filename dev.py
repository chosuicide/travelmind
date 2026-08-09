import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_ROOT = PROJECT_ROOT / "frontend"


def ensure_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(
                f"端口 {port} 已被占用，请先关闭旧的开发服务"
            ) from exc


def stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()

    deadline = time.monotonic() + 5
    for process in processes:
        if process.poll() is not None:
            continue
        timeout = max(0, deadline - time.monotonic())
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()


# === 模块：TravelMind 一键开发启动器 ===
# 流程：检查端口/依赖 → 启动 API → 启动 Worker → 启动 Vite → 统一退出
def main() -> int:
    ensure_port_available(8000)
    ensure_port_available(5173)

    node = shutil.which("node")
    vite = FRONTEND_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
    if node is None:
        raise RuntimeError("没有找到 Node.js，请先安装前端运行环境")
    if not vite.exists():
        raise RuntimeError("前端依赖尚未安装，请先在 frontend 目录执行 pnpm install")

    commands = [
        (
            "API",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            PROJECT_ROOT,
        ),
        (
            "Worker",
            [sys.executable, "-m", "app.generation.worker"],
            PROJECT_ROOT,
        ),
        (
            "Frontend",
            [node, str(vite), "--host", "127.0.0.1", "--port", "5173"],
            FRONTEND_ROOT,
        ),
    ]

    processes: list[subprocess.Popen] = []
    try:
        for name, command, cwd in commands:
            process = subprocess.Popen(command, cwd=cwd)
            processes.append(process)
            print(f"[TravelMind] {name} 已启动，PID={process.pid}")

        print("[TravelMind] 前端：http://127.0.0.1:5173")
        print("[TravelMind] API：http://127.0.0.1:8000/docs")
        print("[TravelMind] 按 Ctrl+C 同时停止三个服务")

        while True:
            for (name, _, _), process in zip(commands, processes):
                return_code = process.poll()
                if return_code is not None:
                    raise RuntimeError(
                        f"{name} 意外退出，退出码 {return_code}"
                    )
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[TravelMind] 正在停止开发服务…")
        return 0
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"[TravelMind] 启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
