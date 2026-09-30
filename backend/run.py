from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from aceye.config import write_default_config
from aceye.server import AceyeServer


def main() -> int:
    parser = argparse.ArgumentParser(description="ACEYE editable backend")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--host", default=os.environ.get("ACEYE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ACEYE_PORT", "8765")))
    parser.add_argument("--desktop", action="store_true", help="compatibility flag used by the native one-click launcher")
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    write_default_config(root)
    server = AceyeServer(root, args.host, args.port)
    print(f"ACEYE ready: http://{args.host}:{args.port}/#token={server.service.token}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
