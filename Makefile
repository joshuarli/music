pc:
	.venv/bin/ruff format .
	.venv/bin/ruff check --fix .
	.venv/bin/ty check --fix
