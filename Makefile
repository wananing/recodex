.PHONY: test build publish publish-yes dashboard-install dashboard-dev dashboard-build dashboard-preview dashboard-serve scan retro patterns propose review export-agents export-skills

test:
	PYTHONPATH=src python3 -m unittest discover -s tests

build:
	uv build

publish:
	scripts/publish-pypi.sh

publish-yes:
	scripts/publish-pypi.sh --yes

dashboard-install:
	npm --prefix dashboard install

dashboard-dev:
	npm --prefix dashboard run dev

dashboard-build:
	npm --prefix dashboard run build

dashboard-preview:
	npm --prefix dashboard run preview

dashboard-serve: dashboard-build
	PYTHONPATH=src python3 -m recodex serve --dashboard-dir dashboard/dist

scan:
	PYTHONPATH=src python3 -m recodex scan

retro:
	PYTHONPATH=src python3 -m recodex retro latest

patterns:
	PYTHONPATH=src python3 -m recodex patterns --since 30d

propose:
	PYTHONPATH=src python3 -m recodex improvements propose

review:
	PYTHONPATH=src python3 -m recodex improvements review

export-agents:
	PYTHONPATH=src python3 -m recodex export agents

export-skills:
	PYTHONPATH=src python3 -m recodex export skills
