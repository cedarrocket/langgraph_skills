import argparse
import os
import sys

from langgraph_skills import compiler, runner
from langgraph_skills.config import Settings
from langgraph_skills.parser import parse_compiled_skill, validate_node_graph


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(
        description="LangGraph Skills: Declarative Agent Harness CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # compile subcommand
    compile_parser = subparsers.add_parser("compile", help="Compile a draft skill into standardized Markdown AST")
    compile_parser.add_argument("draft_path", help="Path to draft skill Markdown file")
    compile_parser.add_argument("output_path", nargs="?", default="compiled_skill.md", help="Path to save compiled skill")

    # validate subcommand
    validate_parser = subparsers.add_parser("validate", help="Statically validate a compiled skill")
    validate_parser.add_argument("skill_path", help="Path to compiled skill Markdown file")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run a compiled skill")
    run_parser.add_argument("skill_path", help="Path to compiled skill Markdown file")
    run_parser.add_argument("skill_args", nargs=argparse.REMAINDER, help="Arguments and options for the skill")

    # Check if we were called without command but with a markdown file (for shebang / direct run compatibility)
    args = sys.argv[1:]
    if args and args[0] not in ("compile", "validate", "run", "-h", "--help"):
        # If the first argument exists or ends with .md, default to run command
        if os.path.exists(args[0]) or args[0].endswith(".md"):
            sys.argv.insert(1, "run")

    parsed = parser.parse_args()

    if parsed.command == "compile":
        compiler.compile_skill(parsed.draft_path, parsed.output_path)
    elif parsed.command == "validate":
        try:
            node_dict = parse_compiled_skill(parsed.skill_path, strict=settings.strict).nodes
            errors = validate_node_graph(node_dict)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                sys.exit(2)
            else:
                print("OK", file=sys.stderr)
                sys.exit(0)
        except Exception as e:
            print(f"Parsing error: {e}", file=sys.stderr)
            sys.exit(2)
    elif parsed.command == "run":
        runner.run_cli(parsed.skill_path, parsed.skill_args)
    else:
        parser.print_help()
        sys.exit(0)

if __name__ == "__main__":
    main()
