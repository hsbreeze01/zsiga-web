#!/usr/bin/env python3
from zsiga_web import create_app

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=58176)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--repo", default="/home/zsiga/repo", help="Path to zsiga repo root")
    parser.add_argument("--daemon-url", default="http://localhost:58175", help="zsiga daemon API URL")
    args = parser.parse_args()
    app = create_app(args.repo, args.daemon_url)
    app.run(host=args.host, port=args.port)
