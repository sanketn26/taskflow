package protocol

import "testing"

var owner = []byte{1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}
var otherOwner = []byte{2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2}

func TestHelloFirstRequired(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	err := s.Authorize(state, MessageSubmit)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != NotRegistered {
		t.Fatalf("got %v, want not_registered", err)
	}
	if err := s.Authorize(state, MessageHello); err != nil {
		t.Fatalf("HELLO should always be allowed: %v", err)
	}
}

func TestRoleMatrix(t *testing.T) {
	cases := []struct {
		role      string
		allowed   MessageType
		forbidden MessageType
	}{
		{"runtime", MessageSubmit, MessagePull},
		{"worker", MessageRegisterTasks, MessageSubmit},
		{"admin", MessageStatus, MessageSubmit},
	}
	for _, c := range cases {
		s := Session{}
		state := NewConnectionState()
		var ownerID []byte
		var workerID string
		if c.role == "runtime" {
			ownerID = owner
		}
		if c.role == "worker" {
			workerID = "w"
		}
		s.Register(state, c.role, ownerID, workerID)

		if err := s.Authorize(state, c.allowed); err != nil {
			t.Errorf("role=%s: unexpected error for allowed type: %v", c.role, err)
		}
		err := s.Authorize(state, c.forbidden)
		de, ok := err.(*DecodeError)
		if !ok || de.Code != RoleForbidden {
			t.Errorf("role=%s: got %v, want role_forbidden", c.role, err)
		}
	}
}

func TestDuplicateInFlightRequestRejected(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	if err := s.BeginRequest(state, 5); err != nil {
		t.Fatal(err)
	}
	err := s.BeginRequest(state, 5)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != DuplicateRequest {
		t.Fatalf("got %v, want duplicate_request", err)
	}
}

func TestCompletionFreesRequestID(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	_ = s.BeginRequest(state, 5)
	s.CompleteRequest(state, 5)
	if err := s.BeginRequest(state, 5); err != nil {
		t.Fatalf("unexpected error after completion freed the id: %v", err)
	}
}

func TestZeroRequestIDNeverTracked(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	_ = s.BeginRequest(state, 0)
	if err := s.BeginRequest(state, 0); err != nil {
		t.Fatalf("request id 0 must never be tracked: %v", err)
	}
}

func TestReconnectResetsRequestNamespace(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	_ = s.BeginRequest(state, 5)
	state.ResetRequestNamespace()
	if err := s.BeginRequest(state, 5); err != nil {
		t.Fatalf("unexpected error after reset: %v", err)
	}
}

func TestOwnerMismatchRejected(t *testing.T) {
	s := Session{}
	state := NewConnectionState()
	s.Register(state, "runtime", owner, "")
	err := s.CheckOwner(state, otherOwner)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != OwnerMismatch {
		t.Fatalf("got %v, want owner_mismatch", err)
	}
	if err := s.CheckOwner(state, owner); err != nil {
		t.Fatalf("unexpected error for matching owner: %v", err)
	}
}

func TestOwnerRegistryPromotesNewestConnection(t *testing.T) {
	r := NewOwnerRegistry()
	if prev := r.Promote(owner, "conn-1"); prev != nil {
		t.Fatalf("expected nil previous, got %v", prev)
	}
	if !r.IsPrimary(owner, "conn-1") {
		t.Fatal("conn-1 should be primary")
	}
	prev := r.Promote(owner, "conn-2")
	if prev != "conn-1" {
		t.Fatalf("got %v, want conn-1", prev)
	}
	if r.IsPrimary(owner, "conn-1") {
		t.Fatal("conn-1 should no longer be primary")
	}
}
