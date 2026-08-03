package protocol

import (
	"bytes"
	"testing"

	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"
)

func TestProtobufRoundTrip(t *testing.T) {
	original := &WorkerRegistration{
		WorkerId: "worker-1", Runtime: "go", RuntimeVersion: "1.26",
		SdkVersion: "0.1.0", Codecs: []string{"msgpack", "bytes"},
		Tasks: &TaskRegistration{WorkerId: "worker-1", Generation: 1},
	}
	encoded, err := proto.Marshal(original)
	if err != nil {
		t.Fatal(err)
	}
	decoded := &WorkerRegistration{}
	if err := proto.Unmarshal(encoded, decoded); err != nil {
		t.Fatal(err)
	}
	if !proto.Equal(decoded, original) {
		t.Fatalf("round trip differs: %v", decoded)
	}
	if err := Validate(decoded); err != nil {
		t.Fatalf("valid registration rejected: %v", err)
	}
}

func TestProtobufUnknownFieldsAreForwardCompatible(t *testing.T) {
	unknown := protowire.AppendVarint(protowire.AppendTag(nil, 100, protowire.VarintType), 42)
	statusBytes, err := proto.Marshal(&StatusRequest{})
	if err != nil {
		t.Fatal(err)
	}
	status := &StatusRequest{}
	if err := proto.Unmarshal(append(statusBytes, unknown...), status); err != nil {
		t.Fatal(err)
	}
	reencoded, err := proto.Marshal(status)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(reencoded, unknown) {
		t.Fatal("unknown protobuf field was not preserved")
	}
}

func TestRequiredSemanticValidation(t *testing.T) {
	value := &ValueRef{Location: &pb.ValueRef_Inline{Inline: []byte("value")}}
	if err := Validate(value); err == nil {
		t.Fatal("inline ValueRef without codec accepted")
	}

	query := &TaskQuery{OwnerId: []byte("short")}
	if err := Validate(query); err == nil {
		t.Fatal("short owner_id accepted")
	}
}

func TestSemanticValidationRecursesIntoNestedMessages(t *testing.T) {
	ownerID := bytes.Repeat([]byte{1}, 16)
	bad := &TaskEnvelope{
		OwnerId: ownerID, TaskName: "task", TaskVersion: "1", Invocation: "value",
		Input: &ValueRef{Location: &pb.ValueRef_Object{Object: &ObjectRef{
			Store: "s", Key: "k", Codec: "bytes", Sha256: []byte("short"),
		}}},
	}
	if err := Validate(bad); err == nil {
		t.Fatal("invalid nested ObjectRef accepted")
	}

	completion := &Completion{LeaseId: nil, Outcome: &pb.Completion_Result{
		Result: &ObjectRef{Store: "s", Key: "k", Codec: "bytes", Sha256: bytes.Repeat([]byte{0}, 32)},
	}}
	if err := Validate(completion); err == nil {
		t.Fatal("missing lease_id accepted")
	}
}

func TestWorkerRegistrationFieldsValidated(t *testing.T) {
	duplicateCodecs := &WorkerRegistration{
		WorkerId: "w", Runtime: "go", RuntimeVersion: "1", SdkVersion: "1",
		Codecs: []string{"msgpack", "msgpack"},
		Tasks:  &TaskRegistration{WorkerId: "w", Generation: 1},
	}
	if err := Validate(duplicateCodecs); err == nil {
		t.Fatal("duplicate worker codecs accepted")
	}

	badRuntime := &WorkerRegistration{
		WorkerId: "w", Runtime: "ruby", RuntimeVersion: "1", SdkVersion: "1",
		Codecs: []string{"msgpack"},
		Tasks:  &TaskRegistration{WorkerId: "w", Generation: 1},
	}
	if err := Validate(badRuntime); err == nil {
		t.Fatal("unsupported worker runtime accepted")
	}
}

func TestValidationErrorsCarryRegisteredCodes(t *testing.T) {
	err := Validate(&TaskEnvelope{})
	pe, ok := err.(*ProtocolError)
	if !ok {
		t.Fatalf("expected *ProtocolError, got %T %v", err, err)
	}
	if pe.Code != InvalidMessage {
		t.Fatalf("expected invalid_message, got %q", pe.Code)
	}
	if _, registered := ErrorRetryable[pe.Code]; !registered {
		t.Fatalf("unregistered error code: %q", pe.Code)
	}
}

func TestResultNotificationRequiresMatchingOutcome(t *testing.T) {
	ownerID := bytes.Repeat([]byte{1}, 16)
	taskID := bytes.Repeat([]byte{2}, 16)

	mismatched := &ResultNotification{
		OwnerId: ownerID, TaskId: taskID, Cursor: 1, State: "succeeded",
		Outcome: &pb.ResultNotification_Failure{Failure: &Failure{Code: "task_exception", Message: "boom"}},
	}
	if err := Validate(mismatched); err == nil {
		t.Fatal("succeeded state with failure outcome accepted")
	}

	valid := &ResultNotification{
		OwnerId: ownerID, TaskId: taskID, Cursor: 1, State: "succeeded",
		Outcome: &pb.ResultNotification_Result{Result: &ObjectRef{
			Store: "s", Key: "k", Codec: "bytes", Sha256: bytes.Repeat([]byte{0}, 32),
		}},
	}
	if err := Validate(valid); err != nil {
		t.Fatalf("valid notification rejected: %v", err)
	}
}
