package protocol

import (
	"errors"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestEveryRegisteredCodeMapsToAGRPCCode(t *testing.T) {
	for code := range ErrorRetryable {
		if GRPCCode(code) == codes.Unknown {
			t.Errorf("error code %q has no gRPC status mapping", code)
		}
	}
}

func TestStatusErrorCarriesTaskwireDetail(t *testing.T) {
	err := StatusError(StaleLease, "lease was fenced")

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("not a gRPC status error: %v", err)
	}
	if st.Code() != codes.Aborted {
		t.Fatalf("got status code %v, want %v", st.Code(), codes.Aborted)
	}

	detail := ErrorFromStatus(err)
	if detail == nil {
		t.Fatal("status carries no Taskwire error detail")
	}
	if detail.Code != StaleLease {
		t.Fatalf("got detail code %q, want %q", detail.Code, StaleLease)
	}
	if detail.Retryable != ErrorRetryable[StaleLease] {
		t.Fatalf("detail retryability disagrees with the registry")
	}
}

func TestRetryableCodePreservedThroughStatus(t *testing.T) {
	detail := ErrorFromStatus(StatusError(StorageUnavailable, "backend down"))
	if detail == nil {
		t.Fatal("status carries no Taskwire error detail")
	}
	if !detail.Retryable {
		t.Fatal("storage_unavailable must survive as retryable")
	}
}

func TestStatusFromErrorConvertsProtocolError(t *testing.T) {
	converted := StatusFromError(NewProtocolError(InvalidMessage, "bad field"))
	detail := ErrorFromStatus(converted)
	if detail == nil || detail.Code != InvalidMessage {
		t.Fatalf("ProtocolError did not convert cleanly: %v", converted)
	}

	if StatusFromError(nil) != nil {
		t.Fatal("nil error must convert to nil")
	}

	plain := StatusFromError(errors.New("boom"))
	if detail := ErrorFromStatus(plain); detail == nil || detail.Code != Internal {
		t.Fatalf("plain error should map to internal: %v", plain)
	}
}
