# Amaranth port of SpinalVoodoo. See README.md and ../docs/amaranth-rewrite-plan.md.
#
# Everything runs inside the uv-managed venv in .venv; `uv run` creates/syncs it
# on demand, so `make test` works from a clean checkout.

UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
RUN := $(UV) run --project $(CURDIR)
RTL_DIR := $(CURDIR)/rtl

.PHONY: help venv test rtl-module rtl-core-sim rtl-console diff gowin-console clean

help:
	@echo "venv           uv sync (create/refresh .venv)"
	@echo "test           pytest"
	@echo "rtl-module MOD=Counter   elaborate one module -> rtl/<MOD>.v"
	@echo "rtl-core-sim   (Phase 7) CoreSim.v for the Verilator Glide harness"
	@echo "rtl-console    (Phase 8) TangConsole138k.v for the Gowin build"
	@echo "diff MOD=X     (Phase 0.4) Spinal-vs-Amaranth lockstep test"
	@echo "gowin-console  (Phase 8) PnR on hyper-ms-lin"
	@echo "rtl-spike      Phase 0.3 Gowin compatibility spike Verilog"

venv:
	$(UV) sync --project $(CURDIR)

test: venv
	$(RUN) pytest

rtl-module: venv
	@test -n "$(MOD)" || { echo "usage: make rtl-module MOD=<Name>"; exit 2; }
	$(RUN) python -m gen.gen_module $(MOD) -o $(RTL_DIR) $(PARAMS)

rtl-spike: venv
	$(RUN) python -m gen.gen_module SpikeBlinky -o $(RTL_DIR)

# --- placeholders; filled in by the phases named above ----------------------

rtl-core-sim:
	@echo "not implemented yet (plan Phase 7.2: gen/gen_core_sim.py -> rtl/CoreSim.v)"; exit 1

rtl-console:
	@echo "not implemented yet (plan Phase 8.3: gen/gen_tang_console.py -> rtl/TangConsole138k.v)"; exit 1

diff:
	@echo "not implemented yet (plan Phase 0.4: tools/diff/harness.py MOD=$(MOD))"; exit 1

gowin-console:
	@echo "not implemented yet (plan Phase 8.4: hw/console + gw_sh on hyper-ms-lin)"; exit 1

clean:
	rm -rf $(RTL_DIR) .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
