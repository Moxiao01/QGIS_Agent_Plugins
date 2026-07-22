import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Run QGIS Agent integration probe in QGIS Python.")
    parser.add_argument("--qgis-root", default=os.environ.get("QGIS_ROOT", ""))
    args = parser.parse_args()
    if not args.qgis_root:
        parser.error("provide --qgis-root or set QGIS_ROOT")

    root = Path(args.qgis_root).resolve()
    python_exe = root / "bin" / "python.exe"
    if not python_exe.is_file():
        raise SystemExit(f"QGIS Python not found: {python_exe}")

    repo_root = Path(__file__).resolve().parents[1]
    probe = Path(__file__).resolve().with_name("qgis_integration_probe.py")
    env = {key: value for key, value in os.environ.items() if key.casefold() != "path"}
    qgis_prefix = root / "apps" / "qgis-ltr"
    env.update({
        "OSGEO4W_ROOT": str(root),
        "PYTHONHOME": str(root / "apps" / "Python312"),
        "PYTHONPATH": os.pathsep.join([
            str(qgis_prefix / "python"),
            str(qgis_prefix / "python" / "plugins"),
            str(repo_root),
        ]),
        "QGIS_PREFIX_PATH": str(qgis_prefix).replace("\\", "/"),
        "QT_PLUGIN_PATH": os.pathsep.join([
            str(qgis_prefix / "qtplugins"),
            str(root / "apps" / "Qt5" / "plugins"),
        ]),
        "GDAL_DATA": str(root / "apps" / "gdal" / "share" / "gdal"),
        "PROJ_DATA": str(root / "share" / "proj"),
        "PATH": os.pathsep.join([
            str(qgis_prefix / "bin"),
            str(root / "apps" / "Qt5" / "bin"),
            str(root / "apps" / "Python312" / "Scripts"),
            str(root / "bin"),
            str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "system32"),
            os.environ.get("SystemRoot", r"C:\Windows"),
        ]),
    })

    with tempfile.TemporaryDirectory(prefix="qgis-agent-probe-") as profile_dir:
        env["QGIS_CUSTOM_CONFIG_PATH"] = profile_dir
        env["TEMP"] = profile_dir
        env["TMP"] = profile_dir
        result = subprocess.run(
            [str(python_exe), str(probe)],
            cwd=str(repo_root),
            env=env,
            text=True,
            capture_output=True,
        )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
