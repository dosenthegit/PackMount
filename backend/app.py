import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field, field_validator

APP_VERSION = "1.3.0"
APP_NAME = "PackMount"

CONFIG_DIR = Path(os.getenv("PACKMOUNT_CONFIG_DIR", "/config"))
DB_PATH = CONFIG_DIR / "packmount.db"
FERNET_KEY_PATH = CONFIG_DIR / "fernet.key"
SESSION_SECRET_PATH = CONFIG_DIR / "session.secret"

MOUNT_ROOT = Path(os.getenv("PACKMOUNT_MOUNT_ROOT", "/mnt/packmount")).resolve()
REMOTE_ROOT = Path(os.getenv("PACKMOUNT_REMOTE_ROOT", "/run/packmount/sources")).resolve()
CREDENTIAL_ROOT = Path(os.getenv("PACKMOUNT_CREDENTIAL_ROOT", "/run/packmount/credentials")).resolve()
SMB_CONF_PATH = Path(os.getenv("PACKMOUNT_SMB_CONF", "/etc/samba/smb.conf"))
NFS_EXPORTS_PATH = Path(os.getenv("PACKMOUNT_NFS_EXPORTS", "/etc/exports.d/packmount.exports"))

DEFAULT_ALLOWED_ROOTS = "/data,/mnt,/media"
ALLOWED_LOCAL_ROOTS = [
    Path(p.strip()).resolve()
    for p in os.getenv("PACKMOUNT_ALLOWED_LOCAL_ROOTS", DEFAULT_ALLOWED_ROOTS).split(",")
    if p.strip()
]

WEB_USERNAME = os.getenv("PACKMOUNT_USER", "admin")
WEB_PASSWORD = os.getenv("PACKMOUNT_PASSWORD", "change-me-now")
SMB_WORKGROUP = os.getenv("PACKMOUNT_SMB_WORKGROUP", "WORKGROUP")
SESSION_MAX_AGE = int(os.getenv("PACKMOUNT_SESSION_MAX_AGE", "86400"))
COMMAND_TIMEOUT = int(os.getenv("PACKMOUNT_COMMAND_TIMEOUT", "45"))

SAFE_SHARE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SAFE_MOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9_. -]{1,80}$")
NFS_SOURCE_RE = re.compile(r"^[A-Za-z0-9_.:-]+:/.*$")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_runtime_dirs() -> None:
    for path in [CONFIG_DIR, MOUNT_ROOT, REMOTE_ROOT, CREDENTIAL_ROOT]:
        path.mkdir(parents=True, exist_ok=True)
    try:
        (CONFIG_DIR / ".keep").touch(exist_ok=True)
    except Exception:
        pass


def load_or_create_text_secret(path: Path, length: int = 64) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o600)
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(length)
    with path.open("x", encoding="utf-8") as secret_file:
        os.fchmod(secret_file.fileno(), 0o600)
        secret_file.write(value)
    return value


def get_serializer() -> URLSafeTimedSerializer:
    secret = os.getenv("PACKMOUNT_SESSION_SECRET") or load_or_create_text_secret(SESSION_SECRET_PATH)
    return URLSafeTimedSerializer(secret_key=secret, salt="packmount-session")


def get_fernet() -> Fernet:
    FERNET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if FERNET_KEY_PATH.exists():
        FERNET_KEY_PATH.chmod(0o600)
        key = FERNET_KEY_PATH.read_bytes().strip()
        try:
            return Fernet(key)
        except Exception as exc:
            raise RuntimeError("Invalid Fernet key; restore /config/fernet.key from backup") from exc
    key = Fernet.generate_key()
    with FERNET_KEY_PATH.open("xb") as secret_file:
        os.fchmod(secret_file.fileno(), 0o600)
        secret_file.write(key)
    return Fernet(key)


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Cannot decrypt stored source password; check /config/fernet.key") from exc


