package protocol

import (
	"bytes"
	"context"
	"testing"

	"google.golang.org/grpc/metadata"
)

func workerRegistration(workerID, runtime string, codecs []string, generation uint64, tasks []*TaskCapability) *WorkerRegistration {
	return &WorkerRegistration{
		WorkerId: workerID, Runtime: runtime, RuntimeVersion: "1", SdkVersion: "0.1.0",
		Codecs: codecs,
		Tasks:  &TaskRegistration{WorkerId: workerID, Generation: generation, Tasks: tasks},
	}
}

func TestRegisterWorkerEstablishesCapabilities(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	registration := workerRegistration("w", "python", []string{"msgpack"}, 1,
		[]*TaskCapability{{TaskName: "a", TaskVersion: "1", Invocation: "value", Codecs: []string{"msgpack"}}})

	if err := s.RegisterWorker(state, registration); err != nil {
		t.Fatal(err)
	}
	if !state.Registered || state.WorkerID != "w" || state.CapabilityGeneration != 1 {
		t.Fatalf("registration did not establish state: %+v", state)
	}
}

func TestRegisterTasksBeforeRegistrationRejected(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	err := s.RegisterTasks(state, &TaskRegistration{WorkerId: "w", Generation: 1})
	pe, ok := err.(*ProtocolError)
	if !ok || pe.Code != NotRegistered {
		t.Fatalf("got %v, want not_registered", err)
	}
}

func TestCapabilityFingerprintIsDerivedFromRegistration(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	tasks := []*TaskCapability{{TaskName: "a", TaskVersion: "1", Invocation: "value", Codecs: []string{"msgpack"}}}
	if err := s.RegisterWorker(state, workerRegistration("w", "nodejs", []string{"msgpack"}, 1, tasks)); err != nil {
		t.Fatal(err)
	}

	same := &TaskRegistration{WorkerId: "w", Generation: 1, Tasks: tasks}
	if err := s.RegisterTasks(state, same); err != nil {
		t.Fatalf("identical registration should be idempotent: %v", err)
	}

	changed := &TaskRegistration{WorkerId: "w", Generation: 1, Tasks: []*TaskCapability{
		{TaskName: "b", TaskVersion: "1", Invocation: "value", Codecs: []string{"msgpack"}},
	}}
	if err := s.RegisterTasks(state, changed); err == nil {
		t.Fatal("changed registration with same generation must conflict")
	}
}

func TestPythonOnlyCapabilityRejectedForOtherRuntimes(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	registration := workerRegistration("w", "go", []string{"cloudpickle"}, 1,
		[]*TaskCapability{{TaskName: "a", TaskVersion: "1", Invocation: "value", Codecs: []string{"cloudpickle"}}})

	err := s.RegisterWorker(state, registration)
	pe, ok := err.(*ProtocolError)
	if !ok || pe.Code != TaskConflict {
		t.Fatalf("got %v, want task_conflict", err)
	}
}

func TestStaleGenerationRejected(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	if err := s.RegisterWorker(state, workerRegistration("w", "python", []string{"msgpack"}, 5, nil)); err != nil {
		t.Fatal(err)
	}
	err := s.RegisterTasks(state, &TaskRegistration{WorkerId: "w", Generation: 2})
	pe, ok := err.(*ProtocolError)
	if !ok || pe.Code != TaskConflict {
		t.Fatalf("got %v, want task_conflict", err)
	}
}

func TestWorkerIDMustMatchRegistration(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	if err := s.RegisterWorker(state, workerRegistration("w", "python", []string{"msgpack"}, 1, nil)); err != nil {
		t.Fatal(err)
	}
	err := s.RegisterTasks(state, &TaskRegistration{WorkerId: "other", Generation: 2})
	pe, ok := err.(*ProtocolError)
	if !ok || pe.Code != OwnerMismatch {
		t.Fatalf("got %v, want owner_mismatch", err)
	}
}

func TestOwnerMismatchRejected(t *testing.T) {
	s := Session{}
	caller := CallerIdentity{Role: RoleRuntime, OwnerID: bytes.Repeat([]byte{1}, 16)}
	if err := s.CheckOwner(caller, caller.OwnerID); err != nil {
		t.Fatalf("matching owner rejected: %v", err)
	}
	err := s.CheckOwner(caller, bytes.Repeat([]byte{2}, 16))
	pe, ok := err.(*ProtocolError)
	if !ok || pe.Code != OwnerMismatch {
		t.Fatalf("got %v, want owner_mismatch", err)
	}
}

func TestOwnerRegistryPromotesNewestStream(t *testing.T) {
	registry := NewOwnerRegistry()
	ownerID := bytes.Repeat([]byte{7}, 16)
	first, second := "stream-1", "stream-2"

	if previous := registry.Promote(ownerID, first); previous != nil {
		t.Fatalf("unexpected previous primary: %v", previous)
	}
	previous := registry.Promote(ownerID, second)
	if previous != first {
		t.Fatalf("got previous %v, want %v", previous, first)
	}
	if registry.IsPrimary(ownerID, first) {
		t.Fatal("replaced stream is still primary")
	}
	if !registry.IsPrimary(ownerID, second) {
		t.Fatal("newest stream is not primary")
	}

	registry.Remove(ownerID, second)
	if registry.IsPrimary(ownerID, second) {
		t.Fatal("removed stream is still primary")
	}
}

// --- Role authorization (gRPC metadata) --------------------------------

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
	_, err := authorize(context.Background(), "/taskwire.v1.TaskwireControl/Status")
	if err == nil {
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
	if !ok {
		t.Fatal("caller identity not attached to context")
	}
	if caller.Role != RoleRuntime || len(caller.OwnerID) != 16 {
		t.Fatalf("unexpected caller identity: %+v", caller)
	}
}
