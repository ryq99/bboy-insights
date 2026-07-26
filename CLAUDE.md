# bboy-insights — project instructions

## Python code conventions

- **No `__init__.py` and no `__main__.py` files.** Do not create either.
  - Use [PEP 420 implicit namespace packages](https://peps.python.org/pep-0420/) — directories are importable without `__init__.py`.
  - Do not rely on `python -m <package>` for CLIs (it requires `__main__.py`). Provide a CLI via a
    console-script entry point in `pyproject.toml` (`[project.scripts]`) pointing at a `main()` in a
    regular module (e.g. `youtube/cli.py:main`), or run that module directly.
