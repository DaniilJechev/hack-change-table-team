package main

import (
	"context"
	"net"
	"testing"
)

func TestNFR_BOOT_1_CancelledStartup(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := run(ctx, "127.0.0.1:0"); err != nil {
		t.Fatal(err)
	}
}

func TestNFR_BOOT_1_AddressAlreadyInUse(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if err := run(context.Background(), listener.Addr().String()); err == nil {
		t.Fatal("startup must fail when the address is occupied")
	}
}
