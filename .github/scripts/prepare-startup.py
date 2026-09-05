import argparse
import base64
import json
from pathlib import Path
import shlex


def build_command():
    scripts = Path(__file__).resolve().parent
    startup = scripts.parent.parent / "startup"
    bundle = {
        name: base64.b64encode((startup / name).read_bytes()).decode("ascii")
        for name in ("boot.sh", "preview.yml")
    }
    installer = base64.b64encode((scripts / "install-startup.py").read_bytes()).decode("ascii")
    program = f"import base64; exec(compile(base64.b64decode('{installer}'), 'install-startup.py', 'exec'))"
    argument = base64.b64encode(json.dumps(bundle).encode("utf-8")).decode("ascii")
    return f"python3 -c {shlex.quote(program)} {shlex.quote(argument)}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    Path(arguments.output).write_text(build_command() + "\n", encoding="utf-8")
