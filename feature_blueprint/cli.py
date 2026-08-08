from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import ValidationError

from feature_blueprint.browser import BrowserCollectionError
from feature_blueprint.models import AppConfig
from feature_blueprint.pipeline import run_analysis
from feature_blueprint.utils import load_gemini_key_from_env_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feature-blueprint",
        description="Generate evidence-backed full-stack requirements from a web feature.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Analyze a feature using a YAML configuration")
    analyze.add_argument("config", type=Path, help="Path to the YAML configuration")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "analyze":
        parser.error("Unknown command")

    try:
        load_gemini_key_from_env_files([Path.cwd() / ".env", args.config.parent / ".env"])
        config = AppConfig.from_yaml(args.config)
        asyncio.run(run_analysis(config))
    except (ValueError, ValidationError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except BrowserCollectionError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(3) from exc
    except KeyboardInterrupt as exc:
        print("Analysis cancelled", file=sys.stderr)
        raise SystemExit(130) from exc
    except Exception as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        raise SystemExit(4) from exc
