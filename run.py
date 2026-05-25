#!/usr/bin/env python3
from zsiga_web import create_app

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=58176)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--repo", default="/home/zsiga/repo")
    args = parser.parse_args()
    app = create_app(args.repo)
    app.run(host=args.host, port=args.port)
