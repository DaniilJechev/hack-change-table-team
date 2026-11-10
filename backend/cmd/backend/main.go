package main

import (
	"context"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/server"
)

func run(parent context.Context, addr string) error {
	ctx, stop := signal.NotifyContext(parent, os.Interrupt, syscall.SIGTERM)
	defer stop()
	if addr == "" {
		addr = ":8080"
	}
	l, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	return server.Serve(ctx, l)
}

func main() {
	if err := run(context.Background(), os.Getenv("HTTP_ADDR")); err != nil {
		log.Fatal(err)
	}
}
