.PHONY: run test compile seed clean help

help:
	@echo "Muninn — make targets:"
	@echo "  make run      start the server (http://127.0.0.1:8000)"
	@echo "  make test     run the stdlib unittest suite"
	@echo "  make compile  syntax/import gate (compileall)"
	@echo "  make clean    remove the local db + memory + caches"

run:
	python3 -m backend.server

test:
	python3 -m unittest discover -s tests -v

compile:
	python3 -m compileall backend tests

clean:
	rm -f data/muninn.db data/muninn.db-* data/memory_local.json
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
