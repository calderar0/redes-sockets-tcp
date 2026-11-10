# Compila servidor e cliente em Linux/macOS (gcc/clang) ou Windows (MinGW-w64).
#   Linux:   make
#   Windows: mingw32-make

ifeq ($(origin CC),default)
  CC := gcc
endif
CFLAGS  ?= -Wall -Wextra -O2 -std=gnu11
LDFLAGS ?=

ifeq ($(OS),Windows_NT)
  EXE    := .exe
  LDLIBS := -pthread -lws2_32
else
  EXE    :=
  LDLIBS := -pthread
endif

BIN := bin
SERVER := $(BIN)/chat_server$(EXE)
CLIENT := $(BIN)/chat_client$(EXE)

all: $(SERVER) $(CLIENT)

$(BIN):
	mkdir -p $(BIN)

$(SERVER): src/chat_server.c src/net_compat.h | $(BIN)
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS) $(LDLIBS)

$(CLIENT): src/chat_client.c src/net_compat.h | $(BIN)
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS) $(LDLIBS)

clean:
	rm -rf $(BIN)

.PHONY: all clean
