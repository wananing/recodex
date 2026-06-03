.PHONY: test scan retro patterns propose review export-agents export-skills

test:
	PYTHONPATH=src python3 -m unittest discover -s tests

scan:
	PYTHONPATH=src python3 -m ai_dev_review scan

retro:
	PYTHONPATH=src python3 -m ai_dev_review retro latest

patterns:
	PYTHONPATH=src python3 -m ai_dev_review patterns --since 30d

propose:
	PYTHONPATH=src python3 -m ai_dev_review improvements propose

review:
	PYTHONPATH=src python3 -m ai_dev_review improvements review

export-agents:
	PYTHONPATH=src python3 -m ai_dev_review export agents

export-skills:
	PYTHONPATH=src python3 -m ai_dev_review export skills
