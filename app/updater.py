"""Updates from GitHub Releases for RX16 Control Center.

- checks https://api.github.com/repos/<repo>/releases/latest a minute after start, then every 6 hours
  (and when the user presses "check now"); the repo name comes from app\\update.json
- a newer release is only offered, never installed on its own: the user presses "update now"
- the setup asset is downloaded over HTTPS and its SHA-256 must match the digest GitHub publishes for
  the asset (or a "SHA256: <hex>" line in the release notes) before it is run
- the setup runs with /update (no wizard); the service is already admin, so there's no UAC prompt.
  The setup stops this app, replaces the files and starts it again.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

UA = {"User-Agent": "RX16-Control-Center-updater", "Accept": "application/vnd.github+json"}


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)


class Updater:
    def __init__(self, base, current, notify, tr):
        self.current, self.notify, self.tr = current, notify, tr
        self.repo = None
        try:
            with open(os.path.join(base, "update.json"), encoding="utf-8") as f:
                self.repo = (json.load(f).get("repo") or "").strip() or None
        except (OSError, ValueError):
            pass
        self.lock = threading.Lock()
        self.info = {"repo": self.repo, "current": current, "available": False, "status": "idle",
                     "checked_at": None, "error": None}
        self.notified = None

    # ---------------------------------------------------------------- check
    def check(self):
        if not self.repo:
            return self.state()
        with self.lock:
            self.info["status"] = "checking"
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{self.repo}/releases/latest", headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                rel = json.load(r)
            latest = (rel.get("tag_name") or "").lstrip("vV")
            asset = next((a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".exe")
                          and "setup" in a.get("name", "").lower()), None)
            digest = (asset or {}).get("digest") or ""
            sha = digest.split(":", 1)[1] if digest.lower().startswith("sha256:") else None
            if not sha:
                m = re.search(r"SHA256[:\s]+([0-9a-fA-F]{64})", rel.get("body") or "")
                sha = m.group(1) if m else None
            newer = vtuple(latest) > vtuple(self.current)
            with self.lock:
                self.info.update({
                    "available": bool(newer and asset and sha), "latest": latest, "notes": rel.get("body") or "",
                    "page": rel.get("html_url"), "url": (asset or {}).get("browser_download_url"),
                    "size": (asset or {}).get("size"), "sha256": (sha or "").lower(),
                    "checked_at": int(time.time()), "error": None, "status": "idle",
                })
                if newer and not (asset and sha):
                    self.info["error"] = "release has no verified setup file"
            if self.info["available"] and self.notified != latest:
                self.notified = latest
                self.notify(self.tr(f"تحديث جديد: {latest}", f"Update available: {latest}"),
                            self.tr("افتح البرنامج ← الإعدادات واضغط «تحديث الحين».",
                                    "Open the app → Settings and press “Update now”."), True)
        except Exception as e:
            with self.lock:
                self.info.update({"status": "idle", "error": str(e)[:200], "checked_at": int(time.time())})
        return self.state()

    @staticmethod
    def folder():
        return os.path.join(tempfile.gettempdir(), "rx16-update")

    def cleanup(self):
        """Delete the setup downloaded for the last update. Runs a minute after start (the setup that
        restarted us has closed by then) and before every check; skipped while an update is running."""
        with self.lock:
            busy = self.info["status"] in ("downloading", "installing")
        if not busy and os.path.isdir(self.folder()):
            shutil.rmtree(self.folder(), ignore_errors=True)

    def loop(self):
        time.sleep(60)
        while True:
            self.cleanup()
            self.check()
            time.sleep(6 * 3600)

    def state(self):
        with self.lock:
            return dict(self.info)

    # ---------------------------------------------------------------- install
    def install(self):
        with self.lock:
            if not self.info.get("available") or self.info["status"] in ("downloading", "installing"):
                return False
            self.info["status"] = "downloading"
            url, sha = self.info["url"], self.info["sha256"]
        threading.Thread(target=self._install, args=(url, sha), daemon=True).start()
        return True

    def _install(self, url, sha):
        try:
            folder = self.folder()
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "RX16-Setup.exe")
            h = hashlib.sha256()
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]}), timeout=60) as r, \
                    open(path, "wb") as f:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    h.update(chunk)
                    f.write(chunk)
            if h.hexdigest().lower() != sha:
                os.remove(path)
                raise RuntimeError("SHA-256 mismatch - download rejected")
            with self.lock:
                self.info["status"] = "installing"
            # detached + out of our job, so it keeps running when it stops this app
            flags = 0x00000008 | 0x00000200 | 0x01000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB
            try:
                subprocess.Popen([path, "/update"], creationflags=flags, close_fds=True, cwd=folder)
            except OSError:
                subprocess.Popen([path, "/update"], creationflags=flags & ~0x01000000, close_fds=True, cwd=folder)
        except Exception as e:
            with self.lock:
                self.info.update({"status": "idle", "error": str(e)[:200]})
