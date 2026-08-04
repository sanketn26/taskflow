package protocol

import (
	"bytes"
	"testing"

	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"
)

func TestProtobufRoundTrip(t *testing.T) {
	original := &pb.WorkerRegistration{
		WorkerId: "worker-1", Runtime: "go", RuntimeVersion: "1.26",
		SdkVersion: "0.1.0", Codecs: []string{"msgpack", "bytes"},
		Tasks: &pb.TaskRegistration{WorkerId: "worker-1", Generation: 1},
	}
	encoded, err := proto.Marshal(original)
	if err != nil {
		t.Fatal(err)
	}
	decoded := &pb.WorkerRegistration{}
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

func TestWorkIsTheOnlyWorkerRegistrationRPC(t *testing.T) {
	service := pb.File_taskwire_v1_control_proto.Services().ByName("TaskwireControl")
	if service.Methods().ByName("RegisterTasks") != nil {
		t.Fatal("obsolete RegisterTasks RPC remains in generated descriptor")
	}
	field := (&pb.WorkerMessage{}).ProtoReflect().Descriptor().Fields().ByName("update_tasks")
	if field == nil {
		t.Fatal("Work message has no update_tasks capability snapshot")
	}
}

func TestProtobufUnknownFieldsAreForwardCompatible(t *testing.T) {
	unknown := protowire.AppendVarint(protowire.AppendTag(nil, 100, protowire.VarintType), 42)
	statusBytes, err := proto.Marshal(&pb.StatusRequest{})
	if err != nil {
		t.Fatal(err)
	}
	status := &pb.StatusRequest{}
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
	value := &pb.ValueRef{Location: &pb.ValueRef_Inline{Inline: []byte("value")}}
	if err := Validate(value); err == nil {
		t.Fatal("inline ValueRef without codec accepted")
	}

	query := &pb.TaskQuery{OwnerId: []byte("short")}
	if err := Validate(query); err == nil {
		t.Fatal("short owner_id accepted")
	}
}

func TestSemanticValidationRecursesIntoNestedMessages(t *testing.T) {
	ownerID := bytes.Repeat([]byte{1}, 16)
	bad := &pb.TaskEnvelope{
		OwnerId: ownerID, TaskName: "task", TaskVersion: "1", Invocation: "value",
		Input: &pb.ValueRef{Location: &pb.ValueRef_Object{Object: &pb.ObjectRef{
			Store: "s", Key: "k", Codec: "bytes", Sha256: []byte("short"),
		}}},
	}
	if err := Validate(bad); err == nil {
		t.Fatal("invalid nested ObjectRef accepted")
	}

	completion := &pb.Completion{LeaseId: nil, Outcome: &pb.Completion_Result{
		Result: &pb.ObjectRef{Store: "s", Key: "k", Codec: "bytes", Sha256: bytes.Repeat([]byte{0}, 32)},
	}}
	if err := Validate(completion); err == nil {
		t.Fatal("missing lease_id accepted")
	}
}

func TestWorkerRegistrationFieldsValidated(t *testing.T) {
	duplicateCodecs := &pb.WorkerRegistration{
		WorkerId: "w", Runtime: "go", RuntimeVersion: "1", SdkVersion: "1",
		Codecs: []string{"msgpack", "msgpack"},
		Tasks:  &pb.TaskRegistration{WorkerId: "w", Generation: 1},
	}
	if err := Validate(duplicateCodecs); err == nil {
		t.Fatal("duplicate worker codecs accepted")
	}

	badRuntime := &pb.WorkerRegistration{
		WorkerId: "w", Runtime: "ruby", RuntimeVersion: "1", SdkVersion: "1",
		Codecs: []string{"msgpack"},
		Tasks:  &pb.TaskRegistration{WorkerId: "w", Generation: 1},
	}
	if err := Validate(badRuntime); err == nil {
		t.Fatal("unsupported worker runtime accepted")
	}
}

func TestValidationErrorsCarryRegisteredCodes(t *testing.T) {
	err := Validate(&pb.TaskEnvelope{})
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

	mismatched := &pb.ResultNotification{
		OwnerId: ownerID, TaskId: taskID, Cursor: 1, State: "succeeded",
		Outcome: &pb.ResultNotification_Failure{Failure: &pb.Failure{Code: "task_exception", Message: "boom"}},
	}
	if err := Validate(mismatched); err == nil {
		t.Fatal("succeeded state with failure outcome accepted")
	}

	valid := &pb.ResultNotification{
		OwnerId: ownerID, TaskId: taskID, Cursor: 1, State: "succeeded",
		Outcome: &pb.ResultNotification_Result{Result: &pb.ObjectRef{
			Store: "s", Key: "k", Codec: "bytes", Sha256: bytes.Repeat([]byte{0}, 32),
		}},
	}
	if err := Validate(valid); err != nil {
		t.Fatalf("valid notification rejected: %v", err)
	}
}
