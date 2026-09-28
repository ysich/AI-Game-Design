from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import to_dict
from .pipeline import PlannerPipeline
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 策划案管线")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="运行一次本地策划案流程")
    run_parser.add_argument("request", help="自然语言需求")
    run_parser.add_argument("--root", default=".")
    serve_parser = sub.add_parser("serve", help="启动本地工作台")
    serve_parser.add_argument("--root", default=".")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    if args.command == "run":
        run = PlannerPipeline(Path(args.root)).run(args.request)
        print(json.dumps(to_dict(run), ensure_ascii=False, indent=2))
    else:
        serve(Path(args.root), args.host, args.port)


if __name__ == "__main__":
    main()
