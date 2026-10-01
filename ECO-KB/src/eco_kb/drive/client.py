import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from eco_kb.drive.extract import GOOGLE_DOC, GOOGLE_FOLDER

log = logging.getLogger("eco_kb.drive")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime: str
    modified: str
    md5: str
    parts: tuple[str, ...]  # nombres de carpeta desde la raíz hasta el archivo (sin incluir la raíz)


class DriveClient(Protocol):
    def list_files(self, root_id: str) -> list[DriveFile]: ...
    def download(self, f: DriveFile) -> bytes: ...


def load_credentials(token_path: str) -> Credentials:
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        Path(token_path).write_text(creds.to_json(), encoding="utf-8")
    return creds


class GoogleDriveClient:
    def __init__(self, creds: Credentials):
        self._svc = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _children(self, folder_id: str) -> list[dict]:
        out, token = [], None
        while True:
            res = self._svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, md5Checksum)",
                pageSize=200, pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            out += res.get("files", [])
            token = res.get("nextPageToken")
            if not token:
                return out

    def list_files(self, root_id: str) -> list[DriveFile]:
        files: list[DriveFile] = []
        stack: list[tuple[str, tuple[str, ...]]] = [(root_id, ())]
        while stack:
            folder, parts = stack.pop()
            for item in self._children(folder):
                if item["mimeType"] == GOOGLE_FOLDER:
                    stack.append((item["id"], (*parts, item["name"])))
                else:
                    files.append(DriveFile(item["id"], item["name"], item["mimeType"],
                                           item.get("modifiedTime", ""), item.get("md5Checksum", ""), parts))
        return files

    def download(self, f: DriveFile) -> bytes:
        if f.mime == GOOGLE_DOC:
            try:
                return self._svc.files().export(fileId=f.id, mimeType="text/markdown").execute()
            except Exception:  # export markdown no disponible: texto plano
                log.warning("export markdown falló para %s; usando text/plain", f.name)
                return self._svc.files().export(fileId=f.id, mimeType="text/plain").execute()
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, self._svc.files().get_media(fileId=f.id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue()
