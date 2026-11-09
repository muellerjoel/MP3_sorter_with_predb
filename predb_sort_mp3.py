#!/usr/bin/env python3
"""Scene-MP3s sortieren: PreDB-Schreibweise nur fuer Release-ORDNER.

Python 3.9+, ausschliesslich Standardbibliothek. Standard: Vorschau ohne HTTP oder Änderungen.
Vorhandene Nutzdateien werden NIEMALS überschrieben, auch nicht bei Case-Kollisionen.
"""
from __future__ import annotations

import argparse
import ctypes
from collections import defaultdict
from datetime import datetime
import errno
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import shutil
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zlib

API_HOST = "api.predb.net"
API_URL = "https://api.predb.net/"
TRACK_RE = re.compile(r"^(\d{1,3})-(.+)\.mp3$", re.I)
SFV_LINE = re.compile(r"^\s*(.+?)\s+([0-9a-fA-F]{8})\s*$")
SIDECARS = {".nfo", ".sfv", ".m3u", ".m3u8", ".cue", ".jpg", ".jpeg", ".png", ".txt"}
MAX_JSON = 1024 * 1024
MAX_NFO = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP = build_opener(NoRedirect())


def valid_name(value):
    return (isinstance(value, str) and bool(value) and value not in (".", "..")
            and not value.startswith(".") and len(value.encode("utf-8")) <= 235
            and not any(x in value for x in "/\\\0") and all(ord(c) >= 32 for c in value))


def track_release(filename):
    m = TRACK_RE.fullmatch(filename)
    return m.group(2) if m and valid_name(m.group(2)) else None


def track_number(filename):
    m = TRACK_RE.fullmatch(filename)
    return int(m.group(1)) if m else -1


def sidecar_release(filename):
    p = Path(filename)
    if p.suffix.casefold() not in SIDECARS:
        return None
    name = p.stem
    return name[3:] if name.casefold().startswith("00-") else name


def folder_tracks(folder, release):
    return sorted((p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                   and (r := track_release(p.name)) and r.casefold() == release.casefold()),
                  key=lambda p: (track_number(p.name), p.name.casefold()))


def file_exists_insensitive(folder, filename):
    """Guard against case collisions even on case-sensitive Linux filesystems."""
    if not folder.is_dir():
        return None
    target = filename.casefold()
    for entry in folder.iterdir():
        if entry.name.casefold() == target:
            return entry
    return None


def write_new_file(path, content):
    """Create with O_EXCL: existing file or casefold neighbour is NEVER overwritten."""
    if file_exists_insensitive(path.parent, path.name):
        return False
    created = False
    try:
        with path.open("xb") as fp:
            created = True
            fp.write(content)
        return True
    except FileExistsError:
        return False
    except OSError:
        if created:
            path.unlink(missing_ok=True)  # delete ONLY our failed new file
        raise


def move_no_replace(src, dst):
    """Hardlink then unlink source, or exclusive-copy across devices; no rename clobber."""
    if src.is_symlink() or not src.is_file():
        return False, "Keine regulaere Datei"
    if file_exists_insensitive(dst.parent, dst.name):
        return False, "Ziel bereits vorhanden"
    try:
        os.link(src, dst)  # atomically fails if dst exists
        try:
            src.unlink()
        except OSError:
            dst.unlink()  # revert our newly-created link only
            raise
        return True, ""
    except FileExistsError:
        return False, "Ziel bereits vorhanden"
    except OSError as e:
        # EXDEV: anderes Dateisystem; ENOTSUP/EPERM/EINVAL: Hardlinks auf
        # bestimmten NAS-/FUSE-/SMB-Mounts nicht verfuegbar. Mit exklusivem
        # xb-Open kopieren, niemals ein vorhandenes Ziel oeffnen/ersetzen.
        if e.errno not in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP,
                           errno.ENOTSUP, errno.EINVAL, errno.ENOSYS):
            return False, str(e)
    # Datei ausschliesslich mit O_EXCL neu anlegen; keine Ueberschreibung.
    created = False
    try:
        with src.open("rb") as source, dst.open("xb") as output:
            created = True
            shutil.copyfileobj(source, output, 1024 * 1024)
        shutil.copystat(src, dst)
        src.unlink()
        return True, ""
    except FileExistsError:
        return False, "Ziel bereits vorhanden"
    except OSError as e:
        if created:
            dst.unlink(missing_ok=True)  # never touch a pre-existing file
        return False, str(e)


