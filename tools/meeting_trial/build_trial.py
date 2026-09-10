"""Build an isolated meeting configuration using the official ESP-IDF tools."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idf", type=Path, default=Path(os.environ["IDF_PATH"]) if os.environ.get("IDF_PATH") else None,
                        help="ESP-IDF directory; defaults to IDF_PATH")
    parser.add_argument("--tools", type=Path, default=Path(os.environ.get("IDF_TOOLS_PATH", str(Path.home() / ".espressif"))),
                        help="ESP-IDF tools; defaults to IDF_TOOLS_PATH or ~/.espressif")
    parser.add_argument("--normal", action="store_true", help="Validate the normal badge build in a separate directory")
    parser.add_argument("--companion", action="store_true", help="Build the independent Android companion firmware")
    args = parser.parse_args()
    if args.idf is None or not (args.idf / "tools/idf.py").is_file():
        parser.error("Set IDF_PATH or pass --idf pointing to an installed ESP-IDF v5.5.3 checkout")
    root = Path(__file__).resolve().parents[2]
    if args.normal and args.companion:
        parser.error("--normal and --companion are mutually exclusive")
    build = root / "build" / ("normal_validation" if args.normal else "meeting_companion" if args.companion else "meeting_trial")
    build.mkdir(parents=True, exist_ok=True)
    config = build / "sdkconfig"
    overrides = {}
    for line in Path(__file__).with_name("sdkconfig.defaults").read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            overrides[key] = value
    if args.normal:
        overrides = {"CONFIG_FOLO_MEETING_TRIAL": "n"}
    else:
        overrides["CONFIG_FOLO_MEETING_COMPANION"] = "y" if args.companion else "n"
    if args.companion:
        for line in Path(__file__).with_name("sdkconfig.companion").read_text().splitlines():
            if line.startswith("CONFIG_") and "=" in line:
                key, value = line.split("=", 1)
                overrides[key] = value
    original = (root / "sdkconfig").read_text(encoding="utf-8")
    lines = [line for line in original.splitlines()
             if line.split("=", 1)[0] not in overrides
             and not any(line == f"# {key} is not set" for key in overrides)]
    lines.extend(f"{key}={value}" for key, value in overrides.items())
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(IDF_PATH=str(args.idf), IDF_TOOLS_PATH=str(args.tools),
               IDF_PYTHON_ENV_PATH=sys.prefix, IDF_TARGET="esp32c3",
               PYTHONUTF8="1")
    # Export tool paths without invoking a shell or editing the user's global PATH.
    result = subprocess.run([sys.executable, str(args.idf / "tools/idf_tools.py"),
                             "export", "--format", "key-value"],
                            env=env, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            value = value.replace("%PATH%", env["PATH"]).replace("$PATH", env["PATH"])
            env[key] = value
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    command = [sys.executable, str(args.idf / "tools/idf.py"), "-B", str(build),
               "-D", "SDKCONFIG=" + str(config), "build"]
    return subprocess.call(command, cwd=root, env=env)


if __name__ == "__main__":
    sys.exit(main())