@contextmanager
def db() -> sqlite3.Connection:
    ensure_runtime_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mounts (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_username TEXT,
                source_password_enc TEXT,
                mount_to TEXT NOT NULL UNIQUE,
                mount_name TEXT NOT NULL,
                automount INTEGER NOT NULL DEFAULT 0,
                read_only INTEGER NOT NULL DEFAULT 0,
                remote_mount_point TEXT,
                status TEXT NOT NULL DEFAULT 'Created',
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shares (
                id TEXT PRIMARY KEY,
                share_type TEXT NOT NULL,
                share_name TEXT NOT NULL,
                path TEXT NOT NULL,
                permission TEXT NOT NULL DEFAULT 'rw',
                allow_guest INTEGER NOT NULL DEFAULT 0,
                allow_network INTEGER NOT NULL DEFAULT 1,
                allowed_clients TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(id DESC)")


def add_log(level: str, message: str, details: Optional[Any] = None) -> None:
    try:
        details_text = json.dumps(details, ensure_ascii=True) if details is not None else None
        with db() as conn:
            conn.execute(
                "INSERT INTO logs(created_at, level, message, details) VALUES (?, ?, ?, ?)",
                (now_iso(), level.upper(), message, details_text),
            )
    except Exception:
        pass


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def run_cmd(args: List[str], timeout: int = COMMAND_TIMEOUT, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out after {timeout}s: {args[0]}") from exc
    if check and result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        raise RuntimeError(f"Command failed: {args[0]}: {detail}")
    return result


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def is_mounted(path: Path) -> bool:
    if not path.exists():
        return False
    result = subprocess.run(["mountpoint", "-q", str(path)], check=False)
    return result.returncode == 0


def safe_resolve_existing_or_parent(path: Path) -> Path:
    if path.exists():
        return path.resolve()
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.resolve()


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def assert_under_mount_root(path: Path) -> None:
    candidate = safe_resolve_existing_or_parent(path)
    if not is_under(candidate, MOUNT_ROOT):
        raise HTTPException(status_code=400, detail=f"Path must be under {MOUNT_ROOT}")


def assert_allowed_local_path(path: Path) -> None:
    candidate = safe_resolve_existing_or_parent(path)
    for root in ALLOWED_LOCAL_ROOTS:
        if is_under(candidate, root):
            return
    roots = ", ".join(str(p) for p in ALLOWED_LOCAL_ROOTS)
    raise HTTPException(status_code=400, detail=f"Local source must be under one of: {roots}")


def normalize_smb_path(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if not re.fullmatch(r"//[^/\s,=]+/[^/\s,=]+(?:/[^\s]*)?", value) or any(c in value for c in "\r\n\x00"):
        raise HTTPException(status_code=400, detail="SMB source must look like //server/share or //server/share/path")
    return value


def normalize_nfs_path(value: str) -> str:
    value = value.strip()
    if not NFS_SOURCE_RE.fullmatch(value) or any(c in value for c in "\r\n\x00, "):
        raise HTTPException(status_code=400, detail="NFS source must look like server:/export/path")
    return value


def sanitize_share_name(name: str) -> str:
    name = name.strip()
    if not SAFE_SHARE_RE.fullmatch(name) or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Share name may contain letters, numbers, dot, dash and underscore only")
    return name


def sanitize_mount_name(name: str) -> str:
    name = name.strip()
    if not SAFE_MOUNT_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Mount name contains unsupported characters")
    return name


def serialize_mount(row: sqlite3.Row) -> Dict[str, Any]:
    item = row_to_dict(row)
    item.pop("source_password_enc", None)
    mount_path = Path(item["mount_to"])
    mounted = is_mounted(mount_path)
    item["mounted"] = mounted
    if mounted:
        item["status"] = "Mounted"
    elif item.get("status") == "Mounted":
        item["status"] = "Stopped"
    return item


def serialize_share(row: sqlite3.Row) -> Dict[str, Any]:
    item = row_to_dict(row)
    item["enabled"] = bool(item["enabled"])
    item["allow_guest"] = bool(item["allow_guest"])
    item["allow_network"] = bool(item["allow_network"])
    return item


def update_mount_status(mount_id: str, status: str, error: Optional[str] = None) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE mounts SET status=?, last_error=?, updated_at=? WHERE id=?",
            (status, error, now_iso(), mount_id),
        )


def update_share_error(share_id: str, error: Optional[str]) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE shares SET last_error=?, updated_at=? WHERE id=?",
            (error, now_iso(), share_id),
        )


def remote_mount_path(mount_id: str) -> Path:
    return REMOTE_ROOT / mount_id


def credentials_file_path(mount_id: str) -> Path:
    return CREDENTIAL_ROOT / f"{mount_id}.credentials"


def mount_remote_source(record: Dict[str, Any]) -> Path:
    source_type = record["source_type"]
    source_path = record["source_path"]
    mount_id = record["id"]
    remote_path = Path(record.get("remote_mount_point") or str(remote_mount_path(mount_id)))
    remote_path.mkdir(parents=True, exist_ok=True)

    if is_mounted(remote_path):
        return remote_path

    if source_type == "smb":
        username = record.get("source_username") or "guest"
        password = decrypt_secret(record.get("source_password_enc"))
        cred_file = credentials_file_path(mount_id)
        cred_file.parent.mkdir(parents=True, exist_ok=True)
        if any(c in username + password for c in "\r\n\x00"):
            raise RuntimeError("SMB credentials must not contain newlines")
        with cred_file.open("w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(f"username={username}\npassword={password}\n")
        opts = [
            f"credentials={cred_file}",
            "iocharset=utf8",
            "vers=3.0",
            "file_mode=0775",
            "dir_mode=0775",
            "noperm",
        ]
        try:
            run_cmd(["mount", "-t", "cifs", source_path, str(remote_path), "-o", ",".join(opts)], timeout=60)
        finally:
            cred_file.unlink(missing_ok=True)
        return remote_path

    if source_type == "nfs":
        opts = "ro,hard,timeo=600,retrans=2" if int(record.get("read_only") or 0) else "rw,hard,timeo=600,retrans=2"
        run_cmd(["mount", "-t", "nfs", source_path, str(remote_path), "-o", opts], timeout=60)
        return remote_path

    raise RuntimeError(f"Unsupported remote source type: {source_type}")


def rar2fs_mount(record: Dict[str, Any]) -> None:
    mount_id = record["id"]
    source_type = record["source_type"]
    mount_to = Path(record["mount_to"])
    mount_to.mkdir(parents=True, exist_ok=True)

    if is_mounted(mount_to):
        update_mount_status(mount_id, "Mounted", None)
        return

    if source_type == "local":
        source = Path(record["source_path"])
        if not source.exists():
            raise RuntimeError(f"Local source does not exist: {source}")
    else:
        source = mount_remote_source(record)

    if not command_exists("rar2fs"):
        raise RuntimeError("rar2fs is not installed in this container")

    options = ["allow_other"]
    if int(record.get("read_only") or 0):
        options.append("ro")
    cmd = ["rar2fs", "-o", ",".join(options), str(source), str(mount_to)]
    run_cmd(cmd, timeout=90)
    update_mount_status(mount_id, "Mounted", None)
    add_log("INFO", f"Mounted {record['source_path']}", {"mount_to": str(mount_to)})


def try_unmount_path(path: Path) -> None:
    if not path.exists() or not is_mounted(path):
        return
    attempts = []
    if command_exists("fusermount"):
        attempts.append(["fusermount", "-u", str(path)])
    if command_exists("fusermount3"):
        attempts.append(["fusermount3", "-u", str(path)])
    attempts.extend([["umount", str(path)], ["umount", "-l", str(path)]])
    last_error = None
    for cmd in attempts:
        result = run_cmd(cmd, timeout=30, check=False)
        if result.returncode == 0:
            return
        last_error = (result.stderr or result.stdout or "unknown error").strip()
    raise RuntimeError(f"Could not unmount {path}: {last_error}")


def unmount_record(record: Dict[str, Any]) -> None:
    mount_id = record["id"]
    mount_to = Path(record["mount_to"])
    try_unmount_path(mount_to)
    if record["source_type"] in {"smb", "nfs"}:
        remote = Path(record.get("remote_mount_point") or str(remote_mount_path(mount_id)))
        try_unmount_path(remote)
    credentials_file_path(mount_id).unlink(missing_ok=True)
    update_mount_status(mount_id, "Stopped", None)
    add_log("INFO", f"Stopped mount {record['source_path']}", {"mount_to": str(mount_to)})


def samba_global_config() -> str:
    return f"""# Generated by PackMount. Manual changes may be overwritten.\n[global]\n   workgroup = {SMB_WORKGROUP}\n   server string = PackMount\n   security = user\n   map to guest = Bad User\n   guest account = nobody\n   server min protocol = SMB3\n   load printers = no\n   printing = bsd\n   disable spoolss = yes\n   log file = /var/log/samba/log.%m\n   max log size = 1000\n   smb ports = 445 139\n   unix extensions = no\n\n"""


def apply_samba_config() -> None:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM shares WHERE enabled=1 AND share_type='smb' ORDER BY created_at ASC"
        ).fetchall()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", SMB_WORKGROUP):
        raise ValueError("Invalid Samba workgroup")
    parts = [samba_global_config()]
    for row in rows:
        item = serialize_share(row)
        name = sanitize_share_name(item["share_name"])
        path = Path(item["path"])
        read_only = "no" if item["permission"] == "rw" else "yes"
        guest_ok = "yes" if item["allow_guest"] else "no"
        parts.append(f"[{name}]\n")
        parts.append(f"   path = {path}\n")
        parts.append("   browsable = yes\n")
        parts.append(f"   read only = {read_only}\n")
        parts.append(f"   guest ok = {guest_ok}\n")
        parts.append("   force user = " + os.getenv("PACKMOUNT_SAMBA_USER", "packmount") + "\n")
        parts.append("   create mask = 0664\n")
        parts.append("   directory mask = 0775\n")
        if not item["allow_network"]:
            parts.append("   hosts allow = 127. ::1\n")
        elif item.get("allowed_clients"):
            parts.append(f"   hosts allow = {item['allowed_clients']}\n")
        parts.append("\n")
    SMB_CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    SMB_CONF_PATH.write_text("".join(parts), encoding="utf-8")
    try:
        run_cmd(["testparm", "-s", str(SMB_CONF_PATH)], timeout=20)
    except Exception as exc:
        add_log("ERROR", "Samba config validation failed", str(exc))
        raise
    result = run_cmd(["smbcontrol", "all", "reload-config"], timeout=20, check=False)
    if result.returncode != 0:
        run_cmd(["smbd", "-D"], timeout=20, check=False)
    add_log("INFO", "Samba configuration reloaded")


def nfs_client_specs(share: Dict[str, Any]) -> List[str]:
    if not share["allow_network"]:
        return ["127.0.0.1"]
    allowed = (share.get("allowed_clients") or "*").strip()
    if not re.fullmatch(r"[A-Za-z0-9.*:/,_ -]+", allowed):
        raise ValueError("Invalid allowed clients (only hostnames, IP addresses and CIDR ranges)")
    clients = [c.strip() for c in re.split(r"[, ]+", allowed) if c.strip()]
    return clients or ["*"]


def nfs_fsid_for_share(share_id: str, index: int) -> int:
    value = int(uuid.UUID(share_id).int % 60000) + 1000
    return value + index


def apply_nfs_exports() -> None:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM shares WHERE enabled=1 AND share_type='nfs' ORDER BY created_at ASC"
        ).fetchall()
    lines = ["# Generated by PackMount. Manual changes may be overwritten.\n"]
    for index, row in enumerate(rows, start=1):
        item = serialize_share(row)
        path = Path(item["path"])
        mode = "rw" if item["permission"] == "rw" else "ro"
        opts = f"{mode},sync,no_subtree_check,root_squash,fsid={nfs_fsid_for_share(item['id'], index)}"
        for client in nfs_client_specs(item):
            lines.append(f"{path} {client}({opts})\n")
    NFS_EXPORTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    NFS_EXPORTS_PATH.write_text("".join(lines), encoding="utf-8")
    result = run_cmd(["exportfs", "-ra"], timeout=30, check=False)
    if result.returncode != 0:
        add_log("ERROR", "NFS export reload failed", result.stderr or result.stdout)
        raise RuntimeError((result.stderr or result.stdout or "exportfs failed").strip())
    add_log("INFO", "NFS exports reloaded")


def apply_all_shares() -> None:
    try:
        apply_samba_config()
    except Exception as exc:
        add_log("ERROR", "Could not apply Samba configuration", str(exc))
    try:
        apply_nfs_exports()
    except Exception as exc:
        add_log("ERROR", "Could not apply NFS exports", str(exc))


def restore_automounts() -> None:
    with db() as conn:
        rows = conn.execute("SELECT * FROM mounts WHERE automount=1 ORDER BY created_at ASC").fetchall()
    for row in rows:
        record = row_to_dict(row)
        try:
            rar2fs_mount(record)
        except Exception as exc:
            update_mount_status(record["id"], "Error", str(exc))
            add_log("ERROR", f"Automount failed for {record['source_path']}", str(exc))


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = True


class MountCreate(BaseModel):
    source_type: str = Field(pattern="^(local|smb|nfs)$")
    source_path: str = Field(min_length=1, max_length=4096)
    username: Optional[str] = Field(default=None, max_length=256)
    password: Optional[str] = Field(default=None, max_length=4096)
    mount_to: str = Field(min_length=1, max_length=4096)
    mount_name: str = Field(min_length=1, max_length=80)
    automount: bool = True
    read_only: bool = False

    @field_validator("source_path", "mount_to", "mount_name", mode="before")
    @classmethod
    def strip_values(cls, value: str) -> str:
        return value.strip()


class ShareCreate(BaseModel):
    share_type: str = Field(pattern="^(smb|nfs)$")
    share_name: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=4096)
    permission: str = Field(default="rw", pattern="^(rw|ro)$")
    allow_guest: bool = False
    allow_network: bool = True
    allowed_clients: Optional[str] = Field(default=None, max_length=512)

    @field_validator("share_name", "path", mode="before")
    @classmethod
    def strip_values(cls, value: str) -> str:
        return value.strip()