def rename_noreplace(src, dst):
    """Atomare Umbenennung ohne Ersetzen des Zielpfads (Linux renameat2)."""
    if not src.exists() or src.is_symlink():
        return False, "Quelle fehlt oder ist ein Symlink"
    if dst.exists() or dst.is_symlink():
        return False, "Ziel bereits vorhanden"
    if sys.platform.startswith("linux"):
        try:
            lib = ctypes.CDLL(None, use_errno=True)
            func = lib.renameat2
            func.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            func.restype = ctypes.c_int
            # AT_FDCWD=-100, RENAME_NOREPLACE=1: auch bei einem Race kein Ersetzen.
            rc = func(-100, os.fsencode(src), -100, os.fsencode(dst), 1)
            if rc == 0:
                return True, ""
            err = ctypes.get_errno()
            if err == errno.EEXIST:
                return False, "Ziel bereits vorhanden"
            return False, os.strerror(err)
        except AttributeError:
            return False, "Sichere Umbenennung auf diesem Linux nicht verfuegbar"
    if os.name == "nt":
        # Unter Windows schlaegt os.rename bei vorhandenen Zielen fehl.
        try:
            os.rename(src, dst)
            return True, ""
        except OSError as e:
            return False, str(e)
    return False, "Sichere Umbenennung auf diesem System nicht verfuegbar"


def rename_case_exact(src, dst):
    """Name auf exakt die PreDB-Schreibweise bringen, nie andere Dateien ersetzen."""
    if src == dst:
        return True, ""
    if src.parent != dst.parent or src.name.casefold() != dst.name.casefold():
        return False, "Keine reine Gross-/Kleinschreibungs-Korrektur"
    for item in src.parent.iterdir():
        if item != src and item.name.casefold() == dst.name.casefold():
            return False, "Anderes Ziel bereits vorhanden"
    if not dst.exists() and not dst.is_symlink():
        return rename_noreplace(src, dst)
    try:
        if not dst.samefile(src):
            return False, "Anderes Ziel bereits vorhanden"
    except OSError:
        return False, "Ziel nicht pruefbar"
    # Case-insensitive Mount: zwei atomare Umbenennungen mit exklusivem Ziel.
    for _ in range(5):
        tmp = src.parent / (".predb-recase-" + secrets.token_hex(12))
        if not tmp.exists() and not tmp.is_symlink():
            break
    else:
        return False, "Kein freier temporaerer Name"
    ok, reason = rename_noreplace(src, tmp)
    if not ok:
        return False, reason
    ok, reason = rename_noreplace(tmp, dst)
    if ok:
        return True, ""
    restored, restore_reason = rename_noreplace(tmp, src)
    if not restored:
        return False, f"Korrektur fehlgeschlagen ({reason}); temporaere Quelle: {tmp} ({restore_reason})"
    return False, reason


def _move_folder_files(src, dst):
    """Nur regulaere Dateien umziehen, mit Rollback bei einem Fehler.

    Keine Verzeichnisbestaende aendern, falls sich Unterordner, Symlinks
    oder gleichnamige Dateien (case-insensitive) im Quellordner befinden.
    """
    items = list(src.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in items):
        return False, "Unterordner/Symlinks/Spezialdateien vorhanden; bitte manuell pruefen"
    names = [p.name.casefold() for p in items]
    if len(names) != len(set(names)):
        return False, "Mehrere Dateien unterscheiden sich nur in Gross-/Kleinschreibung"
    moved = []
    for old in items:
        ok, why = move_no_replace(old, dst / old.name)
        if not ok:
            rollback_errors = []
            for original in reversed(moved):
                restored, why_back = move_no_replace(dst / original.name, original)
                if not restored:
                    rollback_errors.append(f"{original.name}: {why_back}")
            if rollback_errors:
                return False, (f"{old.name}: {why}; Ruecktransfer unvollstaendig: "
                               + "; ".join(rollback_errors) + f"; Daten in {dst}")
            return False, f"{old.name}: {why} (vorherige Verschiebungen rueckgaengig)"
        moved.append(old)
    return True, ""


def _recover_from_stage(stage, original):
    """Best-effort Wiederherstellung ohne Ueberschreibung; Stage bleibt sonst erhalten."""
    try:
        if not original.exists():
            original.mkdir(exist_ok=False)
        if not original.is_dir() or original.is_symlink():
            return "Quelle nicht wiederherstellbar; Dateien gesichert in " + str(stage)
        ok, why = _move_folder_files(stage, original)
        if not ok:
            return "Ruecktransfer fehlgeschlagen: " + why + "; Dateien in " + str(stage)
        stage.rmdir()
        return "Ursprungsordner wiederhergestellt"
    except OSError as exc:
        return f"Wiederherstellung fehlgeschlagen ({exc}); Dateien gesichert in {stage}"


