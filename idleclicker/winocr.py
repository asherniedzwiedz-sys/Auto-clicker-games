"""Reading the "+N" number on an upgrade button with the text recognition (OCR)
built into Windows 10/11.

The OCR engine (Windows.Media.Ocr) is reached through a background Windows
PowerShell process running the script below, so there is nothing to install.
Python saves each screen capture as a .bmp file in a temporary folder, sends
"read <file name>" on one line, and reads back one line: "OK <text>" or
"ERR <reason>".
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional

from .backend import Image

# The capture folder is passed as a command-line argument, so any characters in the
# user's temp path survive; only plain ASCII file names cross the pipe.
_SCRIPT = r"""
param([string]$Folder)
$ErrorActionPreference = 'Stop'
function Send([string]$line) {
    [Console]::Out.WriteLine(($line -replace '[^\x20-\x7E]', ' '))
    [Console]::Out.Flush()
}
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) { throw 'Windows has no text-recognition (OCR) language installed' }
} catch {
    Send ('ERR ' + $_.Exception.Message)
    exit 1
}

function Await($operation, [Type]$resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $null = $task.Wait(-1)
    $task.Result
}

Send 'READY'
while ($true) {
    $command = [Console]::In.ReadLine()
    if ($null -eq $command) { break }
    $request = [regex]::Match($command, '^read ([A-Za-z0-9_.-]+)$')
    if (-not $request.Success) { break }
    try {
        $path = Join-Path $Folder $request.Groups[1].Value
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        try {
            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        } finally {
            try { $stream.Dispose() } catch { }
        }
        Send ('OK ' + (($result.Lines | ForEach-Object { $_.Text }) -join ' '))
    } catch {
        Send ('ERR ' + $_.Exception.Message)
    }
}
"""


class OcrUnavailable(RuntimeError):
    """Windows' text recognition can't be used (or stopped working)."""


def powershell_path() -> Path:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return Path(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


class WindowsOcr:
    """Reads text from screen captures. Thread-safe; the PowerShell process starts on first use."""

    def __init__(self, powershell: Optional[Path] = None, script: str = _SCRIPT) -> None:
        self._powershell = Path(powershell) if powershell else powershell_path()
        self._script = script
        try:
            self._tmp = tempfile.TemporaryDirectory(prefix="idleclicker-ocr-", ignore_cleanup_errors=True)
        except TypeError:  # Python < 3.10
            self._tmp = tempfile.TemporaryDirectory(prefix="idleclicker-ocr-")
        self._dir = Path(self._tmp.name)
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._count = 0

    def read(self, image: Image) -> str:
        with self._lock:
            proc = self._process()
            self._count += 1
            name = f"capture{self._count}.bmp"
            (self._dir / name).write_bytes(encode_bmp(image, upscale_factor(image)))
            try:
                proc.stdin.write(f"read {name}\n")
                proc.stdin.flush()
                reply = proc.stdout.readline().strip()
            except (OSError, ValueError):
                reply = ""
            self._delete_old_captures(keep=name)
        if reply.startswith("OK"):
            return reply[2:].strip()
        if reply.startswith("ERR"):
            raise OcrUnavailable(reply[3:].strip())
        self.close()
        raise OcrUnavailable("Windows text recognition stopped unexpectedly")

    def close(self) -> None:
        """Stop the PowerShell process. Safe to call from another thread to abort a stuck read."""
        proc, self._proc = self._proc, None
        if proc is not None:
            _stop(proc)

    def cleanup(self) -> None:
        """Stop the process and delete the temporary folder. Call once, when finished."""
        self.close()
        try:
            self._tmp.cleanup()
        except OSError:
            pass

    def _process(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        if not self._powershell.exists():
            raise OcrUnavailable(f"Windows PowerShell wasn't found at {self._powershell}")
        script = self._dir / "read_number.ps1"
        script.write_text(self._script, encoding="ascii")
        proc = subprocess.Popen(
            [str(self._powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-Folder", str(self._dir)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="ascii", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        first = proc.stdout.readline().strip()
        if first != "READY":
            _stop(proc)
            reason = first[3:].strip() if first.startswith("ERR") else "it didn't start"
            raise OcrUnavailable(f"Windows text recognition isn't available: {reason}")
        self._proc = proc
        return proc

    def _delete_old_captures(self, keep: str) -> None:
        for old in self._dir.glob("capture*.bmp"):
            if old.name != keep:
                try:
                    old.unlink()
                except OSError:
                    pass  # still open in PowerShell; next time


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()  # first, so a read blocked on its output returns
    for stream in (proc.stdin, proc.stdout):
        try:
            stream.close()
        except (OSError, ValueError):
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def upscale_factor(image: Image) -> int:
    """OCR copes badly with tiny text, so blow small captures up to ~100 px tall."""
    return max(1, min(6, 100 // max(1, image.height)))


def encode_bmp(image: Image, scale: int = 1) -> bytes:
    """A 24-bit .bmp of the image, enlarged ``scale`` times (nearest neighbour)."""
    width, height = image.width * scale, image.height * scale
    row_size = (width * 3 + 3) & ~3
    header = struct.pack("<2sIHHI", b"BM", 54 + row_size * height, 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, row_size * height, 2835, 2835, 0, 0)
    padding = b"\0" * (row_size - width * 3)
    rows = []
    for y in range(image.height - 1, -1, -1):  # BMP rows go bottom to top
        start = y * image.width * 4
        row = b"".join(image.bgra[start + 4 * x:start + 4 * x + 3] * scale for x in range(image.width))
        rows.extend([row + padding] * scale)
    return header + info + b"".join(rows)


# Characters OCR commonly returns instead of digits.
_LOOKALIKES = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "l": "1", "I": "1", "i": "1",
                             "|": "1", "!": "1", "Z": "2", "z": "2", "S": "5", "s": "5", "B": "8"})


def parse_plus_number(text: str) -> Optional[int]:
    """The number in OCR text like '+12'. The '+' is optional: OCR often drops or mangles it.

    Lookalike letters only count as digits inside something number-like (a word with a
    digit in it, or one starting with or following a '+'), so a stray word such as
    "Level" isn't read as a number.
    """
    kept = []
    after_plus = False
    for word in text.split():
        if after_plus or word.startswith("+") or re.search(r"\d", word):
            kept.append(word.translate(_LOOKALIKES))
        after_plus = word == "+"
    cleaned = re.sub(r"(?<=\d)\s+(?=\d)", "", " ".join(kept))
    match = re.search(r"\+\s*(\d+)", cleaned) or re.search(r"(\d+)", cleaned)
    return int(match.group(1)) if match else None
