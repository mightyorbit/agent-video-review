#!/usr/bin/env python3
"""Local video review for agents. Python standard library only."""
import argparse
import json
import mimetypes
import math
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MAX_BODY = 65536
STATUSES = {"open", "addressed", "resolved", "wontfix"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_path(project):
    return project / "review.json"


def load(project):
    path = state_path(project)
    if not path.is_file():
        raise ValueError("Project is not initialized. Run: python3 review.py init PROJECT")
    return json.loads(path.read_text(encoding="utf-8"))


def save(project, state):
    path = state_path(project)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def initialize(project, title):
    project.mkdir(parents=True, exist_ok=True)
    if state_path(project).exists():
        raise ValueError("Project already exists")
    (project / "media").mkdir()
    save(project, {"schema": 1, "title": title, "versions": [], "comments": []})


def add(project, source, note):
    state = load(project)
    if not source.is_file() or source.suffix.lower() not in {".mp4", ".webm", ".mov"}:
        raise ValueError("Use an existing .mp4, .webm or .mov file")
    number = len(state["versions"]) + 1
    dest = project / "media" / ("v%d%s" % (number, source.suffix.lower()))
    if dest.exists():
        raise ValueError("Destination exists")
    shutil.copyfile(source, dest)
    state["versions"].append({"number": number, "file": dest.name, "note": note, "created": now()})
    save(project, state)
    return number


def comment(project, version, start, end, body):
    state = load(project)
    if version < 1 or version > len(state["versions"]):
        raise ValueError("Unknown version")
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
        raise ValueError("Invalid time range")
    if not body.strip() or len(body) > 2000:
        raise ValueError("Comment must have 1 to 2000 characters")
    item = {"id": "c-" + uuid.uuid4().hex[:8], "version": version, "start": start,
            "end": end, "text": body.strip(), "status": "open", "note": "", "created": now()}
    state["comments"].append(item)
    save(project, state)
    return item


def set_status(project, comment_id, status, note):
    if status not in STATUSES:
        raise ValueError("Invalid status")
    state = load(project)
    for item in state["comments"]:
        if item["id"] == comment_id:
            item["status"] = status
            item["note"] = note[:2000]
            item["updated"] = now()
            save(project, state)
            return item
    raise ValueError("Unknown comment ID")


def render(source, edl, output):
    if not shutil.which("ffmpeg"):
        raise ValueError("FFmpeg is required for render")
    if not source.is_file() or output.exists() or source.resolve() == output.resolve():
        raise ValueError("Source must exist; output must be a new path")
    data = json.loads(edl.read_text(encoding="utf-8"))
    keep = data.get("keep")
    if not isinstance(keep, list) or not keep:
        raise ValueError("EDL needs a nonempty keep array")
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    filters = []
    for i, pair in enumerate(keep):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("Each keep range must be [start,end]")
        start, end = pair
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pair) or not all(math.isfinite(v) for v in pair) or start < 0 or end <= start:
            raise ValueError("Invalid keep range")
        if i and start < keep[i-1][1]:
            raise ValueError("Keep ranges must be ordered and nonoverlapping")
        filters.append("[0:v]trim=start=%s:end=%s,setpts=PTS-STARTPTS[v%d]" % (start, end, i))
        filters.append("[0:a]atrim=start=%s:end=%s,asetpts=PTS-STARTPTS[a%d]" % (start, end, i))
    filters.append("".join("[v%d][a%d]" % (i, i) for i in range(len(keep))) +
                   "concat=n=%d:v=1:a=1[v][a]" % len(keep))
    args += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(output)]
    subprocess.run(args, check=True)


def serve(project, port):
    load(project)
    html = (Path(__file__).parent / "player.html").read_bytes()
    write_lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self.send_bytes(html, "text/html; charset=utf-8")
            if path == "/api/state":
                return self.send_bytes(json.dumps(load(project)).encode(), "application/json")
            match = re.fullmatch(r"/media/(v[1-9][0-9]*\.(?:mp4|webm|mov))", path)
            if match:
                media = project / "media" / match.group(1)
                if media.is_file() and not media.is_symlink():
                    size = media.stat().st_size
                    first, last = 0, size - 1
                    requested = self.headers.get("Range", "")
                    if requested:
                        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]*)", requested)
                        if not match:
                            return self.send_error(416)
                        first = int(match.group(1))
                        last = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
                        if first > last or first >= size:
                            return self.send_error(416)
                    self.send_response(206 if requested else 200)
                    self.send_header("Content-Type", mimetypes.guess_type(media.name)[0] or "application/octet-stream")
                    self.send_header("Accept-Ranges", "bytes")
                    if requested:
                        self.send_header("Content-Range", "bytes %d-%d/%d" % (first, last, size))
                    self.send_header("Content-Length", str(last - first + 1))
                    self.end_headers()
                    with media.open("rb") as handle:
                        handle.seek(first)
                        remaining = last - first + 1
                        while remaining:
                            chunk = handle.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    return
            self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in {"/api/comment", "/api/status"}:
                return self.send_error(404)
            try:
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise ValueError("Content-Type must be application/json")
                origin = self.headers.get("Origin")
                if origin and origin != "http://127.0.0.1:%d" % port:
                    raise ValueError("Origin is not local review")
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ValueError("Request body too large or empty")
                data = json.loads(self.rfile.read(size))
                with write_lock:
                    if path == "/api/comment":
                        result = comment(project, int(data["version"]), float(data["start"]),
                                         float(data["end"]), str(data["text"]))
                    else:
                        result = set_status(project, str(data["id"]), str(data["status"]), str(data.get("note", "")))
                self.send_bytes(json.dumps(result).encode(), "application/json")
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                self.send_bytes(json.dumps({"error": str(exc)}).encode(), "application/json", 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Open http://127.0.0.1:%d/" % port, flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Local, private video review for agents")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("project"); init.add_argument("--title", default="Video review")
    push = sub.add_parser("add"); push.add_argument("project"); push.add_argument("video"); push.add_argument("--note", default="")
    web = sub.add_parser("serve"); web.add_argument("project"); web.add_argument("--port", type=int, default=8765)
    items = sub.add_parser("comments"); items.add_argument("project"); items.add_argument("--json", action="store_true")
    address = sub.add_parser("status"); address.add_argument("project"); address.add_argument("id")
    address.add_argument("status", choices=sorted(STATUSES)); address.add_argument("--note", default="")
    edit = sub.add_parser("render"); edit.add_argument("source"); edit.add_argument("edl"); edit.add_argument("output")
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(Path(args.project), args.title)
        elif args.command == "add":
            print("Added version %d" % add(Path(args.project), Path(args.video), args.note))
        elif args.command == "serve":
            serve(Path(args.project), args.port)
        elif args.command == "comments":
            data = load(Path(args.project))["comments"]
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                for item in data:
                    if item["status"] == "open":
                        print("%s v%d %.2f-%.2fs: %s" % (item["id"], item["version"],
                              item["start"], item["end"], item["text"]))
        elif args.command == "status":
            print(json.dumps(set_status(Path(args.project), args.id, args.status, args.note), indent=2))
        elif args.command == "render":
            render(Path(args.source), Path(args.edl), Path(args.output))
            print("Rendered " + args.output)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, "Error: %s\n" % exc)


if __name__ == "__main__":
    main()
