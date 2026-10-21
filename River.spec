# Only explicitly listed public resources enter the app bundle.
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
analysis = Analysis(
    [str(root / 'desktop.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'static'), 'static'),
           (str(root / 'draft-plan.json'), '.'),
           (str(root / 'backlog-config.json'), '.')] + collect_data_files('tzdata'),
    hiddenimports=['rumps', 'AppKit', 'Foundation'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name='River After School',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, argv_emulation=False, target_arch='universal2',
    codesign_identity=os.environ.get('RIVER_CODESIGN_IDENTITY'), entitlements_file=None,
)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='River After School')
app = BUNDLE(
    collection, name='River After School.app', bundle_identifier='co.dayinthecountry.riverafterschool',
    info_plist={
        'CFBundleShortVersionString': '1.1.0', 'CFBundleVersion': '2',
        'LSUIElement': True, 'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True,
    },
)
