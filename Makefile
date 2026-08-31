.PHONY: run demo test mutants

run:      ## the agent; live Claude loop if ANTHROPIC_API_KEY is set
	python agent.py

demo:     ## the same steps as a plain batch pipeline
	python pipeline.py

test:     ## 15 offline tests, no key needed
	pytest -q

mutants:  ## break the code six ways; the suite must go red each time
	python mutcheck.py
