.PHONY: check audit test fmt

# What CI runs.
check: fmt test

fmt:
	ruff check .
	ruff format --check .

test:
	pytest -q

# A poor man's SonarQube: rules ruff implements but the project doesn't enforce
# in pyproject (they are advisory, not gates). Read the output, don't chase zero
# — S603 on every subprocess call and ARG002 on the block interface are by
# design. Run it before cutting a release.
audit:
	@echo "── security (flake8-bandit) ─────────────────────────"
	-@ruff check --select S --statistics src/
	@echo "── complexity (mccabe > 12) ────────────────────────"
	-@ruff check --select C901 --config 'lint.mccabe.max-complexity=12' --output-format concise src/
	@echo "── smells & dead code ──────────────────────────────"
	-@ruff check --select SIM,RUF,ARG,PIE,PERF --statistics src/
	@echo "── blocks: registry vs SPEC vs man page ────────────"
	@python3 scripts/audit_blocks.py
