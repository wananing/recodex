.PHONY: test build publish publish-yes dashboard-install dashboard-dev dashboard-build dashboard-preview dashboard-serve scan report

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

report:
	PYTHONPATH=src python3 -m recodex report latest --llm --llm-provider "$${RECODEX_LLM_PROVIDER:?Set RECODEX_LLM_PROVIDER}" --allow-cloud
