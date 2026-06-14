from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ctypes
import sys
from typing import Callable

from PySide6.QtCore import QLockFile, QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket


@dataclass(frozen=True)
class SingleInstanceConfig:
    app_id: str
    runtime_dir: Path


class SingleInstanceService(QObject):
    def __init__(
        self,
        app_id: str,
        runtime_dir: Path,
        on_activate: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.config = SingleInstanceConfig(app_id=app_id, runtime_dir=runtime_dir)
        self.on_activate = on_activate
        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._handle_new_connection)
        self._server_name = self.config.app_id
        self._lock_path = self.config.runtime_dir / f"{self.config.app_id}.lock"
        self._lock: QLockFile | None = None
        self._mutex_handle = None
        self._mutex_name = f"Local\\{self.config.app_id}"
        if not sys.platform.startswith("win"):
            self._lock = QLockFile(str(self._lock_path))
            self._lock.setStaleLockTime(0)
        self._is_primary = False
        self._kernel32 = None
        if sys.platform.startswith("win"):
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def set_on_activate(self, on_activate: Callable[[], None]) -> None:
        self.on_activate = on_activate

    def acquire_or_activate(self) -> bool:
        if self._try_acquire_primary():
            QLocalServer.removeServer(self._server_name)
            if not self.server.listen(self._server_name):
                self.close()
                return False
            self._is_primary = True
            return True

        self._notify_existing_instance()
        return False

    def _try_acquire_primary(self) -> bool:
        if self._kernel32 is not None:
            error_already_exists = 183
            handle = self._kernel32.CreateMutexW(None, True, self._mutex_name)
            if not handle:
                return False
            last_error = ctypes.get_last_error()
            if last_error == error_already_exists:
                self._kernel32.CloseHandle(handle)
                return False
            self._mutex_handle = handle
            return True

        if self._lock is None:
            return False
        return self._lock.tryLock(0)

    def _notify_existing_instance(self) -> None:
        existing = QLocalSocket(self)
        existing.connectToServer(self._server_name, QLocalSocket.WriteOnly)
        if existing.waitForConnected(300):
            existing.write(b"activate")
            existing.flush()
            existing.waitForBytesWritten(300)
            existing.disconnectFromServer()
            existing.waitForDisconnected(300)

    def _handle_new_connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            socket.readyRead.connect(lambda sock=socket: self._handle_socket_ready(sock))
            if socket.bytesAvailable() > 0:
                self._handle_socket_ready(socket)

    def _handle_socket_ready(self, socket: QLocalSocket) -> None:
        _ = socket.readAll()
        socket.disconnectFromServer()
        socket.close()
        if self.on_activate is not None:
            QTimer.singleShot(0, self.on_activate)

    def close(self) -> None:
        if self.server.isListening():
            self.server.close()
        QLocalServer.removeServer(self._server_name)
        if self._mutex_handle is not None and self._kernel32 is not None:
            self._kernel32.ReleaseMutex(self._mutex_handle)
            self._kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None
        if self._lock is not None and self._lock.isLocked():
            self._lock.unlock()
        self._is_primary = False
