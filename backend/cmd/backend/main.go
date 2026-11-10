package main

import (
	"context"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/api"
)

func serve(ctx context.Context, listener net.Listener) error {
	srv := &http.Server{Handler: api.Handler(), ReadHeaderTimeout: 5 * time.Second}
	finished := make(chan struct{})
	defer close(finished)
	go func() {
		select {
		case <-ctx.Done():
			shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = srv.Shutdown(shutdown)
		case <-finished:
		}
	}()
	err := srv.Serve(listener)
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

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
	return serve(ctx, l)
}

func main() {
	if err := run(context.Background(), os.Getenv("HTTP_ADDR")); err != nil {
		log.Fatal(err)
	}
}
