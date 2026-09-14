"""Own only the new app's loopback server and its graceful idle shutdown."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    import uvicorn
    from app.server import create_app
    from app.storage import atomic_json, now
    data = ROOT / 'data'; data.mkdir(exist_ok=True)
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8060,
                                         access_log=False, timeout_graceful_shutdown=30))
    app.state.shutdown = lambda: setattr(server, 'should_exit', True)
    receipt = data / 'server.json'
    app.state.on_ready = lambda: atomic_json(receipt, {'pid': os.getpid(), 'root': str(ROOT),
        'url': 'http://127.0.0.1:8060', 'started_at': now()})
    server.run()

if __name__ == '__main__': main()
