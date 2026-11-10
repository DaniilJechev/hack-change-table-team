package server

import (
	"context"
	"errors"
	"net"
	"net/http"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/api"
)

// Serve takes ownership of listener and shuts down when ctx is cancelled.
func Serve(ctx context.Context, listener net.Listener, handlers ...http.Handler) error {
	handler := api.Handler()
	if len(handlers) > 0 {
		handler = handlers[0]
	}
	server := &http.Server{
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	shutdownDone := make(chan struct{})
	stop := context.AfterFunc(ctx, func() {
		defer close(shutdownDone)
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			_ = server.Close()
		}
	})
	defer func() {
		if !stop() {
			<-shutdownDone
		}
	}()

	err := server.Serve(listener)
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}