def recase_folder_compatible(src, dst):
    """Fallback fuer NAS-/SMB-/FUSE-Mounts ohne renameat2(RENAME_NOREPLACE).

    Verwendet ausschliesslich exklusive Dateioperationen. Auf case-insensitiven
    Mounts wird ein zufaelliges Stage-Verzeichnis benoetigt, damit der alte
    Name verschwindet, bevor der PreDB-Name neu angelegt wird.
    """
    if src == dst:
        return True, ""
    if src.parent != dst.parent or src.name.casefold() != dst.name.casefold():
        return False, "Keine reine Gross-/Kleinschreibungs-Korrektur"
    if src.is_symlink() or not src.is_dir():
        return False, "Quelle ist kein regulaerer Ordner"
    if dst.exists() and not dst.samefile(src):
        return False, "Anderer Zielordner vorhanden; keine Ueberschreibung"
    for p in src.parent.iterdir():
        if p != src and p.name.casefold() == dst.name.casefold():
            return False, "Namenskollision mit anderem Ordner"
    # Vorpruefung vor jeder Aenderung.
    items = list(src.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in items):
        return False, "Enthaelt Unterordner/Symlinks; aus Sicherheitsgruenden nicht veraendert"
    folded = [p.name.casefold() for p in items]
    if len(folded) != len(set(folded)):
        return False, "Dateinamen mit Case-Kollision; nicht veraendert"

    if not dst.exists():
        # Auf case-sensitiven Mounts reichen mkdir + exklusiver Dateiumzug.
        try:
            dst.mkdir(exist_ok=False)
        except OSError as e:
            return False, str(e)
        ok, why = _move_folder_files(src, dst)
        if not ok:
            try:
                dst.rmdir()
            except OSError:
                pass  # bei Rollback-Problem niemals Daten entfernen
            return False, why
        try:
            src.rmdir()
        except OSError as e:
            return False, f"Quelle nach sicherem Dateiumzug nicht leer ({e}); bitte pruefen"
        return True, ""

    # dst.samefile(src): z.B. case-insensitives SMB, exakter Zielname noch
    # nicht anlegbar. Kein os.rename/os.replace, das ein Ziel ersetzen koennte.
    stage = None
    try:
        for _ in range(5):
            candidate = src.parent / (".predb-recase-" + secrets.token_hex(12))
            try:
                candidate.mkdir(exist_ok=False)
                stage = candidate
                break
            except FileExistsError:
                continue
        if stage is None:
            return False, "Kein freier temporaerer Stage-Ordner"
        ok, why = _move_folder_files(src, stage)
        if not ok:
            try:
                stage.rmdir()
            except OSError:
                pass
            return False, why
        try:
            src.rmdir()
        except OSError as e:
            recovery = _recover_from_stage(stage, src)
            return False, f"Alten Ordner nicht entfernbar ({e}); {recovery}"
        try:
            dst.mkdir(exist_ok=False)
        except OSError as e:
            recovery = _recover_from_stage(stage, src)
            return False, f"Kanonischen Ordner nicht anlegbar ({e}); {recovery}"
        ok, why = _move_folder_files(stage, dst)
        if not ok:
            # Falls alle Dateien ruecktransferiert wurden, ist stage leer;
            # andernfalls bewusst sichtbar als Warnung im Fehlertext.
            return False, f"Ruecktransfer aus {stage} nach {dst} fehlgeschlagen: {why}"
        stage.rmdir()
        return True, ""
    except OSError as e:
        return False, f"NAS-/Dateisystemfehler ({e}); etwaige Daten in {stage} pruefen"


