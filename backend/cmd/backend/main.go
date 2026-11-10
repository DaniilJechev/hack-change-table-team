package main

import (
	"context"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"syscall"

	"ai-buddy/backend/internal/server"
)

func main() {
	if err := run(context.Background(), os.Getenv("HTTP_ADDR")); err != nil {
		slog.Error("backend stopped", "error", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, address string) error {
	ctx, stop := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer stop()
	if address == "" {
		address = ":8080"
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return err
	}

	slog.Info("backend stub started", "address", listener.Addr().String())
	return server.Serve(ctx, listener)
}
