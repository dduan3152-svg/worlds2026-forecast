"""Serve only the dashboard on loopback; optionally follow the private remote data."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import base64
import json
import shutil
import subprocess
import threading

ROOT=Path(__file__).resolve().parents[1]
REMOTE_DATA=None


def follow_remote(executable):
    global REMOTE_DATA
    # Authentication remains in GitHub CLI's credential store, never in the browser.
    while True:
        try:
            result=subprocess.run([executable,'api','repos/dduan3152-svg/worlds2026-forecast/contents/site/data.json'],
                                  capture_output=True,check=True,timeout=30)
            raw=base64.b64decode(json.loads(result.stdout)['content'])
            payload=json.loads(raw)
            if payload.get('schema')!=1 or not payload.get('history'):raise ValueError('Invalid site data')
            REMOTE_DATA=raw
        except (ValueError,KeyError,subprocess.SubprocessError):
            print('Remote refresh unavailable; keeping last valid dashboard data.',flush=True)
        threading.Event().wait(60)


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        payload=REMOTE_DATA
        if self.path.split('?',1)[0]=='/data.json' and payload is not None:
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        super().end_headers()

    def list_directory(self,path):
        self.send_error(404);return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--follow',action='store_true',help='Read private GitHub dashboard data every minute using gh')
    parser.add_argument('--gh',default='gh',help='GitHub CLI executable (optional)')
    args=parser.parse_args()
    if args.follow:
        executable=shutil.which(args.gh)
        if not executable:parser.error('GitHub CLI not found; provide --gh or omit --follow')
        threading.Thread(target=follow_remote,args=(executable,),daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(Handler,directory=str(ROOT/'site')))
    print(f'Private local preview: http://127.0.0.1:{args.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:server.server_close()

if __name__=='__main__':main()
