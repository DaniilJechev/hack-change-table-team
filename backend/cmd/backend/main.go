package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/DaniilJechev/hack-change-table-team/backend/internal/api"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/server"
	"github.com/DaniilJechev/hack-change-table-team/backend/internal/store"
)

func run(parent context.Context, addr string, handlers ...http.Handler) error {
	ctx, stop := signal.NotifyContext(parent, os.Interrupt, syscall.SIGTERM)
	defer stop()
	if addr == "" {
		addr = ":8080"
	}
	l, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	return server.Serve(ctx, l, handlers...)
}

func execute(ctx context.Context, args []string, getenv func(string) string, output io.Writer) error {
	command := "serve"
	if len(args) > 0 {
		command = args[0]
	}
	if command != "serve" && command != "seed" && command != "invite" {
		return errors.New("usage: backend [serve|seed|invite --user ID --ttl 24h]")
	}
	key := getenv("BUDDY_SERVICE_KEY")
	if command == "serve" && key == "" {
		return errors.New("BUDDY_SERVICE_KEY is required")
	}
	startup, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	database, err := store.Open(startup, getenv("DATABASE_URL"))
	if err != nil {
		return err
	}
	defer database.Close()
	if err := database.Migrate(startup); err != nil {
		return errors.New("database migration failed")
	}
	switch command {
	case "seed":
		path := getenv("SEED_USERS_PATH")
		if path == "" {
			path = "/seed/users.yaml"
		}
		file, err := os.Open(path)
		if err != nil {
			return errors.New("cannot open seed users file")
		}
		defer file.Close()
		if err := database.Seed(startup, file); err != nil {
			return fmt.Errorf("seed import failed: %w", err)
		}
		_, err = fmt.Fprintln(output, "Synthetic users imported")
		return err
	case "invite":
		flags := flag.NewFlagSet("invite", flag.ContinueOnError)
		flags.SetOutput(io.Discard)
		user := flags.String("user", "", "internal user ID")
		ttl := flags.Duration("ttl", 24*time.Hour, "validity (max 168h)")
		if err := flags.Parse(args[1:]); err != nil {
			return errors.New("invalid invite arguments")
		}
		if *user == "" || flags.NArg() != 0 {
			return errors.New("invite requires --user ID")
		}
		code, err := database.IssueInvitation(startup, *user, *ttl)
		if err != nil {
			return errors.New("cannot issue invitation: check user, binding and TTL")
		}
		_, err = fmt.Fprintln(output, code)
		return err
	default:
		return run(ctx, getenv("HTTP_ADDR"), api.Handler(api.Identity{Store: database, ServiceKey: key}))
	}
}

func main() {
	if err := execute(context.Background(), os.Args[1:], os.Getenv, os.Stdout); err != nil {
		log.Fatal(err)
	}
}
