#!/usr/bin/env python3

from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import urllib.parse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture a single OAuth callback request and store it as JSON.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--path", default="/callback")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    callback_path = args.path or "/"
    result: dict[str, object] = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            if parsed.path != callback_path:
                self.send_response(404)
                self.end_headers()
                return

            result.update(
                {
                    "path": parsed.path,
                    "query": {key: values[0] if len(values) == 1 else values for key, values in params.items()},
                    "first_line": self.requestline,
                }
            )
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            body = "OAuth callback captured."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, _format: str, *_args: object) -> None:
            return

    with socketserver.TCPServer((args.host, args.port), CallbackHandler) as server:
        server.timeout = args.timeout_seconds
        server.handle_request()

    if not result:
        raise SystemExit(f"Timed out waiting for callback on http://{args.host}:{args.port}{callback_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