def require_user(request: Request) -> str:
    token = request.cookies.get("packmount_session")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        data = get_serializer().loads(token, max_age=SESSION_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="Session expired")
    except BadSignature:
        raise HTTPException(status_code=401, detail="Invalid session")
    username = data.get("sub")
    if username != WEB_USERNAME:
        raise HTTPException(status_code=401, detail="Invalid session")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin:
            host = request.headers.get("host", "")
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc != host:
                raise HTTPException(status_code=403, detail="Cross-origin request blocked")
    return username


@app.on_event("startup")
def on_startup() -> None:
    ensure_runtime_dirs()
    if WEB_PASSWORD in {"change-me-now", "admin", "packmount", "", "REPLACE_WITH_STRONG_UNIQUE_PASSWORD"}:
        raise RuntimeError("Set a unique PACKMOUNT_PASSWORD in .env before starting")
    init_db()
    apply_all_shares()
    restore_automounts()
    add_log("INFO", "PackMount started", {"version": APP_VERSION})


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/login")
def login(payload: LoginRequest, response: Response) -> Dict[str, Any]:
    ok_user = secrets.compare_digest(payload.username, WEB_USERNAME)
    ok_pass = secrets.compare_digest(payload.password, WEB_PASSWORD)
    if not (ok_user and ok_pass):
        add_log("WARN", "Failed login attempt", {"username": payload.username})
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = get_serializer().dumps({"sub": WEB_USERNAME, "iat": int(time.time())})
    max_age = SESSION_MAX_AGE if payload.remember else None
    response.set_cookie(
        "packmount_session",
        token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("PACKMOUNT_SECURE_COOKIE", "false").lower() == "true",
        max_age=max_age,
    )
    add_log("INFO", "User logged in", {"username": payload.username})
    return {"ok": True, "username": WEB_USERNAME}


