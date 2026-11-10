package server

import (
	"context"
	"errors"
	"net"
	"net/http"
	"time"
)

// Handler exposes only process liveness until the application is implemented.
func Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte("{\"status\":\"ok\",\"service\":\"backend\",\"mode\":\"stub\"}\n"))
	})
	return mux
}

// Serve takes ownership of listener and shuts down when ctx is cancelled.
func Serve(ctx context.Context, listener net.Listener) error {
	server := &http.Server{
		Handler:           Handler(),
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