def recase_sidecar_compatible(src, dst):
    """Sichere Case-Korrektur von M3U/SFV/NFO, wenn renameat2 nicht geht.

    NAS/SMB/FUSE koennen renameat2(RENAME_NOREPLACE) mit EINVAL ablehnen.
    Deshalb wird die Begleitdatei ueber einen einmaligen Zwischen-Dateinamen
    exklusiv verschoben (Hardlink oder exklusives Kopieren mit 'xb').
    Ein Ziel oder eine abweichend geschriebene Nachbardatei wird nie ersetzt.
    MP3-Dateinamen werden von dieser Funktion niemals veraendert.
    """
    if src == dst:
        return True, ""
    if src.parent != dst.parent or src.name.casefold() != dst.name.casefold():
        return False, "Keine reine Gross-/Kleinschreibungs-Korrektur"
    if src.suffix.casefold() not in (".m3u", ".sfv", ".nfo"):
        return False, "Nur M3U/SFV/NFO duerfen hier korrigiert werden"
    if src.is_symlink() or not src.is_file():
        return False, "Keine regulaere Begleitdatei"
    matches = [p for p in src.parent.iterdir() if p.name.casefold() == dst.name.casefold()]
    if len(matches) != 1 or matches[0] != src:
        return False, "Andere gleichnamige Datei vorhanden; keine Ueberschreibung"
    try:
        if dst.exists() and not dst.samefile(src):
            return False, "Anderes Ziel bereits vorhanden"
    except OSError:
        return False, "Ziel nicht sicher pruefbar"

    for _ in range(8):
        tmp = src.parent / (".predb-sidecar-" + secrets.token_hex(12))
        if not tmp.exists() and not tmp.is_symlink():
            break
    else:
        return False, "Kein freier temporaerer Dateiname"

    ok, reason = move_no_replace(src, tmp)
    if not ok:
        return False, "Zwischenverschiebung fehlgeschlagen: " + reason
    ok, reason = move_no_replace(tmp, dst)
    if ok:
        return True, ""
    restored, why = move_no_replace(tmp, src)
    if restored:
        return False, "Umbenennung fehlgeschlagen (" + reason + "); Quelldatei wiederhergestellt"
    return False, ("Umbenennung fehlgeschlagen (" + reason + "); Original gesichert in "
                   + str(tmp) + " (Rueckverschiebung: " + why + ")")


def rename_sidecar_safe(src, dst):
    """Dateinamen von M3U/SFV/NFO ausschliesslich klein schreiben."""
    if src.suffix.casefold() not in (".m3u", ".sfv", ".nfo"):
        return False, "Begleitdatei erwartet; MP3-Dateinamen bleiben unveraendert"
    if src.name.casefold() == dst.name.casefold():
        ok, reason = rename_case_exact(src, dst)
        if not ok and reason in (os.strerror(errno.EINVAL), os.strerror(errno.ENOSYS),
                                 os.strerror(errno.EOPNOTSUPP), os.strerror(errno.ENOTSUP)):
            return recase_sidecar_compatible(src, dst)
        return ok, reason
    return move_no_replace(src, dst)


