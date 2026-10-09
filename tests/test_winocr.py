import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from idleclicker import winocr
from idleclicker.backend import Image
from idleclicker.winocr import (OcrUnavailable, WindowsOcr, encode_bmp, parse_plus_number,
                                upscale_factor)


class ParseTests(unittest.TestCase):
    def test_reads_the_number(self):
        cases = {
            "+5": 5, "+ 5": 5, "5": 5, "+50": 50, "+1 2": 12, "+12 Level": 12,
            "2254 Level +3": 3,  # the + one wins over other numbers
            "+S": 5, "+ S": 5, "+1O": 10, "t5": 5,  # common OCR misreads
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_plus_number(text), expected)

    def test_no_number(self):
        for text in ("", "Level", "lvl", "+", "MAX"):
            with self.subTest(text=text):
                self.assertIsNone(parse_plus_number(text))


class BitmapTests(unittest.TestCase):
    def test_header_size_and_row_padding(self):
        image = Image(5, 3, bytes(5 * 3 * 4))
        data = encode_bmp(image, scale=2)
        self.assertEqual(data[:2], b"BM")
        width, height = struct.unpack_from("<ii", data, 18)
        self.assertEqual((width, height), (10, 6))
        row = (10 * 3 + 3) & ~3
        self.assertEqual(len(data), 54 + row * 6)

    def test_bottom_row_first_and_bgr_order(self):
        # 1x2 image: top pixel red, bottom pixel blue.
        image = Image(1, 2, bytes([0, 0, 255, 0, 255, 0, 0, 0]))
        data = encode_bmp(image)
        self.assertEqual(data[54:57], bytes([255, 0, 0]))   # bottom (blue) row stored first
        self.assertEqual(data[58:61], bytes([0, 0, 255]))   # then the top (red) row

    def test_small_captures_are_enlarged(self):
        self.assertEqual(upscale_factor(Image(30, 20, b"")), 5)
        self.assertEqual(upscale_factor(Image(30, 10, b"")), 6)
        self.assertEqual(upscale_factor(Image(30, 200, b"")), 1)


class WindowsOcrTests(unittest.TestCase):
    def test_missing_powershell_is_reported(self):
        ocr = WindowsOcr(powershell=Path(tempfile.gettempdir()) / "no-such-powershell.exe")
        self.addCleanup(ocr.cleanup)
        with self.assertRaisesRegex(OcrUnavailable, "PowerShell wasn't found"):
            ocr.read(Image(1, 1, bytes(4)))


@unittest.skipUnless(shutil.which("pwsh"), "needs PowerShell (pwsh) to run the OCR script's loop")
class PowerShellProtocolTests(unittest.TestCase):
    """Runs the real PowerShell script with the Windows-only OCR calls swapped for a stand-in
    that "reads" the file's size, to test the conversation between Python and PowerShell."""

    def stub_script(self):
        s = winocr._SCRIPT
        setup_start = s.index("try {\n    Add-Type")
        setup_end = s.index("exit 1\n}\n") + len("exit 1\n}\n")
        s = s[:setup_start] + "$asTask = $null\n" + s[setup_end:]
        ocr_start = s.index("        $file = Await")
        ocr_end = s.index("        Send ('OK '")
        return (s[:ocr_start]
                + "        $result = @{ Lines = @(@{ Text = '+' + (Get-Item -LiteralPath $path).Length }) }\n"
                + s[ocr_end:])

    def test_reads_restarts_and_reports_errors(self):
        image = Image(3, 2, bytes(24))
        expected = f"+{len(encode_bmp(image, upscale_factor(image)))}"
        ocr = WindowsOcr(powershell=Path(shutil.which("pwsh")), script=self.stub_script())
        self.addCleanup(ocr.cleanup)
        self.assertEqual(ocr.read(image), expected)
        self.assertEqual(ocr.read(image), expected)        # same process
        ocr.close()
        self.assertEqual(ocr.read(image), expected)        # restarted after close()

    def test_setup_failure_is_reported(self):
        # On a machine without Windows' OCR, the real script fails setup with a reason.
        ocr = WindowsOcr(powershell=Path(shutil.which("pwsh")))
        self.addCleanup(ocr.cleanup)
        if Path("C:/Windows").exists():
            self.skipTest("this is Windows; the real OCR may work")
        with self.assertRaisesRegex(OcrUnavailable, "isn't available"):
            ocr.read(Image(1, 1, bytes(4)))


if __name__ == "__main__":
    unittest.main()
