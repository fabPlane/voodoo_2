# Host tools for the Tang Mega 138K Pro Dock PCIe BAR.
#
#   make            pci-bar + pci-trace-replay + window unit test
#   make test       run the offline window test
#   sudo ./pci-bar status
#   sudo ./pci-trace-replay ../../ext/SpinalVoodoo/traces/logo

CXX      ?= g++
CXXFLAGS ?= -O2 -g -Wall -Wextra -std=c++17
TRACE    := ../../ext/SpinalVoodoo/emu/trace
STB      := ../../ext/SpinalVoodoo/emu/test
INCLUDES := -I. -I$(TRACE) -I$(STB)

.PHONY: all test clean

all: pci-bar pci-trace-replay pci_window_test voodoo-stream

pci-bar: pci-bar.cpp pci_bar.h
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o $@ pci-bar.cpp

pci-trace-replay: pci-trace-replay.cpp pci_bar.h
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o $@ pci-trace-replay.cpp

voodoo-stream: voodoo-stream.cpp pci_bar.h
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o $@ voodoo-stream.cpp

pci_window_test: pci_window_test.cpp pci_bar.h
	$(CXX) $(CXXFLAGS) $(INCLUDES) -o $@ pci_window_test.cpp

test: pci_window_test
	./pci_window_test

clean:
	rm -f pci-bar pci-trace-replay pci_window_test voodoo-stream
