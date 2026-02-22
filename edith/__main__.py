import sys

from edith.application import EdithApplication


def main():
    app = EdithApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