def json_read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def json_cache_write(path, obj):
    """Cache is internal transient metadata, not a user/media file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".predb-cache-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(obj, out, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def valid_nfo_url(url):
    if not isinstance(url, str):
        return False
    try:
        u = urlsplit(url)
        return (u.scheme == "https" and u.hostname == API_HOST and u.port in (None, 443)
                and not u.username and not u.password and u.path.startswith("/nfo/")
                and u.path.lower().endswith((".nfo", ".txt")))
    except ValueError:
        return False


def api_record(payload, requested):
    """Return exact (case-insensitive) matched release only, never a fuzzy neighbour."""
    if not isinstance(payload, dict) or payload.get("status") == "error":
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return None
    matches = [x for x in data if isinstance(x, dict) and isinstance(x.get("release"), str)
               and x["release"].casefold() == requested.casefold() and valid_name(x["release"])]
    return matches[0] if len(matches) == 1 else None


class PreDB:
    def __init__(self, cache, delay, cache_days, offline):
        self.cache = cache
        self.delay = delay
        self.cache_days = cache_days
        self.offline = offline
        self.last_request = 0.0
        self.requests = 0
        self.hits = 0
        self.blocked = False  # only for this run if the provider explicitly denies requests

    def cache_path(self, release):
        return self.cache / (hashlib.sha256(release.casefold().encode()).hexdigest() + ".json")

    def request(self, url, max_bytes):
        if self.offline or self.blocked:
            return None
        u = urlsplit(url)
        if (u.scheme != "https" or u.hostname != API_HOST or u.port not in (None, 443)
                or u.username or u.password or (u.path != "/" and not valid_nfo_url(url))):
            raise ValueError("Unerlaubtes API-Ziel")
        wait = self.delay - (time.monotonic() - self.last_request) if self.last_request else 0
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()
        self.requests += 1
        try:
            with HTTP.open(Request(url, headers={"User-Agent": "MP3ReleaseSorter/8.0", "Accept": "application/json, text/plain, application/octet-stream"}), timeout=25) as res:
                if res.geturl() != url:
                    raise ValueError("Unbekannte API-Weiterleitung")
                body = res.read(max_bytes + 1)
                if "text/html" in res.headers.get("Content-Type", "").lower() or body.lstrip().lower().startswith((b"<html", b"<!doctype html")):
                    raise ValueError("API liefert HTML statt JSON/NFO")
                if len(body) > max_bytes:
                    raise ValueError("API-Antwort zu gross")
                return body
        except HTTPError as e:
            if e.code in (403, 429):
                self.blocked = True
                print(f"  [API] HTTP {e.code}: Server verweigert Anfragen. Keine weiteren Netzwerkanfragen in diesem Lauf.")
            elif e.code != 404:
                print(f"  [API] HTTP {e.code}: Anfrage fehlgeschlagen")
        except (URLError, TimeoutError, OSError, ValueError) as e:
            print(f"  [API] Netzwerk/API-Fehler: {e}")
        return None

    def query(self, kind, release):
        url = API_URL + "?" + urlencode({"type": kind, "release": release})
        raw = self.request(url, MAX_JSON)
        if not raw:
            return None
        try:
            return api_record(json.loads(raw.decode("utf-8")), release)
        except (UnicodeError, ValueError):
            print("  [API] Ungueltige JSON-Antwort")
            return None


    def search_exact(self, release):
        """Fallback nur mit exakt case-insensitiv passendem Release, kein Fuzzy-Match."""
        url = API_URL + "?" + urlencode({"type": "search", "q": release, "limit": 100})
        raw = self.request(url, MAX_JSON)
        if not raw:
            return None
        try:
            return api_record(json.loads(raw.decode("utf-8")), release)
        except (UnicodeError, ValueError):
            print("  [API] Ungueltige JSON-Antwort bei Suche")
            return None

    def metadata(self, release):
        path = self.cache_path(release)
        cached = json_read(path)
        if isinstance(cached, dict) and isinstance(cached.get("saved"), (int, float)):
            record_cached = cached.get("record")
            if record_cached is not None and not (isinstance(record_cached, dict)
                  and valid_name(record_cached.get("release"))
                  and record_cached["release"].casefold() == release.casefold()):
                record_cached = None  # niemals fragwuerdige Cache-Schreibweise verwenden
            expiration = self.cache_days if record_cached else min(1, self.cache_days)
            if time.time() - cached["saved"] < expiration * 86400:
                self.hits += 1
                return record_cached
        if self.offline or self.blocked:
            return None
        record = self.query("pre", release)
        if not record and not self.blocked:
            record = self.search_exact(release)
        # NFO-URL nur aus bestaetigtem, exaktem PreDB-Treffer lesen.
        if record and not valid_nfo_url(record.get("nfo")) and not self.blocked:
            nfo_record = self.query("nfo", record["release"])
            if nfo_record and valid_nfo_url(nfo_record.get("nfo")):
                record["nfo"] = nfo_record["nfo"]
        if not self.blocked:
            json_cache_write(path, {"saved": time.time(), "record": record})
        return record

    def nfo_bytes(self, record):
        url = record.get("nfo") if isinstance(record, dict) else None
        if not valid_nfo_url(url):
            return None
        data = self.request(url, MAX_NFO)
        if not data:
            return None
        if data.lstrip().startswith((b"{", b"[")):
            try:
                if isinstance(json.loads(data.decode("utf-8")), (dict, list)):
                    return None
            except (UnicodeError, ValueError):
                pass
        return data


def groups_from_root(root):
    """Index both loose tracks and already-sorted release directories."""
    groups = defaultdict(lambda: {"names": [], "loose": [], "dirs": []})
    other_files = []
    for item in root.iterdir():
        if item.is_symlink():
            continue
        if item.is_file():
            r = track_release(item.name)
            if r:
                key = r.casefold()
                groups[key]["names"].append(r)
                groups[key]["loose"].append(item)
            elif item.suffix.casefold() in SIDECARS:
                other_files.append(item)
        elif item.is_dir() and item.name != ".predb_cache" and valid_name(item.name):
            # Don't mistake an unrelated folder for a release folder.
            try:
                if folder_tracks(item, item.name):
                    key = item.name.casefold()
                    groups[key]["names"].append(item.name)
                    groups[key]["dirs"].append(item)
            except OSError:
                continue
    for p in other_files:
        r = sidecar_release(p.name)
        if r and r.casefold() in groups:
            groups[r.casefold()]["loose"].append(p)
    return groups


def parse_m3u(path):
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        generated = any("lokal erzeugt" in line.casefold() for line in lines[:5])
        entries = {PureWindowsPath(line.strip().replace("/", "\\")).name.casefold()
                   for line in lines if line.strip() and not line.lstrip().startswith("#")
                   and line.strip().casefold().endswith(".mp3")}
        return entries if entries and not generated else None
    except OSError:
        return None


def parse_sfv(path):
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()
        if any("lokal erzeugt" in line.casefold() for line in lines[:5]):
            return None
        entries = {}
        for line in lines:
            if line.startswith(";") or not line.strip():
                continue
            m = SFV_LINE.match(line)
            if m and m.group(1).casefold().endswith(".mp3"):
                entries[PureWindowsPath(m.group(1).replace("/", "\\")).name.casefold()] = int(m.group(2), 16)
        return entries or None
    except OSError:
        return None


def file_crc(path):
    crc = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xffffffff


def completeness(folder, release, api_count):
    tracks = folder_tracks(folder, release)
    if not tracks:
        print("  [PRUEFUNG] Keine MP3-Dateien gefunden")
        return
    found = {p.name.casefold(): p for p in tracks}
    numbers = {track_number(p.name) for p in tracks}
    missing_nums = sorted(set(range(1, max(numbers) + 1)) - numbers) if max(numbers) <= 999 else []
    references = set()
    original_sfv = []
    for p in folder.iterdir():
        if p.is_symlink() or not p.is_file():
            continue
        if p.suffix.casefold() in (".m3u", ".m3u8"):
            expected = parse_m3u(p)
            if expected:
                references.update(expected)
        elif p.suffix.casefold() == ".sfv":
            expected = parse_sfv(p)
            if expected:
                references.update(expected)
                original_sfv.append((p, expected))
    missing_files = sorted(name for name in references if name not in found)
    bad_crc = []
    for sfv, expected in original_sfv:
        for name, crc in expected.items():
            if name in found:
                try:
                    if file_crc(found[name]) != crc:
                        bad_crc.append(found[name].name)
                except OSError as e:
                    print(f"  [PRUEFUNG] CRC-Fehler bei {name}: {e}")
    issues = bool(missing_nums or missing_files or bad_crc)
    if missing_nums:
        print("  [UNVOLLSTAENDIG] Fehlende Tracknummern:", ", ".join(map(str, missing_nums)))
    if missing_files:
        print("  [UNVOLLSTAENDIG] In originaler M3U/SFV referenziert, aber fehlt:", ", ".join(missing_files))
    if bad_crc:
        print("  [UNVOLLSTAENDIG] CRC32 stimmt nicht:", ", ".join(sorted(set(bad_crc))))
    if not issues:
        if references:
            print(f"  [PRUEFUNG] {len(tracks)} MP3 vorhanden; Original-M3U/SFV und Tracknummern ohne erkannten Fehler.")
        else:
            print(f"  [PRUEFUNG] {len(tracks)} MP3 vorhanden, keine Luecken in Tracknummern. "
                  "Ohne Originaldateiliste keine sichere Vollstaendigkeitsbestaetigung.")
    if isinstance(api_count, int):
        print(f"  [PREDB] {api_count} Dateien insgesamt laut PreDB (enthaelt evtl. NFO/SFV/M3U; "
              "nicht als MP3-Sollzahl verwendbar).")


def create_sidecars(folder, release):
    tracks = folder_tracks(folder, release)
    if not tracks:
        return
    present = [p for p in folder.iterdir() if p.is_file() and not p.is_symlink()]
    for ext in (".m3u", ".sfv"):
        matching = [p for p in present if p.suffix.casefold() == ext
                    and (r := sidecar_release(p.name)) and r.casefold() == release.casefold()]
        # An existing file receives the requested 00- prefix when free. Never overwrite.
        wanted = "00-" + release.lower() + ext
        if matching:
            if any(p.name == wanted for p in matching):
                continue
            old = sorted(matching, key=lambda p: (not p.name.casefold().startswith("00-"), p.name.casefold()))[0]
            other = file_exists_insensitive(folder, wanted)
            if other is None or other == old:
                success, reason = rename_sidecar_safe(old, folder / wanted)
                print(f"  [{'UMBENANNT' if success else 'BLEIBT'}] {old.name} -> {wanted}" + (f" ({reason})" if not success else ""))
            else:
                print(f"  [BLEIBT] Ziel mit anderer Schreibweise schon vorhanden: {other.name}")
            continue
        if ext == ".m3u":
            content = ("#EXTM3U\n# Lokal erzeugt aus vorhandenen MP3, keine Original-Scene-M3U\n"
                       + "".join(p.name + "\n" for p in tracks)).encode("utf-8")
        else:
            lines = ["; Lokal erzeugt: CRC32 der vorhandenen Dateien, keine Original-Scene-SFV\r\n"]
            try:
                lines.extend(f"{p.name} {file_crc(p):08X}\r\n" for p in tracks)
            except OSError as e:
                print(f"  [FEHLER] SFV nicht erzeugt: {e}")
                continue
            content = "".join(lines).encode("utf-8")
        if write_new_file(folder / wanted, content):
            print("  [ERSTELLT]", wanted)
        else:
            print("  [BLEIBT] Bereits vorhanden:", wanted)


def has_nfo(folder, release):
    return any(p.is_file() and not p.is_symlink() and p.suffix.casefold() == ".nfo"
               and (r := sidecar_release(p.name)) and r.casefold() == release.casefold()
               for p in folder.iterdir())



def recase_existing_nfos(folder, release):
    for p in list(folder.iterdir()):
        if (p.is_symlink() or not p.is_file() or p.suffix.casefold() != ".nfo"
                or (r := sidecar_release(p.name)) is None or r.casefold() != release.casefold()):
            continue
        pref = "00-" if p.stem.casefold().startswith("00-") else ""
        wanted = pref + release.lower() + ".nfo"
        if p.name == wanted:
            continue
        neighbour = file_exists_insensitive(folder, wanted)
        if neighbour is not None and neighbour != p:
            print(f"  [NFO] Andere Datei belegt Zielnamen: {wanted}; keine Ueberschreibung")
            continue
        ok, reason = rename_sidecar_safe(p, folder / wanted)
        print(f"  [{'UMBENANNT' if ok else 'BLEIBT'}] {p.name} -> {wanted}" + (f" ({reason})" if not ok else ""))


def process(root, key, group, client, apply, no_nfo):
    local_name = group["names"][0]
    record = client.metadata(local_name) if apply or client.offline else None
    if record and valid_name(record.get("release")):
        name = record["release"]
    else:
        name = None
    print(f"\n[RELEASE] {local_name}" + (f" -> {name} (PreDB-verifiziert)" if name else ""))
    if not apply:
        print("  [VORSCHAU] " + (f"PreDB-Ziel: {name}" if name else
              "Korrekte Schreibweise erst mit --apply online / gueltigem Cache pruefbar")
              + f"; lose Dateien: {len(group['loose'])}; Ordner: {len(group['dirs'])}")
        return 0
    if name is None:
        print("  [UEBERSPRUNGEN] Kein verifizierter PreDB-Name; NICHT mit geratener Schreibweise sortiert.")
        return 1
    target = root / name
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        print("  [FEHLER] Ziel ist keine sichere Verzeichnisadresse:", target)
        return 1
    # Bestehenden Ordner exakt auf API-Schreibweise umbenennen. Dies gilt auch
    # fuer Case-insensitive Dateisysteme, ohne die Daten neu anzulegen.
    existing = group["dirs"]
    if len(existing) == 1 and existing[0] != target:
        old = existing[0]
        same_location = False
        if target.exists():
            try:
                same_location = target.samefile(old)
            except OSError:
                pass
        if not target.exists() or same_location:
            ok, why = rename_case_exact(old, target)
            if not ok and why in (os.strerror(errno.EINVAL), os.strerror(errno.ENOSYS),
                                  os.strerror(errno.EOPNOTSUPP), os.strerror(errno.ENOTSUP)):
                print("  [ORDNER] Dateisystem unterstuetzt atomare Case-Umbenennung nicht; "
                      "verwende sicheren Datei-Umzug ohne Ueberschreiben")
                ok, why = recase_folder_compatible(old, target)
            if not ok:
                print(f"  [FEHLER] Ordnerschreibweise konnte nicht sicher korrigiert werden: {why}")
                return 1
            print("  [ORDNER] Originalschreibweise hergestellt:", old.name, "->", target.name)
            group["dirs"] = [target]
    if not target.exists():
        try:
            target.mkdir(exist_ok=False)
            print("  [ORDNER] Erstellt mit PreDB-Schreibweise:", target.name)
        except FileExistsError:
            if not target.is_dir() or target.is_symlink():
                print("  [FEHLER] Ziel wurde bereits belegt")
                return 1
    # MP3-Trackdateien bleiben exakt unveraendert.
    # Nur der Release-ORDNER erhaelt die PreDB-Schreibweise.
    # M3U/SFV/NFO haben dagegen IMMER kleingeschriebene Dateinamen.
    # Include all direct files from old folders: preserve unrelated files as well.
    sources = sorted(group["loose"], key=lambda p: p.name.casefold())
    for old_folder in sorted(group["dirs"], key=lambda p: p.name.casefold()):
        if old_folder == target:
            continue
        sources += sorted((p for p in old_folder.iterdir() if p.is_file() and not p.is_symlink()),
                          key=lambda p: p.name.casefold())
    errors = 0
    for source in sources:
        dst = target / source.name
        if source.parent == target:
            continue
        success, reason = move_no_replace(source, dst)
        if success:
            print("  [VERSCHOBEN]", source.name)
        else:
            print("  [BLEIBT]", source, "->", reason)
            if reason != "Ziel bereits vorhanden":
                errors += 1
    for old_folder in group["dirs"]:
        if old_folder != target:
            try:
                old_folder.rmdir()  # succeeds only if empty; never deletes file contents
                print("  [ORDNER] Leeren Altordner entfernt:", old_folder.name)
            except OSError:
                print("  [ORDNER] Altordner behalten (nicht leer):", old_folder.name)
    # Completeness is checked BEFORE local M3U/SFV generation, to avoid claiming
    # that self-generated lists are an independent reference.
    completeness(target, name, record.get("files") if record else None)
    create_sidecars(target, name)
    recase_existing_nfos(target, name)
    if has_nfo(target, name):
        print("  [NFO] Bereits vorhanden; kein Download, keine Ueberschreibung")
    elif no_nfo or client.offline:
        print("  [NFO] Download deaktiviert")
    elif record is None:
        print("  [NFO] Kein passender PreDB-API-Eintrag")
    else:
        content = client.nfo_bytes(record)
        if content:
            filename = name.lower() + ".nfo"
            if write_new_file(target / filename, content):
                print("  [NFO] Heruntergeladen:", filename)
            else:
                print("  [NFO] Ziel bereits vorhanden; nicht ueberschrieben:", filename)
        else:
            print("  [NFO] Keine herunterladbare NFO bei PreDB.net")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="MP3 sortieren, PreDB-Namen/NFO abrufen, M3U/SFV mit 00- erzeugen; ohne Ueberschreiben")
    parser.add_argument("directory", nargs="?", default=".", help="Hauptverzeichnis mit MP3 und Release-Ordnern")
    parser.add_argument("--apply", action="store_true", help="Aenderungen ausfuehren; andernfalls nur lokale Vorschau")
    parser.add_argument("--offline", action="store_true", help="Keine API-Aufrufe; nur Releases mit gueltigem PreDB-Cache verarbeiten")
    parser.add_argument("--no-nfo", action="store_true", help="NFO nicht herunterladen (PreDB-Metadaten weiterhin abrufen)")
    parser.add_argument("--delay", type=float, default=3.0, help="Pause zwischen HTTP-Anfragen in Sekunden (Standard: 3)")
    parser.add_argument("--cache-days", type=float, default=30, help="Positive API-Metadaten zwischenspeichern (Standard: 30 Tage)")
    parser.add_argument("--limit", type=int, help="Nur die ersten N Releases verarbeiten")
    args = parser.parse_args(argv)
    if args.delay < 2 or args.cache_days <= 0 or (args.limit is not None and args.limit < 1):
        parser.error("--delay mindestens 2, --cache-days > 0, --limit > 0")
    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"Verzeichnis existiert nicht: {root}")
    groups = groups_from_root(root)
    keys = sorted(groups)
    if args.limit:
        keys = keys[:args.limit]
    client = PreDB(root / ".predb_cache", args.delay, args.cache_days, args.offline or not args.apply)
    print("Modus:", "AUSFUEHREN" if args.apply else "VORSCHAU (offline)")
    print(f"Verzeichnis: {root} | Releases: {len(keys)} | API-Pause: {args.delay:g}s")
    print("Grundsatz: Ordnername exakt nach PreDB, Dateinamen bleiben klein; nie ueberschreiben.")
    print("PreDB-API-Limit: 30 Anfragen / 60 Sekunden; bei HTTP 403/429 wird der Zugriff im aktuellen Lauf beendet.")
    errors = 0
    for key in keys:
        try:
            errors += process(root, key, groups[key], client, args.apply, args.no_nfo)
        except OSError as e:
            print(f"  [FEHLER] {groups[key]['names'][0]}: {e}")
            errors += 1
    print(f"\nFERTIG: {len(keys)} Releases verarbeitet, {client.requests} API-Anfragen, {client.hits} Cachetreffer, {errors} Fehler")
    if not args.apply:
        print("Keine Dateien veraendert und keine Netzwerkabfragen gestartet.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
