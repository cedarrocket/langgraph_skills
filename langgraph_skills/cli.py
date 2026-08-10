import argparse
import os
import sys

from langgraph_skills import compiler, model_cmd, runner
from langgraph_skills.config import Settings
from langgraph_skills.parser import parse_compiled_skill, validate_node_graph


def main() -> None:
    settings = Settings.load()
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
    run_parser.add_argument("-q", "--quiet", action="store_true", help="Hide node-level debug logs (keep interaction prompts and errors)")
    run_parser.add_argument("skill_args", nargs=argparse.REMAINDER, help="Arguments and options for the skill")

    # model subcommand
    model_parser = subparsers.add_parser("model", help="Manage AI models and providers")
    model_sub = model_parser.add_subparsers(dest="model_command", help="Model commands")
    model_sub.add_parser("list", help="List available providers and models")
    model_set = model_sub.add_parser("set", help="Set default model (e.g. deepseek/deepseek-chat)")
    model_set.add_argument("model_ref", help="Model reference like 'provider/model'")
    model_sub.add_parser("config", help="Show effective model configuration")
    model_sub.add_parser("import-opencode", help="Import providers from opencode global config")

    # Check if we were called without command but with a markdown file (for shebang / direct run compatibility)
    args = sys.argv[1:]
    if args and args[0] not in ("compile", "validate", "run", "model", "-h", "--help"):
        # If the first argument exists or ends with .md, default to run command
        if os.path.exists(args[0]) or args[0].endswith(".md"):
            sys.argv.insert(1, "run")

    parsed = parser.parse_args()

    if parsed.command == "compile":
        compiler.compile_skill(parsed.draft_path, parsed.output_path)
    elif parsed.command == "validate":
        try:
            compiled = parse_compiled_skill(parsed.skill_path, strict=settings.strict)
            errors = validate_node_graph(compiled.nodes, subgraph_names=set(compiled.subgraphs.keys()))
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
        runner.run_cli(parsed.skill_path, parsed.skill_args, quiet=getattr(parsed, "quiet", False))
    elif parsed.command == "model":
        if parsed.model_command == "list":
            model_cmd.cmd_list()
        elif parsed.model_command == "set":
            model_cmd.cmd_set(parsed.model_ref)
        elif parsed.model_command == "config":
            model_cmd.cmd_config()
        elif parsed.model_command == "import-opencode":
            model_cmd.cmd_import_opencode()
        else:
            model_parser.print_help()
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
