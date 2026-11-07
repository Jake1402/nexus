.PHONY: run test install clean

run:
	python3 nexus.py

test:
	python3 tests/test_nexus.py

install:
	./install.sh

clean:
	sh -c 'find . -name __pycache__ -type d -exec rm -rf {} +'
	sh -c 'rm -rf dist build *.egg-info'
