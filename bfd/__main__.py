"""CLI entry point.

Usage:
    uv run python -m bfd refresh-bfs   # fetch + persist BFS list
    uv run python -m bfd serve         # run the web app on localhost:8765
"""
import argparse
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser(prog="bfd")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh-bfs", help="Fetch and persist the BFS map list")
    serve = sub.add_parser("serve", help="Run the FastAPI app")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--host", default="127.0.0.1")

    args = parser.parse_args()

    if args.cmd == "refresh-bfs":
        from . import bfs
        stats = asyncio.run(bfs.refresh())
        print(f"BFS refresh: {stats}")
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("bfd.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
