package main

import (
	"bytes"
	"context"
	"os"
	"strings"
	"testing"
)

func TestFR_TR_03_InvalidRetentionRefusesStart(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires TEST_DATABASE_URL")
	}
	for _, bad := range []string{"0", "-1", "thirty"} {
		values := map[string]string{"DATABASE_URL": dsn, "BUDDY_SERVICE_KEY": "key", "AGENT_TRACE_RETENTION_DAYS": bad}
		err := execute(context.Background(), nil, func(k string) string { return values[k] }, &bytes.Buffer{})
		if err == nil || !strings.Contains(err.Error(), "AGENT_TRACE_RETENTION_DAYS") {
			t.Fatal(bad, err)
		}
	}
}
