package main

import (
	"context"
	"net"
	"net/http"
	"testing"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/server"
)

func TestST0_INFRA_1_GracefulShutdown(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- server.Serve(ctx, l) }()
	client := &http.Client{Timeout: time.Second}
	response, err := client.Get("http://" + l.Addr().String() + "/health")
	if err != nil {
		t.Fatal(err)
	}
	_ = response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatal(response.StatusCode)
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(6 * time.Second):
		t.Fatal("shutdown timed out")
	}
}

func TestST0_INFRA_1_InvalidAddress(t *testing.T) {
	if err := run(context.Background(), "invalid:address:port"); err == nil {
		t.Fatal("expected listen error")
	}
}

func TestST0_INFRA_1_RunCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := run(ctx, "127.0.0.1:0"); err != nil {
		t.Fatal(err)
	}
}
func TestST0_INFRA_1_ServeClosedListener(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	_ = l.Close()
	if err := server.Serve(context.Background(), l); err == nil {
		t.Fatal("expected closed listener error")
	}
}

func TestNFR_BOOT_1_AddressAlreadyInUse(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if err := run(context.Background(), listener.Addr().String()); err == nil {
		t.Fatal("expected occupied port error")
	}
}
