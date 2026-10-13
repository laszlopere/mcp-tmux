"""Time the tmux command transport: local, loopback SSH, and optionally a real host.

Measures per-call latency of a bare ``capture-pane`` through ``TmuxRunner`` and
of a ``tmux_run`` round trip (``true``) through the MCP tool, so a transport
change can be compared against a saved baseline. Each target gets its own
throwaway session on a private ``-L mcp-tmux-bench`` socket, killed afterwards.

Run from the repo root:

    .venv/bin/python scripts/bench_transport.py --save baseline.json
    .venv/bin/python scripts/bench_transport.py --compare baseline.json
    .venv/bin/python scripts/bench_transport.py --host pipnode   # also a real host
    .venv/bin/python scripts/bench_transport.py --transport control
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from _loopback_sshd import loopback_sshd  # noqa: E402

from mcp_tmux.runner import TmuxRunner  # noqa: E402
from mcp_tmux.server import build_server  # noqa: E402

SOCKET = "mcp-tmux-bench"
SESSION = "bench"


def _stats(samples: list[float]) -> dict[str, float]:
    ms = sorted(s * 1000 for s in samples)
    return {
        "n": len(ms),
        "median_ms": round(statistics.median(ms), 2),
        "mean_ms": round(statistics.fmean(ms), 2),
        "p90_ms": round(ms[min(len(ms) - 1, int(len(ms) * 0.9))], 2),
        "min_ms": round(ms[0], 2),
    }


async def _timed(n: int, call) -> list[float]:
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        await call()
        samples.append(time.perf_counter() - t0)
    return samples


async def bench_target(config: dict[str, Any], target: str, n: int, runs: int) -> dict:
    runner = TmuxRunner(config)
    mcp = build_server(config=config)
    await runner.run_checked(["new-session", "-d", "-s", SESSION], target=target)
    try:
        # Warm-up: capability detection and the shell prompt coming up.
        await runner.capabilities(target)
        await mcp.call_tool(
            "tmux_run", {"target_pane": SESSION, "command": "true", "target": target}
        )

        async def capture():
            await runner.run_checked(["capture-pane", "-p", "-t", SESSION], target=target)

        async def run_true():
            res = await mcp.call_tool(
                "tmux_run",
                {"target_pane": SESSION, "command": "true", "timeout": 30, "target": target},
            )
            data = json.loads(res.content[0].text)
            if not data.get("completed"):
                raise RuntimeError(f"tmux_run did not complete on {target}: {data}")

        return {
            "capture_pane": _stats(await _timed(n, capture)),
            "tmux_run": _stats(await _timed(runs, run_true)),
        }
    finally:
        await runner.run(["kill-server"], target=target)


def _git_describe() -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO), "describe", "--always", "--dirty"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or "unknown"


def _print_table(results: dict[str, dict], baseline: dict[str, dict] | None) -> None:
    head = f"{'target':<10} {'benchmark':<13} {'median':>9} {'mean':>9} {'p90':>9} {'min':>9}"
    if baseline is not None:
        head += f" {'base med':>9} {'speedup':>8}"
    print(head)
    print("-" * len(head))
    for target, benches in results.items():
        for name, s in benches.items():
            row = (
                f"{target:<10} {name:<13} {s['median_ms']:>7.1f}ms {s['mean_ms']:>7.1f}ms "
                f"{s['p90_ms']:>7.1f}ms {s['min_ms']:>7.1f}ms"
            )
            if baseline is not None:
                b = baseline.get(target, {}).get(name)
                if b:
                    speedup = b["median_ms"] / s["median_ms"] if s["median_ms"] else 0.0
                    row += f" {b['median_ms']:>7.1f}ms {speedup:>7.2f}x"
            print(row)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-n", type=int, default=50, help="capture-pane calls per target")
    ap.add_argument("--runs", type=int, default=10, help="tmux_run round trips per target")
    ap.add_argument("--host", help="also time this real ssh destination or ~/.ssh alias")
    ap.add_argument("--no-loopback", action="store_true", help="skip the loopback sshd")
    ap.add_argument(
        "--transport", choices=["oneshot", "control"], default="oneshot", help="command transport"
    )
    ap.add_argument("--save", type=Path, help="write results as JSON to this file")
    ap.add_argument("--compare", type=Path, help="show speedup against a saved JSON file")
    args = ap.parse_args()

    baseline = None
    if args.compare:
        baseline = json.loads(args.compare.read_text())["results"]

    config: dict[str, Any] = {
        "defaults": {"socket_name": SOCKET, "timeout": 30, "transport": args.transport},
        "targets": {},
        "toolsets": ["all"],
    }
    targets = ["local"]
    if args.host:
        config["targets"]["host"] = {"host": args.host}
        targets.append("host")

    results: dict[str, dict] = {}
    with contextlib.ExitStack() as stack:
        if not args.no_loopback:
            d = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="bench-ssh-")))
            lb = stack.enter_context(loopback_sshd(d))
            config["targets"]["loopback"] = lb.target_spec()
            targets.insert(1, "loopback")
        for target in targets:
            print(f"timing {target} ...", file=sys.stderr)
            results[target] = await bench_target(config, target, args.n, args.runs)

    _print_table(results, baseline)

    if args.save:
        meta = {
            "commit": _git_describe(),
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "machine": platform.node(),
            "tmux": (await TmuxRunner(config).capabilities()).raw,
            "n": args.n,
            "runs": args.runs,
            "host": args.host,
            "transport": args.transport,
        }
        args.save.write_text(json.dumps({"meta": meta, "results": results}, indent=2) + "\n")
        print(f"saved {args.save}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
