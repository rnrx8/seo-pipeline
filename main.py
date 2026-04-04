#!/usr/bin/env python3
import argparse
import os
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = ["SUPABASE_URL", "SUPABASE_KEY", "SERPAPI_KEY", "ANTHROPIC_API_KEY"]


def check_env() -> None:
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"Missing environment variables: {', '.join(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SEO article generation pipeline")
    parser.add_argument("keyword", help="Target keyword for the SEO article")
    args = parser.parse_args()

    check_env()

    from pipeline.runner import run_pipeline
    run_pipeline(args.keyword)


if __name__ == "__main__":
    main()
