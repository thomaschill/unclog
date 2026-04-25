"""Console-script entrypoint — ``unclog`` and ``python -m unclog`` both land here."""

from unclog.cli import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
