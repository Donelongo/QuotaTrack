# PyInstaller recipe for the packaged app: QuotaTrack.exe on Windows, QuotaTrack.app on macOS.
# Build it from the repo folder with:  pip install pyinstaller  then  pyinstaller packaging/QuotaTrack.spec
# The app then runs with no Python installed, no console window and (on macOS) no Dock icon.

import ast
import glob
import os
import sys

ROOT = os.path.dirname(SPECPATH)
PROVIDERS = sorted(glob.glob(os.path.join(ROOT, "providers", "*.py")))


def provider_imports():
    """Providers are loaded from their files at run time, so PyInstaller can't see what they import; list it."""
    names = set()
    for path in PROVIDERS:
        with open(path, encoding="utf-8") as f:
            for node in ast.walk(ast.parse(f.read())):
                if isinstance(node, ast.Import):
                    names.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names.add(node.module)
    local = {os.path.splitext(os.path.basename(p))[0] for p in PROVIDERS}
    return sorted(n for n in names if n.split(".")[0] not in local)


a = Analysis(
    [os.path.join(ROOT, "quotatrack.pyw")],
    pathex=[ROOT],
    datas=[(p, "providers") for p in PROVIDERS],
    hiddenimports=provider_imports(),
    excludes=["pytest", "unittest", "pydoc"],
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="QuotaTrack", console=False)
    app = COLLECT(exe, a.binaries, a.datas, name="QuotaTrack")
    app = BUNDLE(
        app,
        name="QuotaTrack.app",
        bundle_identifier="com.quotatrack",
        info_plist={
            "CFBundleDisplayName": "QuotaTrack",
            "LSUIElement": True,  # a background app: no Dock icon, no menu bar
            "NSHighResolutionCapable": True,
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="QuotaTrack", console=False, upx=False)
