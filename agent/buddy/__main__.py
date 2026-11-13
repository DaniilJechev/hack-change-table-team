import argparse
import asyncio
import logging

from buddy.app import run
from buddy.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-buddy")
    parser.add_argument("--smoke", action="store_true", help="Offline /start check without network")
    args = parser.parse_args()
    # Our INFO lines carry only reasons and counters; libraries stay at WARNING (httpx logs request URLs).
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("buddy").setLevel(logging.INFO)
    if args.smoke:
        from buddy.smoke import smoke

        asyncio.run(smoke())
    else:
        asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
