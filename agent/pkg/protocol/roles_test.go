package protocol

import (
	"context"
	"testing"

	"google.golang.org/grpc/metadata"
)

func callerContext(pairs ...string) context.Context {
	return metadata.NewIncomingContext(context.Background(), metadata.Pairs(pairs...))
}

func TestRoleMatrix(t *testing.T) {
	ownerHex := "000102030405060708090a0b0c0d0e0f"
	cases := []struct {
		name    string
		role    string
		method  string
		allowed bool
	}{
		{"runtime submits", RoleRuntime, "/taskwire.v1.TaskwireControl/Submit", true},
		{"runtime may not work", RoleRuntime, "/taskwire.v1.TaskwireControl/Work", false},
		{"worker works", RoleWorker, "/taskwire.v1.TaskwireControl/Work", true},
		{"worker may not submit", RoleWorker, "/taskwire.v1.TaskwireControl/Submit", false},
		{"admin reads status", RoleAdmin, "/taskwire.v1.TaskwireControl/Status", true},
		{"admin may not submit", RoleAdmin, "/taskwire.v1.TaskwireControl/Submit", false},
		{"runtime may not forward", RoleRuntime, "/taskwire.v1.TaskwireControl/ForwardTask", false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ctx := callerContext(MetadataRole, tc.role, MetadataOwnerID, ownerHex)
			_, err := authorize(ctx, tc.method)
			if tc.allowed && err != nil {
				t.Fatalf("role %s should call %s: %v", tc.role, tc.method, err)
			}
			if !tc.allowed && err == nil {
				t.Fatalf("role %s must not call %s", tc.role, tc.method)
			}
		})
	}
}

func TestMissingRoleMetadataRejected(t *testing.T) {
	if _, err := authorize(context.Background(), "/taskwire.v1.TaskwireControl/Status"); err == nil {
		t.Fatal("request without metadata accepted")
	}
	ctx := callerContext("unrelated", "value")
	if _, err := authorize(ctx, "/taskwire.v1.TaskwireControl/Status"); err == nil {
		t.Fatal("request without role metadata accepted")
	}
}

func TestRuntimeRequiresValidOwnerID(t *testing.T) {
	ctx := callerContext(MetadataRole, RoleRuntime)
	if _, err := authorize(ctx, "/taskwire.v1.TaskwireControl/Submit"); err == nil {
		t.Fatal("runtime without owner_id accepted")
	}
	ctx = callerContext(MetadataRole, RoleRuntime, MetadataOwnerID, "abcd")
	if _, err := authorize(ctx, "/taskwire.v1.TaskwireControl/Submit"); err == nil {
		t.Fatal("runtime with short owner_id accepted")
	}
}

func TestAuthorizedCallerIsAttachedToContext(t *testing.T) {
	ownerHex := "000102030405060708090a0b0c0d0e0f"
	ctx := callerContext(MetadataRole, RoleRuntime, MetadataOwnerID, ownerHex)
	authorized, err := authorize(ctx, "/taskwire.v1.TaskwireControl/Submit")
	if err != nil {
		t.Fatal(err)
	}
	caller, ok := CallerFrom(authorized)
	if !ok || caller.Role != RoleRuntime || len(caller.OwnerID) != 16 {
		t.Fatalf("unexpected caller identity: %+v", caller)
	}
}
