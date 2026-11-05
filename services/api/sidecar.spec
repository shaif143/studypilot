# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the bundled backend.

Produces a single self-contained ARM64 executable so the installed macOS app
has no dependency on a Python interpreter, a virtual environment, Docker, or
the user's PATH.

A one-*file* build is used rather than the faster-starting one-directory
layout. Tauri's ``externalBin`` copies the single named executable into
``Contents/MacOS``, whereas a one-directory build also needs its ``_internal``
folder sitting beside that executable — which the bundler would place in
``Contents/Resources`` instead, leaving the sidecar unable to find its own
libraries. One file keeps the contract simple and signs as a single Mach-O
object; the cost is a short extraction on first launch, which the Rust shell
already waits for before showing the window.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []

# Packages that load code or data dynamically and therefore cannot be found by
# static analysis alone.
for package in (
    "qdrant_client",
    "pymupdf",
    "fitz",
    "pymupdf_fonts",  # FiraGO etc., loaded by name when drawing slide text
    "pptx",  # python-pptx ships its default template and XML schemas as data
    "langgraph",
    "langchain_core",
):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception:  # pragma: no cover - optional at build time
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# uvicorn resolves its protocol and loop implementations by name at runtime.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # SQLAlchemy picks its DBAPI dialect by name.
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
]

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Large optional dependencies that the default configuration never imports.
    # If a user installs them, this build simply will not include them.
    excludes=[
        "tkinter",
        "matplotlib",
        "IPython",
        "pytest",
        "sentence_transformers",
        "torch",
        "transformers",
        # qdrant-client pulls onnxruntime for its optional fastembed feature.
        # Local mode never uses it, and it costs ~18 MB in the bundle.
        "onnxruntime",
        "fastembed",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="studypilot-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
