# HyperMesh Core — C shared library build
#
# Targets:
#   make                 — build libhypermesh (.dylib on macOS, .so on Linux)
#   make test            — compile and run tests/test_basic
#   make parser_test     — compile and run tests/test_parser
#   make write_test      — compile and run tests/test_write
#   make demo            — build and run demo/edge_demo
#   make clean           — remove build artifacts
#   make install         — copy lib to ../server/ for Python bridge use
#   make migrate         — build the hmdb_migrate CLI tool
#
# Cross-compilation (ARM example):
#   make CC=aarch64-linux-gnu-gcc

# Allow CC override for cross-compilation; default to system cc
CC      ?= cc
CFLAGS  := -std=c11 -O2 -Wall -Wextra -Wno-unused-parameter -Isrc -D_GNU_SOURCE
LDFLAGS :=
LMATH   := -lm

SRC := src/tpi.c src/fmi.c src/wal.c src/psi.c src/hypermesh.c \
       src/cypher_parser/lexer.c src/cypher_parser/parser.c

# Platform detection
UNAME := $(shell uname -s)
ifeq ($(UNAME), Darwin)
    LIB      := libhypermesh.dylib
    LIBFLAGS := -dynamiclib -install_name @rpath/$(LIB)
    # pthreads are part of the system library on macOS; no extra flag needed
    PTHREAD  :=
else
    LIB      := libhypermesh.so
    CFLAGS   += -fPIC
    LIBFLAGS := -shared
    PTHREAD  := -lpthread
endif

.PHONY: all test parser_test write_test v2_test wal_test compact_test native-test demo coordinator clean install migrate

all: $(LIB)

PARSER_HDRS := src/cypher_parser/lexer.h src/cypher_parser/parser.h

$(LIB): $(SRC) src/format.h src/tpi.h src/fmi.h src/wal.h src/hypermesh.h $(PARSER_HDRS)
	$(CC) $(CFLAGS) $(LIBFLAGS) -o $@ $(SRC) $(LDFLAGS) $(PTHREAD)
	@echo "Built $@"

# The original smoke fixture was outside this repository. Generate the same
# deterministic 1,774-record, 20-drone shape locally so `make test` works from
# any checkout without relying on a developer machine or an untracked file.
tests/fixtures/hyperedges.csv:
	@mkdir -p tests/fixtures
	@{ \
	  printf '%s\n' 'event_ts,members,member_count,weight,mean_dist_m,formation'; \
	  i=0; while [ $$i -lt 1774 ]; do \
	    printf '%s,"[8, %s]",2,0.9,50.0,WEDGE\n' $$((i % 1000)) $$((i % 20 + 1)); \
	    i=$$((i + 1)); \
	  done; \
	} > $@

test: $(LIB) tests/test_basic.c tests/fixtures/hyperedges.csv
	$(CC) $(CFLAGS) -o tests/test_basic tests/test_basic.c \
	    -L. -lhypermesh $(PTHREAD) -Wl,-rpath,.
	@echo "Running smoke tests..."
	@cd tests && DYLD_LIBRARY_PATH=.. LD_LIBRARY_PATH=.. ./test_basic

parser_test: $(LIB) tests/test_parser.c
	$(CC) $(CFLAGS) -o tests/test_parser tests/test_parser.c \
	    -L. -lhypermesh $(PTHREAD) -Wl,-rpath,.
	@echo "Running parser tests..."
	@cd tests && DYLD_LIBRARY_PATH=.. LD_LIBRARY_PATH=.. ./test_parser

write_test: $(LIB) tests/test_write.c
	$(CC) $(CFLAGS) -o tests/test_write tests/test_write.c \
	    -L. -lhypermesh $(PTHREAD) -Wl,-rpath,.
	@echo "Running write-path tests..."
	@cd tests && DYLD_LIBRARY_PATH=.. LD_LIBRARY_PATH=.. ./test_write

demo: $(LIB) demo/edge_demo.c
	@mkdir -p demo
	$(CC) $(CFLAGS) -o demo/edge_demo demo/edge_demo.c \
	    -L. -lhypermesh $(PTHREAD) -Wl,-rpath,.
	@echo "Running edge coalition demo..."
	@cd demo && DYLD_LIBRARY_PATH=.. LD_LIBRARY_PATH=.. ./edge_demo

coordinator: $(LIB) demo/swarm_coordinator.c
	@mkdir -p demo
	$(CC) $(CFLAGS) -o demo/swarm_coordinator demo/swarm_coordinator.c \
	    -L. -lhypermesh $(PTHREAD) $(LMATH) -Wl,-rpath,.
	@echo "Built demo/swarm_coordinator — run with:"
	@echo "  cd demo && DYLD_LIBRARY_PATH=.. LD_LIBRARY_PATH=.. ./swarm_coordinator"

v2_test: $(LIB) tests/test_v2.c migrate
	$(CC) $(CFLAGS) -o tests/test_v2 tests/test_v2.c \
	    -L. -lhypermesh $(PTHREAD) -Wl,-rpath,.
	@echo "Running V2 property tests..."
	@cd tests && DYLD_LIBRARY_PATH=.. LD_LIBRARY_PATH=.. ./test_v2

wal_test: $(LIB) tests/test_wal_recovery.c
	$(CC) $(CFLAGS) -o tests/test_wal_recovery tests/test_wal_recovery.c \
	    -L. -lhypermesh $(PTHREAD) -Wl,-rpath,.
	@echo "Running WAL crash-recovery tests..."
	@cd tests && DYLD_LIBRARY_PATH=.. LD_LIBRARY_PATH=.. ./test_wal_recovery

compact_test: $(LIB) tests/test_compact_scale.c
	$(CC) $(CFLAGS) -o tests/test_compact_scale tests/test_compact_scale.c \
	    -L. -lhypermesh $(PTHREAD) -Wl,-rpath,.
	@echo "Running compaction scale + reconciliation tests..."
	@cd tests && DYLD_LIBRARY_PATH=.. LD_LIBRARY_PATH=.. ./test_compact_scale

# The blocking release gate exercises every portable native test target.
native-test: test parser_test write_test v2_test wal_test compact_test

migrate: src/hmdb_migrate.c src/format.h
	$(CC) $(CFLAGS) -o hmdb_migrate src/hmdb_migrate.c
	@echo "Built hmdb_migrate"

clean:
	rm -f $(LIB) tests/test_basic tests/test_parser tests/test_write tests/test_v2 tests/test_wal_recovery tests/test_compact_scale demo/edge_demo hmdb_migrate tests/fixtures/hyperedges.csv

# Copy the shared library to ../server/ so the Python bridge can find it
install: $(LIB)
	cp $(LIB) ../server/$(LIB)
	@echo "Installed $(LIB) → ../server/$(LIB)"
