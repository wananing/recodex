.PHONY: test scan retro patterns propose review export-agents export-skills

test:
	PYTHONPATH=src python3 -m unittest discover -s tests

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