@app.post("/api/logout")
def logout(response: Response, user: str = Depends(require_user)) -> Dict[str, Any]:
    response.delete_cookie("packmount_session")
    add_log("INFO", "User logged out", {"username": user})
    return {"ok": True}


@app.get("/api/me")
def me(user: str = Depends(require_user)) -> Dict[str, Any]:
    return {"username": user, "version": APP_VERSION}


@app.get("/api/system")
def system(user: str = Depends(require_user)) -> Dict[str, Any]:
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "mount_root": str(MOUNT_ROOT),
        "allowed_local_roots": [str(p) for p in ALLOWED_LOCAL_ROOTS],
        "tools": {
            "rar2fs": command_exists("rar2fs"),
            "mount.cifs": command_exists("mount.cifs"),
            "mount.nfs": command_exists("mount.nfs"),
            "smbd": command_exists("smbd"),
            "exportfs": command_exists("exportfs"),
            "fusermount": command_exists("fusermount") or command_exists("fusermount3"),
        },
    }


@app.get("/api/mounts")
def list_mounts(user: str = Depends(require_user)) -> Dict[str, Any]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM mounts ORDER BY created_at DESC").fetchall()
    return {"items": [serialize_mount(row) for row in rows]}


@app.post("/api/mounts")
def create_mount(payload: MountCreate, user: str = Depends(require_user)) -> Dict[str, Any]:
    mount_id = str(uuid.uuid4())
    source_type = payload.source_type
    mount_name = sanitize_mount_name(payload.mount_name)
    mount_to = Path(payload.mount_to)
    if not mount_to.is_absolute():
        raise HTTPException(status_code=400, detail="Mount path must be absolute")
    assert_under_mount_root(mount_to)
    if mount_to.resolve() == MOUNT_ROOT or mount_to.resolve().parent != MOUNT_ROOT:
        raise HTTPException(status_code=400, detail="Mount destination must be a direct child of the mount root")
    source_path = payload.source_path
    remote_point = None

    if source_type == "local":
        source = Path(source_path)
        if not source.is_absolute():
            raise HTTPException(status_code=400, detail="Local source path must be absolute")
        assert_allowed_local_path(source)
        if not source.exists() or not source.is_dir():
            raise HTTPException(status_code=400, detail=f"Local source does not exist or is not a directory: {source}")
        source_path = str(source)
    elif source_type == "smb":
        source_path = normalize_smb_path(source_path)
        remote_point = str(remote_mount_path(mount_id))
    elif source_type == "nfs":
        source_path = normalize_nfs_path(source_path)
        remote_point = str(remote_mount_path(mount_id))

    record = {
        "id": mount_id,
        "source_type": source_type,
        "source_path": source_path,
        "source_username": payload.username or None,
        "source_password_enc": encrypt_secret(payload.password),
        "mount_to": str(mount_to),
        "mount_name": mount_name,
        "automount": 1 if payload.automount else 0,
        "read_only": 1 if payload.read_only else 0,
        "remote_mount_point": remote_point,
        "status": "Created",
        "last_error": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO mounts(id, source_type, source_path, source_username, source_password_enc, mount_to, mount_name,
                                   automount, read_only, remote_mount_point, status, last_error, created_at, updated_at)
                VALUES(:id, :source_type, :source_path, :source_username, :source_password_enc, :mount_to, :mount_name,
                       :automount, :read_only, :remote_mount_point, :status, :last_error, :created_at, :updated_at)
                """,
                record,
            )
        rar2fs_mount(record)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Mount path is already registered") from exc
    except Exception as exc:
        update_mount_status(mount_id, "Error", str(exc))
        add_log("ERROR", f"Mount failed for {source_path}", str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    with db() as conn:
        row = conn.execute("SELECT * FROM mounts WHERE id=?", (mount_id,)).fetchone()
    return {"item": serialize_mount(row)}


@app.post("/api/mounts/{mount_id}/start")
def start_mount(mount_id: str, user: str = Depends(require_user)) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM mounts WHERE id=?", (mount_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mount not found")
    record = row_to_dict(row)
    try:
        rar2fs_mount(record)
    except Exception as exc:
        update_mount_status(mount_id, "Error", str(exc))
        add_log("ERROR", f"Start failed for {record['source_path']}", str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    with db() as conn:
        row = conn.execute("SELECT * FROM mounts WHERE id=?", (mount_id,)).fetchone()
    return {"item": serialize_mount(row)}


@app.post("/api/mounts/{mount_id}/stop")
def stop_mount(mount_id: str, user: str = Depends(require_user)) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM mounts WHERE id=?", (mount_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mount not found")
    record = row_to_dict(row)
    try:
        unmount_record(record)
    except Exception as exc:
        update_mount_status(mount_id, "Error", str(exc))
        add_log("ERROR", f"Stop failed for {record['source_path']}", str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    with db() as conn:
        row = conn.execute("SELECT * FROM mounts WHERE id=?", (mount_id,)).fetchone()
    return {"item": serialize_mount(row)}


@app.delete("/api/mounts/{mount_id}")
def delete_mount(mount_id: str, user: str = Depends(require_user)) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM mounts WHERE id=?", (mount_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mount not found")
    record = row_to_dict(row)
    try:
        unmount_record(record)
    except Exception as exc:
        add_log("WARN", "Unmount during delete failed", str(exc))
    with db() as conn:
        conn.execute("DELETE FROM mounts WHERE id=?", (mount_id,))
        conn.execute("DELETE FROM shares WHERE path=?", (record["mount_to"],))
    apply_all_shares()
    add_log("INFO", f"Deleted mount {record['mount_name']}")
    return {"ok": True}


@app.get("/api/shares")
def list_shares(user: str = Depends(require_user)) -> Dict[str, Any]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM shares ORDER BY created_at DESC").fetchall()
    return {"items": [serialize_share(row) for row in rows]}


@app.post("/api/shares")
def create_share(payload: ShareCreate, user: str = Depends(require_user)) -> Dict[str, Any]:
    share_id = str(uuid.uuid4())
    share_name = sanitize_share_name(payload.share_name)
    share_path = Path(payload.path)
    if not share_path.is_absolute():
        raise HTTPException(status_code=400, detail="Share path must be absolute")
    assert_under_mount_root(share_path)
    with db() as conn:
        known_mounts = {row[0] for row in conn.execute("SELECT mount_to FROM mounts").fetchall()}
    if str(share_path) not in known_mounts:
        raise HTTPException(status_code=400, detail="Share path must refer to a registered mount")
    if not share_path.exists() or not share_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Share path does not exist or is not a directory: {share_path}")

    if payload.allowed_clients and not re.fullmatch(r"[A-Za-z0-9.*:/,_ -]+", payload.allowed_clients):
        raise HTTPException(status_code=400, detail="Invalid allowed clients")
    record = {
        "id": share_id,
        "share_type": payload.share_type,
        "share_name": share_name,
        "path": str(share_path),
        "permission": payload.permission,
        "allow_guest": 1 if payload.allow_guest else 0,
        "allow_network": 1 if payload.allow_network else 0,
        "allowed_clients": payload.allowed_clients.strip() if payload.allowed_clients else None,
        "enabled": 1,
        "last_error": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO shares(id, share_type, share_name, path, permission, allow_guest, allow_network,
                                   allowed_clients, enabled, last_error, created_at, updated_at)
                VALUES(:id, :share_type, :share_name, :path, :permission, :allow_guest, :allow_network,
                       :allowed_clients, :enabled, :last_error, :created_at, :updated_at)
                """,
                record,
            )
        if payload.share_type == "smb":
            apply_samba_config()
        else:
            apply_nfs_exports()
        add_log("INFO", f"Enabled {payload.share_type.upper()} share {share_name}", {"path": str(share_path)})
    except Exception as exc:
        update_share_error(share_id, str(exc))
        add_log("ERROR", f"Share activation failed for {share_name}", str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    with db() as conn:
        row = conn.execute("SELECT * FROM shares WHERE id=?", (share_id,)).fetchone()
    return {"item": serialize_share(row)}


@app.post("/api/shares/{share_id}/toggle")
def toggle_share(share_id: str, user: str = Depends(require_user)) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM shares WHERE id=?", (share_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Share not found")
        new_enabled = 0 if row["enabled"] else 1
        conn.execute(
            "UPDATE shares SET enabled=?, updated_at=? WHERE id=?",
            (new_enabled, now_iso(), share_id),
        )
    apply_all_shares()
    with db() as conn:
        row = conn.execute("SELECT * FROM shares WHERE id=?", (share_id,)).fetchone()
    add_log("INFO", f"{'Enabled' if new_enabled else 'Disabled'} share {row['share_name']}")
    return {"item": serialize_share(row)}


