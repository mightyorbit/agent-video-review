# Contributing

Issues and pull requests are welcome. For a bug, include the command, expected result, actual result, Python version, and a reproduction using synthetic or redistributable media. Do not upload private footage, review comments, API keys, or client data.

Run the checks before a pull request:

    PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v

For UI changes, also run the README quick start with synthetic media in a current browser. Keep the default server bound to localhost. Document changes to the review.json schema and provide migration logic before changing its schema number.

Contributions are submitted under the repository's MIT license.
