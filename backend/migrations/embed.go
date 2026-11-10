package migrations

import "embed"

// Files contains the versioned schema, also available inside the read-only image.
//
//go:embed *.sql
var Files embed.FS
