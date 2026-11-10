import argparse
import asyncio

from buddy.app import run
from buddy.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-buddy")
    parser.add_argument("--smoke", action="store_true", help="Offline /start check without network")
    args = parser.parse_args()
    if args.smoke:
        from buddy.smoke import smoke

        asyncio.run(smoke())
    else:
        asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