@app.delete("/api/shares/{share_id}")
def delete_share(share_id: str, user: str = Depends(require_user)) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM shares WHERE id=?", (share_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Share not found")
        conn.execute("DELETE FROM shares WHERE id=?", (share_id,))
    apply_all_shares()
    add_log("INFO", f"Deleted share {row['share_name']}")
    return {"ok": True}


@app.get("/api/logs")
def logs(limit: int = 100, user: str = Depends(require_user)) -> Dict[str, Any]:
    limit = min(max(limit, 1), 500)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"items": [row_to_dict(row) for row in rows]}


@app.get("/api/path")
def browse_path(path: Optional[str] = None, user: str = Depends(require_user)) -> Dict[str, Any]:
    selected = Path(path or str(ALLOWED_LOCAL_ROOTS[0] if ALLOWED_LOCAL_ROOTS else "/"))
    if not selected.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute")
    assert_allowed_local_path(selected)
    if not selected.exists() or not selected.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    entries = []
    try:
        for child in sorted(selected.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.is_dir():
                entries.append({"name": child.name, "path": str(child), "type": "directory"})
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    parent = selected.parent if selected != selected.parent else selected
    return {
        "path": str(selected),
        "parent": str(parent),
        "roots": [str(p) for p in ALLOWED_LOCAL_ROOTS],
        "entries": entries,
    }
