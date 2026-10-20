"""CLI tools print the pipeline's exact text, with paths to its exact image bytes."""
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="LAB only — $0 API lens material trials")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("trial_dir")
    init.add_argument("--source-job")
    tool = sub.add_parser("tool")
    tool.add_argument("trial_dir")
    tool.add_argument("tool")
    tool.add_argument("arguments", nargs="?", default="{}")
    tool.add_argument("--args-file", type=Path, help="Exact JSON arguments, convenient on Windows")
    describe = sub.add_parser("tools")
    describe.add_argument("trial_dir")
    args = parser.parse_args(argv)
    from .harness import init_trial, call_tool, trial_path
    if args.command == "init":
        print(json.dumps(init_trial(args.trial_dir, args.source_job), ensure_ascii=False))
    elif args.command == "tools":
        print((trial_path(args.trial_dir) / "tool-schemas.json").read_text(encoding="utf-8"))
    else:
        raw = args.args_file.read_text(encoding="utf-8-sig") if args.args_file else args.arguments
        result = call_tool(args.trial_dir, args.tool, json.loads(raw))
        for text in result["output_text"]:
            print(text)
        for image in result["images"]:
            print("LAB tool image (open with your image-view tool): " + image["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
